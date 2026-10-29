"""Owner-selected group/member reply policy; processing uses the common workflow."""
import json
import re
import time
import urllib.request

ID=re.compile(r'\d{1,26}')


def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS group_reply_policy(
        group_id TEXT PRIMARY KEY,name TEXT NOT NULL,enabled INTEGER NOT NULL,
        enabled_since REAL NOT NULL,revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS group_reply_members(
        group_id TEXT NOT NULL,uid TEXT NOT NULL,name TEXT NOT NULL,enabled_since REAL NOT NULL,
        PRIMARY KEY(group_id,uid));
        CREATE TABLE IF NOT EXISTS group_send_attempts(outbox_id TEXT PRIMARY KEY,started REAL NOT NULL);''')
    # Retire the replaced feature without deleting its historical records.
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'delegated_contacts' in tables:db.execute('UPDATE delegated_contacts SET enabled=0 WHERE enabled<>0')
    if 'delegated_drafts' in tables:
        db.execute("UPDATE delegated_drafts SET status='cancelled' WHERE status IN ('awaiting_approval','queued')")
    if 'reply_outbox' in tables:db.execute("UPDATE reply_outbox SET status='cancelled' WHERE status='pending' AND id LIKE 'delegate:%'")
    if 'live_tasks' in tables:
        db.execute("UPDATE live_tasks SET status='cancelled' WHERE status IN ('pending','resource_saved','media_pending') AND json_extract(raw_json,'$.origin') IN ('delegate_inbound','delegate_approved')")
    db.commit()


def sdk_read(group_id=None):
    from app.sdk_sender import BASE_URL
    path='/groups'
    if group_id is not None:
        if not isinstance(group_id,str) or not ID.fullmatch(group_id):raise ValueError('无效群ID')
        path+='?group_id='+group_id
    with urllib.request.urlopen(BASE_URL+path,timeout=10) as response:value=json.load(response)
    if value.get('ok') is not True:raise ValueError('SDK群聊资料未就绪，请确认新版SDK在线')
    return value


def allowed(db,group_id,uid,received,revision=None):
    if not isinstance(group_id,str) or not isinstance(uid,str) or not ID.fullmatch(group_id) or not ID.fullmatch(uid):return None
    row=db.execute('''SELECT g.name,g.revision,m.name FROM group_reply_policy g
        JOIN group_reply_members m ON m.group_id=g.group_id
        WHERE g.group_id=? AND m.uid=? AND g.enabled=1 AND g.enabled_since<=? AND m.enabled_since<=?''',
        (group_id,uid,received,received)).fetchone()
    if not row or (revision is not None and row[1]!=revision):return None
    return {'group_id':group_id,'group_name':row[0],'revision':row[1],'sender_uid':uid,'sender_name':row[2]}


def task_allowed(db,raw):
    from app.sdk_receiver import ACCOUNT_UID
    return raw.get('sender_uid')!=ACCOUNT_UID and allowed(db,raw.get('group_id'),raw.get('sender_uid'),
        raw.get('association_received_at',0),raw.get('group_policy_revision'))


def state(db):
    schema(db)
    groups=[]
    for gid,name,enabled,since,revision in db.execute('SELECT * FROM group_reply_policy ORDER BY name'):
        groups.append({'group_id':gid,'name':name,'enabled':bool(enabled),'revision':revision,
            'members':[{'uid':r[0],'name':r[1]} for r in db.execute('SELECT uid,name FROM group_reply_members WHERE group_id=? ORDER BY name',(gid,))]})
    return {'groups':groups,'automatic_reply':True,'same_agent_and_session':True}


def save(db,body):
    from app.sdk_receiver import ACCOUNT_UID
    gid=body.get('group_id');selected=body.get('members');enabled=body.get('enabled');revision=body.get('revision',0)
    if not isinstance(gid,str) or not ID.fullmatch(gid):raise ValueError('请先选择真实群聊')
    if type(enabled) is not bool or type(revision) is not int:raise ValueError('群开关或版本无效')
    if not isinstance(selected,list) or any(not isinstance(x,str) or not ID.fullmatch(x) or x==ACCOUNT_UID for x in selected):raise ValueError('请勾选有效成员，不能选择小号自己')
    selected=list(dict.fromkeys(selected))
    if enabled and not selected:raise ValueError('启用群回复前至少选择一位成员')
    remote=sdk_read(gid);group=remote.get('group') or {};members={str(m['uid']):m for m in remote.get('members',[])}
    if group.get('group_id')!=gid or group.get('is_participant') is False:raise ValueError('小号已不在这个群中')
    if any(uid not in members for uid in selected):raise ValueError('成员列表已变化，请重新读取后勾选')
    schema(db)
    with db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT enabled,enabled_since,revision FROM group_reply_policy WHERE group_id=?',(gid,)).fetchone()
        if (old[2] if old else 0)!=revision:raise ValueError('群设置已被更新，请刷新后再保存')
        previous=dict(db.execute('SELECT uid,enabled_since FROM group_reply_members WHERE group_id=?',(gid,)))
        now=time.time();since=old[1] if old and old[0] and enabled else now
        db.execute('INSERT OR REPLACE INTO group_reply_policy VALUES(?,?,?,?,?)',(gid,str(group.get('name') or gid)[:100],int(enabled),since,revision+1))
        db.execute('DELETE FROM group_reply_members WHERE group_id=?',(gid,))
        for uid in selected:
            keep_since=previous.get(uid,now) if old and old[0] and enabled else now
            db.execute('INSERT INTO group_reply_members VALUES(?,?,?,?)',(gid,uid,str(members[uid].get('name') or uid)[:100],keep_since))
        # Settings changes cancel pending replies, never rearm an attempted send.
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='reply_outbox'").fetchone():
            db.execute("UPDATE reply_outbox SET status='cancelled' WHERE status='pending' AND task_id IN (SELECT id FROM live_tasks WHERE json_extract(raw_json,'$.group_id')=?)",(gid,))
    return {'ok':True,'group_id':gid,'revision':revision+1}


def action(db,body):
    op=body.get('action')
    if op=='list':return sdk_read()
    if op=='members':return sdk_read(body.get('group_id'))
    if op=='save':return save(db,body)
    if op=='pause':
        schema(db)
        with db:
            changed=db.execute('UPDATE group_reply_policy SET enabled=0,revision=revision+1 WHERE group_id=? AND revision=?',(body.get('group_id'),body.get('revision'))).rowcount
            if not changed:raise ValueError('群设置已变化，请刷新后重试')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='reply_outbox'").fetchone():
                db.execute("UPDATE reply_outbox SET status='cancelled' WHERE status='pending' AND task_id IN (SELECT id FROM live_tasks WHERE json_extract(raw_json,'$.group_id')=?)",(body.get('group_id'),))
        return {'ok':True}
    raise ValueError('未知群聊设置操作')
