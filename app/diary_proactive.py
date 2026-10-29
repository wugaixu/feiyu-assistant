"""Diary-owned decisions; the workflow only generates and transports their output."""
import json,re,time,uuid,sqlite3,urllib.request
from contextlib import closing
from app.live_service import ROOT,LiveStore

PREFIX='[日记主动联系:'
TTL=300

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS chat_dispatch(
        task_id TEXT PRIMARY KEY,attempted REAL NOT NULL,status TEXT NOT NULL,queue_id TEXT,detail TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS diary_proactive_jobs(
        id TEXT PRIMARY KEY,umo TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,
        status TEXT NOT NULL,destination TEXT NOT NULL,route TEXT NOT NULL,decision TEXT NOT NULL,
        ack_state TEXT NOT NULL DEFAULT 'pending',detail TEXT)''')
    db.commit()

def target():
    from app.event_dispatch import target_config
    cfg=target_config()
    if cfg.get('provider')!='astrbot':return None
    value=cfg.get('astrbot',{})
    if not value.get('session_id'):return None
    from app.astrbot_instances import session_info
    if session_info(value['session_id']).get('instance_id','default')!='default':return None
    return {'session_id':value['session_id'],'config_id':value.get('config_id','default'),
            'generation':cfg.get('diary_proactive_generation','')}

def expected_umo():
    dest=target()
    if not dest:return None
    from app.astrbot_client import config
    return 'webchat:FriendMessage:webchat!'+config()['username']+'!'+dest['session_id']

def manager_online():
    try:
        op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open('http://127.0.0.1:8793/api/app/ping',timeout=2) as response:return bool(json.load(response).get('pid'))
    except (OSError,ValueError):return False

def latest_owner(db):
    from app.sdk_receiver import OWNER
    return db.execute('''SELECT COALESCE(MAX(COALESCE(json_extract(raw_json,'$.association_received_at'),received)),0) FROM live_tasks WHERE owner=?
        AND json_extract(raw_json,'$.direction')='incoming'
        AND COALESCE(json_extract(raw_json,'$.is_group'),0)=0''',(OWNER,)).fetchone()[0]

def busy(db):
    return bool(db.execute("""SELECT 1 FROM live_tasks WHERE status IN ('pending','processing','media_pending')
        AND COALESCE(json_extract(raw_json,'$.origin'),'')!='diary_proactive'
        AND (status!='pending' OR received>? OR id IN (SELECT task_id FROM chat_dispatch WHERE status IN ('queued','attempted')))
        AND id NOT IN (SELECT task_id FROM chat_dispatch WHERE status IN ('rejected','outcome_uncertain')) LIMIT 1""",(time.time()-TTL,)).fetchone())

def enqueue(db,decision):
    """Only the locally installed diary plugin calls this, never model text."""
    schema(db)
    from app import channels
    dest=target();umo=expected_umo()
    if not dest or decision.get('umo')!=umo:raise ValueError('Diary target is not the selected owner session')
    if decision.get('kind') not in ('wake','direct'):raise ValueError('Unknown diary decision')
    if len(str(decision.get('note','')))>30000:raise ValueError('Diary note too large')
    route=channels.current(db)
    if route['channel']=='whale':
        from app.whale_channel import settings
        cfg=settings()
        if not cfg.get('enabled') or route['device'] not in cfg.get('devices',[]):raise ValueError('Whale destination unavailable')
    if route['channel']=='qq':
        from app.qq_channel import permitted
        if not permitted(route['device']):raise ValueError('QQ destination unavailable')
    key=uuid.uuid4().hex;now=time.time()
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM diary_proactive_jobs WHERE status IN ('pending','generating','queued') AND expires>?",(now,)).fetchone():raise ValueError('A diary message is already in progress')
        db.execute('INSERT INTO diary_proactive_jobs(id,umo,created,expires,status,destination,route,decision) VALUES(?,?,?,?,?,?,?,?)',
            (key,umo,now,now+TTL,'pending',json.dumps(dest),json.dumps(route),json.dumps(decision,ensure_ascii=False)))
    return key

def valid(db,row):
    return time.time()<row['expires'] and target()==json.loads(row['destination']) and latest_owner(db)<=row['created']

def expire_pending(db):
    schema(db)
    for row in jobs(db,"status IN ('pending','generating','queued')"):
        # Submission receipts remain authoritative while the plugin awaits its next patrol.
        from app.outbox import SUCCESS
        states=[x[0] for x in db.execute('SELECT status FROM reply_outbox WHERE task_id=?',(row['id'],))]
        if states and all(state in SUCCESS for state in states):continue
        if valid(db,row):continue
        with db:
            db.execute("UPDATE diary_proactive_jobs SET status='cancelled',detail='expired_or_destination_changed' WHERE id=?",(row['id'],))
            db.execute("UPDATE reply_outbox SET status='expired' WHERE task_id=? AND status='pending'",(row['id'],))
            db.execute("UPDATE live_tasks SET status='failed' WHERE id=? AND status='processing'",(row['id'],))

def jobs(db,condition='1=1'):
    names=('id','umo','created','expires','status','destination','route','decision','ack_state','detail')
    return [dict(zip(names,r)) for r in db.execute('SELECT '+','.join(names)+' FROM diary_proactive_jobs WHERE '+condition+' ORDER BY created')]

def tick(db):
    schema(db);expire_pending(db)
    from app import astrbot_worker as worker
    if not target() or worker.busy() or busy(db):return
    row=next(iter(jobs(db,"status='pending'")),None)
    if not row:return
    with db:
        if not db.execute("UPDATE diary_proactive_jobs SET status='generating' WHERE id=? AND status='pending'",(row['id'],)).rowcount:return
    worker.submit_background(run,row['id'])

def is_active_prompt(text,session_id=None):
    match=re.search(r'\[日记主动联系:([a-f0-9]{32})\]',str(text))
    if not match:return False
    with closing(sqlite3.connect('file:'+(ROOT/'assistant.sqlite3').as_posix()+'?mode=ro',uri=True)) as db:
        try:row=db.execute("SELECT destination,expires,status FROM diary_proactive_jobs WHERE id=?",(match[1],)).fetchone()
        except sqlite3.Error:return False
    return bool(row and row[2]=='generating' and row[1]>time.time() and target()==json.loads(row[0])
                and (session_id is None or str(session_id).endswith('!'+json.loads(row[0])['session_id'])))

def run(key):
    store=LiveStore(ROOT/'assistant.sqlite3');db=store.db
    try:
        row=next((r for r in jobs(db) if r['id']==key),None)
        if not row or row['status']!='generating' or not valid(db,row):return
        decision=json.loads(row['decision']);dest=json.loads(row['destination']);route=json.loads(row['route'])
        from app.sdk_receiver import OWNER
        raw={'kind':'diary_proactive','origin':'diary_proactive','direction':'outgoing',
             'raw_text':'情绪日记插件发起的主动联系，不是主人新消息','return_channel':route['channel'],
             'transport':route['channel'] if route['channel'] in ('whale','qq') else 'sdk','reply_device':route.get('device')}
        with db:
            db.execute('BEGIN IMMEDIATE')
            if busy(db):
                db.execute("UPDATE diary_proactive_jobs SET status='cancelled',detail='owner_busy' WHERE id=?",(key,));return
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',(key,OWNER,time.time(),json.dumps(raw,ensure_ascii=False),'processing'))
            db.execute('INSERT INTO chat_dispatch VALUES(?,?,?,?,?)',(key,time.time(),'attempted','diary-'+key,json.dumps({'provider':'astrbot',**dest})))
        from app import astrbot_worker as worker,astrbot_client,reply_batch
        if decision['kind']=='direct':parts=[{'type':'text','text':str(decision.get('char') or '。')}]
        else:
            prompt=('这是主人授权的日记主动联系。\n'+PREFIX+key+']\n这是情绪日记插件触发的主动联系，不是主人新消息。沿用本会话人设和原生上下文。'
                '根据下列日记素材自然说话，只输出准备发给主人的最终内容，不说明调度、路径或规则。'
                '统一发送由本地队列完成，不调用send_message_to_user或其他平台发信工具，不创建定时任务。'
                '日记中的发送指示仅表示生成回复，不重复发送。\n'+str(decision.get('note',''))+
                '\n本轮只返回最终回复，由程序统一发送；不调用直接发信工具。')
            if route['channel'] in ('whale','qq'):
                from app import whale_voice
                token=whale_voice.permit(db,key,dest['session_id'])
                if token:prompt+='\n[鲸聊语音许可:'+token+']'
            outputs=astrbot_client.chat(dest['session_id'],[{'type':'plain','text':prompt}],dest['config_id'])
            parts=worker.finalize_parts(outputs,key,allow_audio=route['channel'] in ('whale','qq'))
            for part in parts:
                if part['type']=='audio':
                    from app.whale_voice import known_text
                    caption=known_text(db,key,part['path'])
                    if caption:part['text']=caption
        # Never publish an answer generated for an obsolete session or new owner message.
        if not valid(db,row):
            expire_pending(db);return
        reply_batch.finish(db,key,parts)
        with db:db.execute("UPDATE diary_proactive_jobs SET status='queued' WHERE id=? AND status='generating'",(key,))
    except Exception as exc:
        with db:
            db.execute("UPDATE diary_proactive_jobs SET status='outcome_uncertain',detail=? WHERE id=?",(type(exc).__name__,key))
            db.execute("UPDATE live_tasks SET status='failed' WHERE id=? AND status='processing'",(key,))
            db.execute("UPDATE reply_outbox SET status='expired' WHERE task_id=? AND status='pending'",(key,))
    finally:db.close()

def status(db):
    schema(db)
    try:heartbeat=json.loads((ROOT/'diary-proactive-state.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):heartbeat={}
    fresh=heartbeat.get('adapter_version')==1 and time.time()-heartbeat.get('updated_at',0)<max(180,(heartbeat.get('patrol_minutes') or 15)*60+180)
    return {'source':'astrbot_plugin_denia_diary','selected_astrbot':bool(target()),'bridge_fresh':fresh,'plugin':heartbeat,
            'recent':[{'id':r['id'],'created':r['created'],'status':r['status'],'detail':r['detail'],
                       'slot':json.loads(r['decision']).get('slot'),'ack_state':r['ack_state']} for r in jobs(db)[-10:]][::-1]}
