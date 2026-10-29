"""The same registered material catalog and bounded file reader for all hosts."""
import hashlib
import json
from pathlib import Path
from app import visual_delivery,resource_storage

TEXT_LIMIT=8000
TEXT_SUFFIXES={'.md','.txt','.json','.csv','.log','.yaml','.yml','.py','.js','.html','.xml'}


def catalog(records,context):
    images=visual_delivery.inputs(records,limit=None,max_total=None)
    roots=[r.resolve() for kind in ('media','analysis','content','context') for r in resource_storage.resource_dirs(kind)]
    context=Path(context).resolve()
    candidates=[context,context.with_name('instructions.md')]
    for record in records:
        audio=record.get('audio_analysis') or record.get('transcript') or {}
        candidates.extend(audio.get(k) for k in ('text_path','path') if audio.get(k))
        comments=record.get('comments') or {}
        if comments.get('path'):candidates.append(comments['path'])
        for file in record.get('files',[]):
            if file.get('status')=='saved' and Path(file.get('path','')).suffix.lower() in TEXT_SUFFIXES:
                candidates.append(file['path'])
    texts=[];seen=set()
    for name in candidates:
        path=Path(name).resolve()
        if path in seen or not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:continue
        if path!=context and not any(path.is_relative_to(r) for r in roots):continue
        if path.name=='data.json':continue  # Raw SDK diagnostics can contain signed URLs/keys.
        if path.stat().st_size>4*1024*1024:continue
        raw=path.read_bytes()
        if b'\0' in raw:continue
        texts.append({'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
        seen.add(path)
    return {'images':images,'texts':texts}


def verified_bytes(items,path,max_bytes):
    target=Path(path).resolve(strict=True)
    item=next((x for x in items if Path(x['path'])==target),None)
    if not item:raise ValueError('File is not registered for this task')
    with target.open('rb') as source:raw=source.read(max_bytes+1)
    if len(raw)>max_bytes or hashlib.sha256(raw).hexdigest()!=item['sha256']:
        raise ValueError('Registered material changed')
    return raw,item


def snapshot(records,context):
    manifest=Path(context).with_name('materials.json')
    if manifest.is_file():
        value=json.loads(manifest.read_text(encoding='utf-8'))
        if value.get('protocol_version')!=4 or value.get('context_file')!=str(Path(context).resolve()):
            raise ValueError('Material catalog does not match this task context')
        return {'images':value['images'],'texts':value['texts']}
    return catalog(records,context)  # Legacy/isolated fixtures; new packets always freeze a catalog.


def read_text(items,path,offset=0,limit=TEXT_LIMIT):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=TEXT_LIMIT:
        raise ValueError('Use a nonnegative offset and a 1..8000 character limit')
    raw,item=verified_bytes(items,path,4*1024*1024)
    try:text=raw.decode('utf-8-sig')
    except UnicodeDecodeError:text=raw.decode('gb18030')
    if offset>len(text):raise ValueError('Offset beyond text')
    end=min(offset+limit,len(text))
    return {'path':item['path'],'sha256':item['sha256'],'text':text[offset:end],
            'offset':offset,'next_offset':end if end<len(text) else None,'total_chars':len(text),
            'untrusted_content':True}


def for_task(db,task_id):
    from app.sdk_receiver import OWNER
    from app.task_context import packet_path
    row=db.execute('SELECT owner,status FROM live_tasks WHERE id=?',(task_id,)).fetchone()
    if not row or row!=(OWNER,'processing'):raise ValueError('Claim this owner task before reading')
    records=[json.loads(r[0]) for r in db.execute('SELECT record_json FROM media_records WHERE owner=? AND (id=? OR attached_to=?)',(OWNER,task_id,task_id))]
    return snapshot(records,packet_path(task_id))
