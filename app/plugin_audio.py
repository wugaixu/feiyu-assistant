"""Durable late audio from an installed plugin after its WebChat request ended.

No public tool or endpoint: only the installed music compatibility hook calls
this. Authorization and immutable return address come from the original task.
"""
import json
import sqlite3
import time
from pathlib import Path
from app import audio_sender,channels
from app.whale_voice import extract_token


def authorized(db,token):
    from app.sdk_receiver import OWNER
    row=db.execute('''SELECT t.id,t.status,t.raw_json,p.session_id,d.status,d.detail
        FROM whale_voice_permits p JOIN live_tasks t ON p.task_id=t.id
        JOIN chat_dispatch d ON d.task_id=t.id WHERE p.token=? AND p.expires>?
        AND t.owner=?''',(token,time.time(),OWNER)).fetchone()
    if not row:raise ValueError('Plugin audio permission expired or unavailable')
    task,status,raw,session,dispatch,detail=row
    raw=json.loads(raw);detail=json.loads(detail)
    if raw.get('transport')!='whale' or raw.get('direction')!='incoming' or detail.get('provider')!='astrbot' or detail.get('session_id')!=session:
        raise ValueError('Plugin audio origin mismatch')
    if status=='processing' and dispatch in ('attempted','queued'):return task,False
    if status!='done' or dispatch not in ('processed','attempted') or not db.execute('SELECT 1 FROM reply_outbox WHERE task_id=?',(task,)).fetchone():
        raise ValueError('Original reply is not committed')
    if channels.address(db,task)['channel']!='whale':raise ValueError('Whale audio only')
    return task,True


def enqueue(db,token,raw):
    task,late=authorized(db,token)
    if not late:return False  # An open request retains the ordinary Record path.
    from app.resource_storage import LOCK
    from app.sdk_receiver import OWNER
    with LOCK:
        path=audio_sender.prepare(raw,task)
        path,digest,_=audio_sender.validate_audio(path)
        key='plugin-audio:'+task+':'+digest
        with db:
            db.execute('BEGIN IMMEDIATE')
            authorized(db,token)
            old=db.execute("SELECT id FROM reply_outbox WHERE task_id=? AND id LIKE 'plugin-audio:%'",(task,)).fetchone()
            if old:
                if old[0]!=key:raise ValueError('Only one late plugin audio per turn')
                return True
            seq=db.execute('SELECT COALESCE(MAX(seq),-1)+1 FROM reply_outbox WHERE task_id=?',(task,)).fetchone()[0]
            db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,kind,seq,path,sha256) VALUES(?,?,?,?,?,?,?,?)',
                       (key,task,OWNER,'','audio',seq,str(path),digest))
    return True


def queue_cover_file(event,path):
    """Return True only after committing a late audio; False keeps live SSE."""
    token=extract_token(event.get_message_str())
    if event.get_platform_name()!='webchat' or not token:return False
    db=sqlite3.connect(audio_sender.ROOT/'assistant.sqlite3',timeout=10)
    try:
        _,late=authorized(db,token)
        if not late:return False
        source=Path(path)
        if not source.is_file() or not 0<source.stat().st_size<=audio_sender.MAX_BYTES:raise ValueError('Plugin audio exceeds size limit')
        with source.open('rb') as stream:raw=stream.read(audio_sender.MAX_BYTES+1)
        return enqueue(db,token,raw)
    finally:db.close()
