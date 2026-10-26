"""Managed resource paths and conservative, previewable retention cleanup."""
import hashlib,json,os,re,shutil,sqlite3,threading,time,uuid
from pathlib import Path

RUNTIME=Path(r'__RUNTIME_ROOT__')
TEMP_PARENT=RUNTIME.parent
CONFIG=RUNTIME/'resource-storage.json'
STATE=RUNTIME/'resource-cleanup-state.json'
PREVIEW=RUNTIME/'resource-cleanup-preview.json'
FOLDERS={'media':'media','analysis':'shared-video-analysis','content':'tool-content','context':'task-context'}
LABELS={'media':'视频、图片、语音及资源清单','analysis':'抽帧、九宫格、字幕带','content':'作品评论、OCR及内容缓存','context':'本会话任务资料包','outgoing':'已发送图片和鲸聊语音缓存'}
CATEGORIES=set(FOLDERS)|{'outgoing'}
OUTGOING_PREFIXES=('astrbot-','resource-')
LOCK=threading.RLock()

def outgoing_dir():return RUNTIME/'outgoing-images'


def outgoing_dirs():return [outgoing_dir(),RUNTIME/'outgoing-audio']

def read(path,default):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)

def config():
    value=read(CONFIG,{})
    if not isinstance(value,dict):value={}
    return {'base':str(RUNTIME),'previous_bases':[],'cleanup_enabled':False,'retention_days':30,
            'interval_hours':24,'categories':list(FOLDERS)+['outgoing'],'next_run_at':None,**value}

def bases():
    cfg=config()
    return list(dict.fromkeys([Path(cfg['base']),RUNTIME]+[Path(p) for p in cfg['previous_bases']]))

def resource_dirs(kind,legacy_root=None):
    if kind not in FOLDERS:raise ValueError('Unknown resource category')
    if legacy_root is not None and Path(legacy_root)!=RUNTIME:return [Path(legacy_root)/FOLDERS[kind]]
    return [base/FOLDERS[kind] for base in bases()]

def resource_dir(kind,legacy_root=None):return resource_dirs(kind,legacy_root)[0]

def media_output_allowed(directory,legacy_root=None):
    p=Path(directory).resolve()
    legacy=Path(legacy_root or RUNTIME).resolve()
    return p.is_relative_to(legacy) or any(p.is_relative_to(r.resolve()) for r in resource_dirs('media',legacy_root))

def validate_base(value):
    if not isinstance(value,str) or not value.strip():raise ValueError('请填写资源根目录')
    path=Path(value.strip())
    if not path.is_absolute() or '..' in path.parts:raise ValueError('请选择绝对路径，不允许上级目录符号')
    resolved=path.resolve()
    if resolved==TEMP_PARENT.resolve() or not resolved.is_relative_to(TEMP_PARENT.resolve()):
        raise ValueError('资源目录须位于 __TEMP_PARENT__ 下的独立文件夹')
    if resolved!=RUNTIME.resolve() and resolved.is_relative_to(RUNTIME.resolve()):
        raise ValueError('请在工作临时文件下选择独立文件夹，不要选账号、工具或现有资源的子目录')
    for parent in [path]+list(path.parents):
        if parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction()):raise ValueError('资源路径不能经过符号链接或目录联接')
    for base in bases():
        if resolved!=base.resolve() and (resolved.is_relative_to(base.resolve()) or base.resolve().is_relative_to(resolved)):
            raise ValueError('新旧资源根目录不能相互包含')
    return resolved

