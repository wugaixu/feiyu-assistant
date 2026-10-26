"""Read only verified images belonging to a currently processing owner turn."""
import base64
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from app import visual_delivery
from app import task_materials

PREFIX='[本轮看图许可:'


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS local_vision_permits(
        token TEXT PRIMARY KEY,task_id TEXT UNIQUE NOT NULL,session_id TEXT NOT NULL,
        expires REAL NOT NULL,images TEXT NOT NULL)''')
    if 'texts' not in {r[1] for r in db.execute('PRAGMA table_info(local_vision_permits)')}:
        db.execute("ALTER TABLE local_vision_permits ADD COLUMN texts TEXT NOT NULL DEFAULT '[]'")
    db.commit()


def permit(db,task_id,session_id,records):
    from app.sdk_receiver import OWNER
    row=db.execute('SELECT owner,status FROM live_tasks WHERE id=?',(task_id,)).fetchone()
    if not row or row!=(OWNER,'processing'):raise ValueError('Processing owner turn required')
    from app.task_context import packet_path
    materials=task_materials.snapshot(records,packet_path(task_id))
    images=materials['images'];texts=materials['texts']
    if not images and not texts:return ''
    schema(db)
    with db:
        db.execute('INSERT OR IGNORE INTO local_vision_permits(token,task_id,session_id,expires,images,texts) VALUES(?,?,?,?,?,?)',
                   (secrets.token_hex(24),task_id,session_id,time.time()+600,json.dumps(images),json.dumps(texts)))
    return db.execute('SELECT token FROM local_vision_permits WHERE task_id=?',(task_id,)).fetchone()[0]


def extract_token(text):
    match=re.search(r'\[本轮看图许可:([0-9a-f]{48})\]',str(text))
    return match.group(1) if match else None


def _permission(db,token):
    from app.sdk_receiver import OWNER
    row=db.execute('''SELECT p.images,p.texts FROM local_vision_permits p
        JOIN live_tasks t ON t.id=p.task_id JOIN chat_dispatch d ON d.task_id=t.id
        WHERE p.token=? AND p.expires>? AND t.owner=? AND t.status='processing'
        AND d.status IN ('attempted','queued')
        AND json_extract(d.detail,'$.provider')='astrbot'
        AND json_extract(d.detail,'$.session_id')=p.session_id''',(token,time.time(),OWNER)).fetchone()
    if not row:raise ValueError('Image reading permission is invalid or expired')
    return json.loads(row[0]),json.loads(row[1])


def read(db,token,path):
    images,_=_permission(db,token)
    raw,image=task_materials.verified_bytes(images,path,10*1024*1024)
    return {'data':base64.b64encode(raw).decode('ascii'),'mimeType':image['mediaType']}


def read_text(db,token,path,offset=0,limit=8000):
    _,texts=_permission(db,token)
    return task_materials.read_text(texts,path,offset,limit)


def comment(db,token,video_id,text,evidence):
    _permission(db,token)
    row=db.execute('SELECT task_id FROM local_vision_permits WHERE token=?',(token,)).fetchone()
    from app.shared_comments import enqueue
    return enqueue(db,row[0],video_id,{'text':text,'evidence':evidence})
