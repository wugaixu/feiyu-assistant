"""Atomically finish a task with an ordered, prevalidated reply batch."""
import json
from app import outbox
from app.reply_limits import MAX_PARTS,MAX_TEXT,MAX_TOTAL_TEXT
from app.image_sender import validate_image

SUCCESS=outbox.SUCCESS

def schema(db):
    outbox.schema(db)

def finish(db,task_id,parts,*,status='done'):
    if status not in ('done','needs_input','failed'):raise ValueError('Invalid completion status')
    if not isinstance(parts,list) or not 1<=len(parts)<=MAX_PARTS:raise ValueError('Reply requires 1..12 parts')
    prepared=[];total=0
    for part in parts:
        if part.get('type')=='text':
            text=part.get('text','').strip()
            if not 1<=len(text)<=MAX_TEXT:raise ValueError('Text part must be 1..1500 characters')
            total+=len(text);prepared.append(('text',text,None,None))
        elif part.get('type')=='image':
            path,digest=validate_image(part['path']);prepared.append(('image',str(path),digest,None))
        elif part.get('type')=='audio':
            from app.audio_sender import validate_audio
            caption=part.get('text','')
            if not isinstance(caption,str) or len(caption)>MAX_TEXT:raise ValueError('Invalid audio caption')
            total+=len(caption)
            path,digest,_=validate_audio(part['path']);prepared.append(('audio',str(path),digest,caption))
        elif part.get('type')=='share':
            work=part.get('work') or {}
            import re
            if not re.fullmatch(r'\d{15,25}',str(work.get('itemId') or '')):raise ValueError('Verified work ID required')
            if set(work)-{'itemId','title','authorUid','authorSecUid','authorName','coverUrl','imageCount'}:raise ValueError('Unknown work fields')
            if len(json.dumps(work,ensure_ascii=False))>5000:raise ValueError('Work card too large')
            prepared.append(('share',json.dumps(work,ensure_ascii=False),None,None))
        else:raise ValueError('Unsupported outgoing part')
    if total>MAX_TOTAL_TEXT:raise ValueError('Reply too long')
    schema(db)
    from app.shared_comments import schema as comment_schema
    comment_schema(db)
    from app import channels
    channels.schema(db)
    with db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT owner FROM live_tasks WHERE id=? AND status='processing'",(task_id,)).fetchone()
        if not row:raise ValueError('Task already finished or not claimed')
        raw_row=db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(task_id,)).fetchone()
        raw=json.loads(raw_row[0]) if raw_row else {}
        if raw.get('origin')=='local_schedule' and 'return_channel' not in raw:
            route=channels.current(db)
            raw.update(return_channel=route['channel'],reply_device=route['device'])
            db.execute('UPDATE live_tasks SET raw_json=? WHERE id=?',(json.dumps(raw,ensure_ascii=False),task_id))
        channel=channels.address(db,task_id)['channel']
        if any(p[0]=='audio' for p in prepared) and channel not in ('whale','qq'):
            raise ValueError('Audio replies require the Whale channel')
        if channel=='qq':
            ordered=[]
            for index,part in enumerate(prepared):
                kind,value,digest,caption=part
                caption=caption.strip() if kind=='audio' and caption else ''
                # Reposition an adjacent identical original, rather than echoing twice.
                if (kind=='text' and index+1<len(prepared) and prepared[index+1][0]=='audio'
                    and (prepared[index+1][3] or '').strip()==value
                    and (index==0 or prepared[index-1][0]!='audio')):
                    continue
                ordered.append(part)
                if caption and not (index+1<len(prepared) and prepared[index+1][0]=='text' and prepared[index+1][1]==caption):
                    ordered.append(('text',caption,None,None))
            prepared=ordered
        for seq,(kind,value,digest,work) in enumerate(prepared):
            key=f'{task_id}:batch:{seq}'
            if kind=='text':
                db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,kind,seq) VALUES(?,?,?,?,?,?)',
                           (key,task_id,row[0],value,'text',seq))
            elif kind in ('image','audio'):
                db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,kind,seq,path,sha256) VALUES(?,?,?,?,?,?,?,?)',
                           (key,task_id,row[0],work if kind=='audio' else '',kind,seq,value,digest))
            else:
                db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,kind,seq,work_json) VALUES(?,?,?,?,?,?,?)',
                           (key,task_id,row[0],'','share',seq,value))
        db.execute('UPDATE live_tasks SET status=?,result=? WHERE id=?',(status,json.dumps(parts,ensure_ascii=False),task_id))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_dispatch'").fetchone():
            db.execute("UPDATE chat_dispatch SET status='processed' WHERE task_id=?",(task_id,))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='video_rounds'").fetchone():
            db.execute('UPDATE video_rounds SET status=? WHERE id=?',(status,task_id))
        db.execute("UPDATE shared_comment_outbox SET status=? WHERE task_id=? AND status='waiting_for_reply'",
                   ('pending' if status=='done' else 'cancelled',task_id))

def allowed(db,key):
    return outbox.allowed(db,key)