def save(values,confirm_cleanup=False):
    allowed={'base','cleanup_enabled','retention_days','interval_hours','categories'}
    if not isinstance(values,dict) or set(values)-allowed:raise ValueError('无效的资源设置字段')
    with LOCK:
        old=config();new={**old,**values};base=validate_base(new['base'])
        if type(new['cleanup_enabled']) is not bool:raise ValueError('清理开关必须是布尔值')
        if type(new['retention_days']) is not int or not 1<=new['retention_days']<=3650:raise ValueError('保留天数须为1–3650')
        if type(new['interval_hours']) is not int or not 1<=new['interval_hours']<=720:raise ValueError('检查间隔须为1–720小时')
        if not isinstance(new['categories'],list) or not new['categories'] or any(k not in CATEGORIES for k in new['categories']):raise ValueError('请选择有效的清理类别')
        if new['cleanup_enabled'] and confirm_cleanup is not True:raise ValueError('启用定期删除前请明确确认')
        previous=list(old['previous_bases'])
        if Path(old['base'])!=base and old['base'] not in previous:previous.append(old['base'])
        new.update(base=str(base),previous_bases=previous,categories=list(dict.fromkeys(new['categories'])))
        for folder in FOLDERS.values():(base/folder).mkdir(parents=True,exist_ok=True)
        outgoing_dir().mkdir(parents=True,exist_ok=True)
        probe=base/('.write-probe-'+uuid.uuid4().hex);probe.write_bytes(b'');probe.unlink()
        new['next_run_at']=time.time()+new['interval_hours']*3600 if new['cleanup_enabled'] else None
        atomic(CONFIG,new)
    return settings()

def settings():
    cfg=config()
    paths={k:str(resource_dir(k)) for k in FOLDERS}
    paths['outgoing']=str(outgoing_dir())+'；'+str(RUNTIME/'outgoing-audio')
    return {'config':cfg,'paths':paths,'labels':LABELS,
        'previous_bases':list(map(str,bases()[1:])),'runtime':str(RUNTIME),
        'last_cleanup':read(STATE,{}),'notes':'修改仅影响新资源，旧资源不迁移；清理不删除任务数据库、登录数据、工具、备份和未结束任务。'}

def tree_info(path):
    """No reparse traversal; fingerprint every file before a deletion preview."""
    size=0;stamp=0;digest=hashlib.sha256();count=0
    for root,dirs,files in os.walk(path,followlinks=False):
        for name in ['.']+dirs+files:
            p=Path(root) if name=='.' else Path(root)/name
            if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise ValueError('Reparse point refused')
            s=p.stat();stamp=max(stamp,s.st_mtime)
            if p.is_file():size+=s.st_size
            digest.update((str(p.relative_to(path))+':'+str(s.st_size)+':'+str(s.st_mtime_ns)).encode())
            count+=1
            if count>20000:raise ValueError('Oversized resource tree')
    return {'bytes':size,'mtime':stamp,'fingerprint':digest.hexdigest()}

def file_info(path):
    p=Path(path);stat=p.stat()
    digest=hashlib.sha256((str(p)+'|'+str(stat.st_size)+'|'+str(stat.st_mtime_ns)).encode()).hexdigest()
    return {'bytes':stat.st_size,'mtime':stat.st_mtime,'fingerprint':digest}

def _db():return sqlite3.connect(RUNTIME/'assistant.sqlite3',timeout=5)

