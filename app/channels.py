"""Immutable return addresses for private turns; a separate default for proactive work."""
import json
import time


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS channel_current(id INTEGER PRIMARY KEY CHECK(id=1),channel TEXT NOT NULL,device TEXT,updated REAL NOT NULL)')
    db.commit()


def observe(db,channel,device=None):
    if channel not in ('douyin','whale'):raise ValueError('Unknown message channel')
    if channel=='whale' and not device:raise ValueError('Whale device required')
    db.execute('INSERT INTO channel_current VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET channel=excluded.channel,device=excluded.device,updated=excluded.updated',(channel,device,time.time()))


def current(db):
    if not db.in_transaction:schema(db)
    row=db.execute('SELECT channel,device FROM channel_current WHERE id=1').fetchone()
    return {'channel':row[0],'device':row[1]} if row else {'channel':'douyin','device':None}


def address(db,task_id):
    row=db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(task_id,)).fetchone()
    raw=json.loads(row[0]) if row else {}
    # Only receiver-written fields route messages. Model text is never inspected.
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
