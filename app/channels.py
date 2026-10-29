"""Immutable return addresses for private turns; a separate default for proactive work."""
import json
import time


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS channel_current(id INTEGER PRIMARY KEY CHECK(id=1),channel TEXT NOT NULL,device TEXT,updated REAL NOT NULL)')
    db.commit()


def observe(db,channel,device=None):
    if channel not in ('douyin','whale','qq'):raise ValueError('Unknown message channel')
    if channel=='whale' and not device:raise ValueError('Whale device required')
    db.execute('INSERT INTO channel_current VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET channel=excluded.channel,device=excluded.device,updated=excluded.updated',(channel,device,time.time()))


def current(db):
    if not db.in_transaction:schema(db)
    row=db.execute('SELECT channel,device FROM channel_current WHERE id=1').fetchone()
    return {'channel':row[0],'device':row[1]} if row else {'channel':'douyin','device':None}


def address(db,task_id):
    row=db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(task_id,)).fetchone()
    raw=json.loads(row[0]) if row else {}
    if raw.get('origin') in ('delegate_inbound','delegate_approved'):raise ValueError('Retired private delegation task')
    if raw.get('origin')=='group_chat':
        import re
        gid=raw.get('group_id')
        if not isinstance(gid,str) or not re.fullmatch(r'\d{1,26}',gid) or gid!=raw.get('sdk_thread_id'):raise ValueError('Missing verified group address')
        return {'channel':'douyin','device':None,'group_id':gid}
    # Only receiver-written fields route messages. Model text is never inspected.
    if raw.get('transport')=='qq' or raw.get('return_channel')=='qq':
        import re
        device=raw.get('reply_device')
        if not isinstance(device,str) or not re.fullmatch(r'qq:[1-9][0-9]{4,14}:[1-9][0-9]{4,14}',device):raise ValueError('Missing verified QQ address')
        return {'channel':'qq','device':device}
    if raw.get('transport')=='whale' or raw.get('return_channel')=='whale':
        device=raw.get('reply_device')
        if not isinstance(device,str) or len(device)!=64:raise ValueError('Missing verified return address')
        return {'channel':'whale','device':device}
    if raw.get('direction')=='incoming':return {'channel':'douyin','device':None}
    if raw.get('return_channel')=='douyin':return {'channel':'douyin','device':None}
    return current(db) if raw.get('origin')=='local_schedule' else {'channel':'douyin','device':None}


def same_source(left,right):
    a,b=json.loads(left or '{}'),json.loads(right or '{}')
    return (a.get('transport','sdk'),a.get('reply_device'))==(b.get('transport','sdk'),b.get('reply_device'))