def candidates(db):
    tasks={r[0]:r[1] for r in db.execute('SELECT id,status FROM live_tasks')}
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    protected=set()
    for table in ('reply_outbox','shared_comment_outbox'):
        if table in tables:
            protected.update(r[0] for r in db.execute(f"SELECT task_id FROM {table} WHERE status IN ('pending','attempted','processing')"))
    ended={t for t,s in tasks.items() if s in ('done','failed') and t not in protected}
    groups={};records=[]
    def add(kind,path,finished,record_id):
        p=Path(path).resolve()
        for root in resource_dirs(kind):
            root=root.resolve()
            if p.is_relative_to(root) and p!=root:
                child=root/p.relative_to(root).parts[0]
                if not re.fullmatch(r'[0-9a-f]{16,32}|\d{15,25}',child.name):return
                item=groups.setdefault((kind,str(child)),{'kind':kind,'path':str(child),'finished':True,'record_ids':[]})
                item['finished'] &= finished
                if record_id not in item['record_ids']:item['record_ids'].append(record_id)
                return
    for task in tasks:
        for root in resource_dirs('context'):add('context',root/task/'context.md',task in ended,task)
    if 'media_records' in tables:
        for task,attached,raw in db.execute('SELECT id,attached_to,record_json FROM media_records'):
            try:r=json.loads(raw)
            except ValueError:continue
            finished=task in ended or (tasks.get(task)=='resource_saved' and attached in ended and task not in protected)
            for key in ('manifest_path',):
                if r.get(key):add('media',r[key],finished,task)
            for file in r.get('files',[]):
                if file.get('path'):add('media',file['path'],finished,task)
            analysis=r.get('shared_video_analysis') or {}
            for p in [analysis.get('contact_sheet'),analysis.get('subtitle_strip')]+analysis.get('frames',[]):
                if p:add('analysis',p,finished,task)
            for clip in (r.get('audio_analysis') or {}).get('clips',[]):
                if clip.get('cache_file'):add('analysis',clip['cache_file'],finished,task)
            video=str((r.get('video') or {}).get('video_id') or '')
            if re.fullmatch(r'\d{15,25}',video):
                for root in resource_dirs('content'):add('content',root/video/'transcript.json',finished,task)
    protected_paths=set()
    if 'reply_outbox' in tables:
        protected_paths={str(Path(r[0]).resolve()) for r in db.execute("SELECT path FROM reply_outbox WHERE kind IN ('image','audio') AND path IS NOT NULL AND status IN ('pending','attempted','processing','outcome_uncertain')")}
    if 'local_image_jobs' in tables:
        protected_paths.update(str(Path(r[0]).resolve()) for r in db.execute("SELECT path FROM local_image_jobs WHERE path IS NOT NULL AND state IN ('ready','needs_attention')"))
    if 'whale_messages' in tables:
        # Whale history contains local attachments, rather than CDN copies.
        # Preserve referenced images; never claim queue acceptance means viewed.
        protected_paths.update(str(Path(r[0]).resolve()) for r in db.execute("SELECT path FROM whale_messages WHERE kind IN ('image','audio','file') AND path IS NOT NULL"))
    for group in groups.values():
        if group['kind']=='media' and any(Path(p).is_relative_to(Path(group['path'])) for p in protected_paths):
            group['finished']=False
    try:outgoing_files=sorted(path for directory in outgoing_dirs() for prefix in OUTGOING_PREFIXES for path in directory.glob(prefix+'*'))
    except OSError:outgoing_files=[]
    for path in outgoing_files:
        try:
            if not path.is_file() or str(path.resolve()) in protected_paths:continue
        except OSError:continue
        groups[('outgoing',str(path))]={'kind':'outgoing','path':str(path),'finished':True,'record_ids':[],'file':True}
    return [g for g in groups.values() if g['finished']]

def plan(db=None,now=None):
    owns=db is None;db=db or _db();cfg=config();now=time.time() if now is None else now
    try:
        items=[]
        for item in candidates(db):
            if item['kind'] not in cfg['categories']:continue
            p=Path(item['path'])
            try:
                if item.get('file'):
                    if not p.is_file():continue
                    info=file_info(p)
                else:
                    if not p.is_dir():continue
                    info=tree_info(p)
            except (OSError,ValueError):continue
            if info['mtime']>now-cfg['retention_days']*86400:continue
            items.append({**item,**info})
            if len(items)>=300:break
        return {'token':uuid.uuid4().hex,'created':now,'config':cfg,'items':items,
                'directories':len(items),'bytes':sum(i['bytes'] for i in items)}
    finally:
        if owns:db.close()

def preview():
    with LOCK:
        value=plan();atomic(PREVIEW,value);return value

