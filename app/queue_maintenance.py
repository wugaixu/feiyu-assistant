"""Explicit owner maintenance. Never requeue, replay or fabricate delivery success."""
import json
import re
import time

ACTIVE_TASKS=('pending','processing','media_pending')
OBSERVATIONS={'received':'主人确认已收到','missing':'主人确认未收到','dismiss':'保留未知，结束关注'}


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS queue_maintenance(
        id INTEGER PRIMARY KEY,kind TEXT NOT NULL,record_id TEXT NOT NULL,
        action TEXT NOT NULL,before_status TEXT NOT NULL,after_status TEXT NOT NULL,
        note TEXT NOT NULL,created REAL NOT NULL)''')


def resolutions(db):
    if 'queue_maintenance' not in tables(db):return {}
    result={}
    for kind,key,action,before,after,note,stamp in db.execute(
            'SELECT kind,record_id,action,before_status,after_status,note,created FROM queue_maintenance ORDER BY id'):
        result[(kind,key)]={'action':action,'status':after,'note':note,'at':stamp}
    return result


def resolved(item,record):
    return bool(record and record.get('action') in OBSERVATIONS and record.get('status')==item)


def state(db,limit=100):
    known=tables(db);reviews=resolutions(db);tasks=[];sends=[]
    if 'live_tasks' in known:
        for key,status,received,raw in db.execute(
                "SELECT id,status,received,raw_json FROM live_tasks WHERE status IN ('pending','processing','media_pending') ORDER BY received LIMIT ?",(limit,)):
            try:message=json.loads(raw)
            except (TypeError,ValueError):message={}
            tasks.append({'id':key,'status':status,'received':received,
                          'text':str(message.get('raw_text') or '附件消息')[:180]})
    if 'reply_outbox' in known:
        # Filter acknowledged history before limiting, so old blocked rows stay reachable.
        for key,task,status,kind,text in db.execute("SELECT id,task_id,status,kind,text FROM reply_outbox WHERE status NOT IN ('cancelled','superseded') ORDER BY rowid DESC"):
            review=reviews.get(('send',key))
            if resolved(status,review):continue
            if status in ('pending','attempted','outcome_uncertain','failed','platform_not_accepted','platform_rejected','server_accepted','visible_in_history','confirmed'):
                sends.append({'id':key,'task_id':task,'status':status,'kind':kind,'text':str(text or '')[:180]})
        sends.sort(key=lambda x:0 if x['status'] in ('pending','attempted','outcome_uncertain','failed','platform_not_accepted','platform_rejected') else 1)
    history=[]
    if 'queue_maintenance' in known:
        history=[{'kind':r[0],'id':r[1],'action':r[2],'before':r[3],'after':r[4],'note':r[5],'at':r[6]}
                 for r in db.execute('SELECT kind,record_id,action,before_status,after_status,note,created FROM queue_maintenance ORDER BY id DESC LIMIT 30')]
    return {'tasks':tasks,'sends':sends[:limit],'history':history}


def apply(db,kind,key,action,expected,note='',now=None):
    if kind not in ('task','send') or not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,160}',key):
        raise ValueError('无效维护记录')
    if not isinstance(note,str) or len(note)>500:raise ValueError('备注最多500字')
    if not isinstance(expected,str):raise ValueError('请先刷新记录状态')
    now=time.time() if now is None else now
    with db:
        db.execute('BEGIN IMMEDIATE')
        schema(db);known=tables(db)
        table='live_tasks' if kind=='task' else 'reply_outbox'
        row=db.execute(f'SELECT status FROM {table} WHERE id=?',(key,)).fetchone()
        if not row:raise ValueError('记录不存在')
        status=row[0]
        if status!=expected:raise ValueError('记录状态已经变化，请刷新后再操作')
        after=status
        if kind=='task' and action=='end':
            if status not in ACTIVE_TASKS:raise ValueError('任务已结束，无需重复维护')
            if 'reply_outbox' in known and db.execute("SELECT 1 FROM reply_outbox WHERE task_id=? AND status='attempted'",(key,)).fetchone():
                raise ValueError('该任务有正在发送的回复，请先等待回执或结束超时发送')
            after='failed'
            # The processing predicate in reply_batch.finish rejects late model results.
            previous=db.execute('SELECT result FROM live_tasks WHERE id=?',(key,)).fetchone()[0]
            detail={'manual_maintenance':True,'reason':'owner_ended_without_reply','at':now}
            if previous is not None:detail['previous_result']=previous
            db.execute('UPDATE live_tasks SET status=?,result=? WHERE id=?',
                       (after,json.dumps(detail,ensure_ascii=False),key))
            if 'chat_dispatch' in known:db.execute("UPDATE chat_dispatch SET status='cancelled' WHERE task_id=?",(key,))
            if 'video_rounds' in known:db.execute("UPDATE video_rounds SET status='failed' WHERE id=?",(key,))
            if 'reply_outbox' in known:db.execute("UPDATE reply_outbox SET status='cancelled' WHERE task_id=? AND status='pending'",(key,))
            if 'shared_comment_outbox' in known:db.execute("UPDATE shared_comment_outbox SET status='cancelled' WHERE task_id=? AND status IN ('waiting_for_reply','pending')",(key,))
        elif kind=='send' and action=='cancel':
            if status!='pending':raise ValueError('只能取消尚未发送的回复')
            after='cancelled'
            task=db.execute('SELECT task_id FROM reply_outbox WHERE id=?',(key,)).fetchone()[0]
            # Cancel the remaining batch, rather than leaving later parts blocked.
            siblings=db.execute("SELECT id FROM reply_outbox WHERE task_id=? AND status='pending'",(task,)).fetchall() if task else [(key,)]
            for sibling, in siblings:
                db.execute("UPDATE reply_outbox SET status='cancelled' WHERE id=? AND status='pending'",(sibling,))
                if sibling!=key:db.execute('INSERT INTO queue_maintenance(kind,record_id,action,before_status,after_status,note,created) VALUES(?,?,?,?,?,?,?)',('send',sibling,'cancel','pending',after,note,now))
        elif kind=='send' and action=='close_attempt':
            if status!='attempted' or 'outbound_attempts' not in known:raise ValueError('请等待发送回执，暂不能结束')
            attempt=db.execute('SELECT id,started,finished FROM outbound_attempts WHERE outbox_id=? ORDER BY started DESC LIMIT 1',(key,)).fetchone()
            if not attempt or attempt[2] is not None or now-attempt[1]<300:raise ValueError('发送尚未超时，请至少等待5分钟；缺少尝试记录时由自动恢复程序观察处理')
            after='outcome_uncertain'
            detail=json.dumps({'result':after,'reason':'owner_closed_stale_attempt','automatic_retry':False,'at':now})
            db.execute('UPDATE reply_outbox SET status=?,detail=? WHERE id=?',(after,detail,key))
            db.execute('UPDATE outbound_attempts SET finished=?,status=?,detail=? WHERE id=?',(now,after,detail,attempt[0]))
        elif kind=='send' and action in OBSERVATIONS:
            if status in ('pending','attempted','cancelled','superseded'):raise ValueError('请先等待发送结束或取消待发送回复')
            # Observation only: retain original receipt, status and quota history.
        else:raise ValueError('不支持的维护操作')
        db.execute('INSERT INTO queue_maintenance(kind,record_id,action,before_status,after_status,note,created) VALUES(?,?,?,?,?,?,?)',
                   (kind,key,action,status,after,note,now))
    return {'ok':True,'status':after,'resent':False}
