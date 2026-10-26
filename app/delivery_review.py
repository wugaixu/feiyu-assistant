"""Owner observations only. Never changes outbox status or schedules a send."""
import sqlite3
import time
from pathlib import Path

FIELDS={'reply':{'receipt'},'comment':{'visibility','mention'}}
VALUES={'confirmed','missing','deferred','unknown'}


def automatic(items,kind='reply'):
    """Estimate channel state from newest evidence, not historical alert count."""
    if not items:return {'state':'idle','detail':'通道待机，等待新的发送记录','metric':'自动检查已启用'}
    item=items[0];status=item.get('status');reviews=item.get('review',{})
    field='receipt' if kind=='reply' else 'visibility'
    observation=reviews.get(field,{}).get('value')
    if observation=='confirmed':
        if kind=='comment':
            notification=reviews.get('mention',{}).get('value')
            detail='评论可见已有反馈；'+('提及通知已有收到反馈' if notification=='confirmed' else '提及通知未直接确认')
            return {'state':'idle','detail':detail,'metric':'沿用已有反馈'}
        return {'state':'idle','detail':'最近记录有主人已收到的反馈，自动沿用此依据','metric':'已有接收反馈'}
    if observation=='missing':
        return {'state':'note','detail':'最近记录曾反馈未收到；保留历史，不重复要求确认，也不自动重发','metric':'通道状态结合最新回执判断'}
    if kind=='comment' and item.get('error_class'):
        phases={'identity':'账号核对','open_work':'打开作品','open_comment_editor':'打开评论编辑器','type_comment':'输入评论','owner_suggestions':'取得主人提及候选','select_owner':'选择主人提及','click_publish':'点击发布','publish_receipt':'等待发布回执'}
        phase=phases.get((item.get('diagnostic') or {}).get('phase'),'发布流程')
        return {'state':'warn','detail':phase+'发生'+('超时' if item['error_class']=='TimeoutError' else '异常')+'，未确认发布成功；不会自动重发','metric':'程序自动检测到异常'}
    if status in ('platform_not_accepted','platform_rejected','blocked_before_publish','failed','quota_blocked'):
        return {'state':'warn','detail':'最近一次提交明确失败或受阻，请查看系统记录','metric':'自动检查发现异常'}
    if status in ('server_accepted','visible_in_account_history','owner_confirmed_published','accepted_unverified'):
        if kind=='reply' and item.get('channel')=='whale':
            return {'state':'idle','detail':'已写入原设备的鲸聊历史，等待客户端读取；尚未确认手机实际收到','metric':'本地历史已保存'}
        return {'state':'idle','detail':'最近提交回执正常，自动检查未发现新的发送故障；不等于接收端确认',
            'metric':'已回读' if status=='visible_in_account_history' else '平台已接受'}
    if kind=='reply':
        samples=[x.get('review',{}).get('receipt',{}).get('value') for x in items
            if x.get('status')==status and x.get('check_code')==item.get('check_code')
            and x.get('kind','text')==item.get('kind','text') and x.get('channel')==item.get('channel')]
        yes=samples.count('confirmed');no=samples.count('missing')
        if yes and not no:
            return {'state':'idle','detail':'同类回执已有主人收到的反馈，据此推测通道可用；本条未直接确认','metric':f'参考 {yes} 条历史反馈'}
        if yes or no:
            return {'state':'note','detail':f'同类回执历史反馈：收到 {yes} 条、未收到 {no} 条；自动保留不确定性，不要求逐条确认','metric':'已结合历史反馈检查'}
    return {'state':'note','detail':'自动检查已完成，平台证据不足以确认接收；保留记录，不要求人工核验','metric':'结果不明，不自动重发'}


def latest(path):
    if not Path(path).is_file():return {}
    db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2)
    try:
        result={}
        for kind,record_id,field,value,stamp in db.execute('SELECT kind,record_id,field,value,created FROM reviews ORDER BY id'):
            result.setdefault((kind,record_id),{})[field]={'value':value,'at':stamp}
        return result
    finally:db.close()


def save(path,business_db,kind,record_id,field,value):
    if not all(isinstance(x,str) for x in (kind,field,value)) or kind not in FIELDS or field not in FIELDS[kind] or value not in VALUES:
        raise ValueError('无效的核验选项')
    if not isinstance(record_id,str) or not 1<=len(record_id)<=100:raise ValueError('无效记录编号')
    table,key=('reply_outbox','id') if kind=='reply' else ('shared_comment_outbox','video_id')
    db=sqlite3.connect('file:'+str(business_db)+'?mode=ro',uri=True,timeout=2)
    try:
        row=db.execute(f'SELECT status FROM {table} WHERE {key}=?',(record_id,)).fetchone()
        if not row:raise ValueError('记录不存在')
        if row[0]=='pending':raise ValueError('尚未发送的记录不能核验')
    finally:db.close()
    # Dedicated append-only audit DB; business receipts and quotas remain untouched.
    db=sqlite3.connect(path,timeout=3)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,record_id TEXT NOT NULL,field TEXT NOT NULL,value TEXT NOT NULL,created REAL NOT NULL)')
            db.execute('INSERT INTO reviews(kind,record_id,field,value,created) VALUES(?,?,?,?,?)',(kind,record_id,field,value,time.time()))
    finally:db.close()
    return {'ok':True,'review':latest(path).get((kind,record_id),{})}