def busy(db):
    if db.execute("SELECT 1 FROM live_tasks WHERE status IN ('pending','processing','media_pending') LIMIT 1").fetchone():return True
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'local_image_jobs' in tables and db.execute("SELECT 1 FROM local_image_jobs WHERE state IN ('queued','starting','submitting','running','ready') LIMIT 1").fetchone():return True
    for table in ('reply_outbox','shared_comment_outbox'):
        if table in tables and db.execute(f"SELECT 1 FROM {table} WHERE status IN ('pending','attempted','processing') LIMIT 1").fetchone():return True
    if 'media_records' in tables:
        for raw, in db.execute('SELECT record_json FROM media_records'):
            try:
                if any((json.loads(raw).get(field) or {}).get('status')=='processing' for field in ('shared_video_analysis','audio_analysis')):return True
            except ValueError:continue
    return False

def execute(value,db=None):
    owns=db is None;db=db or _db();result={'time':time.time(),'deleted':[],'skipped':[],'bytes':0}
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if busy(db):return {**result,'state':'deferred_busy'}
            current={(i['kind'],i['path']) for i in candidates(db)}
            for item in value['items']:
                p=Path(item['path']);kind=item['kind']
                try:
                    # Recheck under the write transaction before every deletion.
                    if busy(db):raise ValueError('Workflow became busy')
                    current={(i['kind'],i['path']) for i in candidates(db)}
                    # Revalidate absolute roots, ownership, completion and tree contents.
                    if (kind,str(p)) not in current:raise ValueError('Resource is no longer eligible')
                    if item.get('file'):
                        if kind!='outgoing' or p.resolve()!=p or p.parent.resolve() not in [d.resolve() for d in outgoing_dirs()] or not p.name.startswith(OUTGOING_PREFIXES):
                            raise ValueError('Unexpected cleanup target')
                        if file_info(p)['fingerprint']!=item['fingerprint']:raise ValueError('Resource changed since preview')
                        p.unlink()
                        result['deleted'].append(str(p));result['bytes']+=item['bytes']
                        continue
                    if p.resolve()!=p or p.parent.resolve() not in [r.resolve() for r in resource_dirs(kind)]:raise ValueError('Unexpected cleanup target')
                    if tree_info(p)['fingerprint']!=item['fingerprint']:raise ValueError('Resource changed since preview')
                    shutil.rmtree(p)
                    result['deleted'].append(str(p));result['bytes']+=item['bytes']
                    for task in item['record_ids']:
                        row=db.execute('SELECT record_json FROM media_records WHERE id=?',(task,)).fetchone()
                        if not row:continue
                        record=json.loads(row[0]);record.setdefault('cleanup',{})[kind]=result['time']
                        if kind=='media':
                            record.update(status='cleaned',failure_reason='Local resources removed by configured retention cleanup')
                            for f in record.get('files',[]):f['status']='cleaned'
                        elif kind=='analysis':record['shared_video_analysis']={**record.get('shared_video_analysis',{}),'status':'cleaned'}
                        db.execute('UPDATE media_records SET record_json=? WHERE id=?',(json.dumps(record,ensure_ascii=False),task))
                except (OSError,ValueError) as exc:result['skipped'].append({'path':str(p),'reason':type(exc).__name__})
        result['state']='completed';return result
    finally:
        if owns:db.close()

def run_preview(token):
    with LOCK:
        value=read(PREVIEW,{})
        if not isinstance(token,str) or value.get('token')!=token or time.time()-value.get('created',0)>600:raise ValueError('预览已失效，请重新预览')
        if value.get('config')!=config():raise ValueError('设置已改变，请重新预览')
        result=execute(value);atomic(STATE,result);atomic(PREVIEW,{})
        return result

def cleanup_loop(stop):
    while not stop.is_set():
        try:
            with LOCK:
                cfg=config()
                if cfg['cleanup_enabled'] and (cfg.get('next_run_at') or float('inf'))<=time.time():
                    result=execute(plan());atomic(STATE,result)
                    cfg['next_run_at']=time.time()+(300 if result['state']=='deferred_busy' else cfg['interval_hours']*3600)
                    atomic(CONFIG,cfg)
        except Exception as exc:atomic(STATE,{'time':time.time(),'state':'error','error_class':type(exc).__name__})
        stop.wait(60)
