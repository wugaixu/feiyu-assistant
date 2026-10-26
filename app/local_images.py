"""Durable local ComfyUI jobs. A private turn grants one serial image batch.

No detached WebChat sends: completed images join the normal SDK outbox after
the original private reply commits. Generation submission is never retried.
"""
import hashlib
import json
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path

ROOT=Path(r'__RUNTIME_ROOT__')
BASE=ROOT/'tools/local-image'
URL='http://127.0.0.1:8188'
PREFIX='这是已核验主人的私聊。'
_process=None
_log=None
_released=False
LOCK=threading.Lock()
MAX_BATCH_IMAGES=10


def settings():
    try:return json.loads((ROOT/'local-image.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return {'enabled':False}


ACTIVITY=ROOT/'local-image-activity.json'
IDLE_EXIT_MINUTES=10


def idle_minutes():
    """Idle window before an owned backend process is ended (1–60 minutes)."""
    try:value=json.loads((ROOT/'local-image.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):value={}
    idle=value.get('idle_minutes')
    return idle if isinstance(idle,int) and not isinstance(idle,bool) and 1<=idle<=60 else IDLE_EXIT_MINUTES


def touch(reason='image'):
    """Record local image activity so an idle backend can be released."""
    try:ACTIVITY.write_text(json.dumps({'at':time.time(),'reason':str(reason)[:32]}),encoding='utf-8')
    except OSError:pass


def last_activity():
    """Latest activity from manual jobs and the private-image job table."""
    stamps=[]
    try:stamps.append(float(json.loads(ACTIVITY.read_text(encoding='utf-8')).get('at')))
    except (OSError,ValueError,TypeError):pass
    try:
        db=sqlite3.connect('file:'+str(ROOT/'assistant.sqlite3')+'?mode=ro',uri=True,timeout=1)
        try:
            row=db.execute('SELECT MAX(updated) FROM local_image_jobs').fetchone()
            if row and row[0]:stamps.append(float(row[0]))
        finally:db.close()
    except sqlite3.Error:pass
    return max(stamps) if stamps else None


def idle_expired(minutes=None):
    last=last_activity()
    limit=(minutes if isinstance(minutes,int) else idle_minutes())*60
    return last is not None and time.time()-last>=limit


def stop_backend(reason='idle'):
    """End only the backend this process started, then allow a later restart."""
    global _process,_log,_released
    if _process is None:return False
    if _process.poll() is None:
        _process.terminate()
        try:_process.wait(timeout=20)
        except Exception:
            try:_process.kill()
            except Exception:pass
    _process=None;_released=False
    if _log is not None:
        _log.close();_log=None
    try:(BASE/'backend-idle-exit.log').write_text(time.strftime('%Y-%m-%d %H:%M:%S')+' '+str(reason)+'\n',encoding='utf-8')
    except OSError:pass
    return True


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS local_image_permits(
        token TEXT PRIMARY KEY,task_id TEXT UNIQUE NOT NULL,session_id TEXT NOT NULL,
        created REAL NOT NULL,expires REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS local_image_jobs(
        id TEXT PRIMARY KEY,token TEXT UNIQUE NOT NULL,task_id TEXT NOT NULL,
        prompt TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,
        prompt_id TEXT,path TEXT,error TEXT,notified INTEGER NOT NULL DEFAULT 0)''')
    if 'notified' not in {r[1] for r in db.execute('PRAGMA table_info(local_image_jobs)')}:
        db.execute('ALTER TABLE local_image_jobs ADD COLUMN notified INTEGER NOT NULL DEFAULT 0')
    columns={r[1] for r in db.execute('PRAGMA table_info(local_image_jobs)')}
    for name,decl in [('batch_id','TEXT'),('batch_index','INTEGER NOT NULL DEFAULT 1'),('batch_total','INTEGER NOT NULL DEFAULT 1')]:
        if name not in columns:db.execute(f'ALTER TABLE local_image_jobs ADD COLUMN {name} {decl}')
    if 'format' not in columns:db.execute("ALTER TABLE local_image_jobs ADD COLUMN format TEXT NOT NULL DEFAULT 'png'")
    db.commit()


IMAGE_FORMATS=('png','jpeg','webp','gif')


def batch_format(value):
    """One outbound image format per batch; ComfyUI itself only writes PNG."""
    fmt=str(value or 'png').strip().lower()
    if fmt in ('jpg','jpeg'):return 'jpeg'
    if fmt not in IMAGE_FORMATS:raise ValueError('输出格式需为 png/jpeg/webp/gif')
    return fmt


def permit(db,task_id,session_id):
    if not settings().get('enabled'):return ''
    from app.sdk_receiver import OWNER
    row=db.execute('SELECT owner,status,raw_json FROM live_tasks WHERE id=?',(task_id,)).fetchone()
    if not row or row[0]!=OWNER or row[1]!='processing':raise ValueError('Claimed owner turn required')
    raw=json.loads(row[2])
    if raw.get('direction')!='incoming' or raw.get('origin')=='local_schedule':return ''
    schema(db)
    token=secrets.token_hex(24);now=time.time()
    with db:db.execute('INSERT OR IGNORE INTO local_image_permits VALUES(?,?,?,?,?)',(token,task_id,session_id,now,now+240))
    token=db.execute('SELECT token FROM local_image_permits WHERE task_id=?',(task_id,)).fetchone()[0]
    return '\n[本地生图许可:'+token+']\n'


def extract_token(text):
    # Local worker inserts this before all history and untrusted material.
    match=re.match(re.escape(PREFIX)+r'\n\[本地生图许可:([0-9a-f]{48})\]\n',str(text))
    return match.group(1) if match else None


def submit(db,token,prompt):
    """Compatibility entry for existing single-image calls."""
    return submit_batch(db,token,prompt=prompt)


def batch_prompts(prompt='',count=1,prompts=None):
    if isinstance(count,float) and count.is_integer():count=int(count)
    if not isinstance(count,int) or isinstance(count,bool) or not 1<=count<=MAX_BATCH_IMAGES:
        raise ValueError(f'单批数量需为1–{MAX_BATCH_IMAGES}的整数')
    if not isinstance(prompt,str):raise ValueError('画面描述必须为文字')
    prompt=prompt.strip()
    if prompts is not None:
        if not isinstance(prompts,list) or not 1<=len(prompts)<=MAX_BATCH_IMAGES:raise ValueError('请提供1–10条画面描述')
        if count not in (1,len(prompts)):raise ValueError('数量与逐张描述条数不一致')
        if any(not isinstance(p,str) or not p.strip() for p in prompts):raise ValueError('每张图片需要非空文字描述')
        values=[(prompt+'\n'+p.strip()).strip() if prompt else p.strip() for p in prompts]
    else:values=[prompt]*count
    if any(not 1<=len(p)<=3000 for p in values):raise ValueError('每张画面描述需1–3000字')
    return values


def submit_batch(db,token,prompt='',count=1,prompts=None,fmt='png'):
    if not settings().get('enabled'):raise ValueError('本地生图未启用')
    output_format=batch_format(fmt)
    values=batch_prompts(prompt,count,prompts)
    schema(db);now=time.time()
    from app.sdk_receiver import OWNER
    with db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT id,state,COALESCE(batch_id,id),batch_total FROM local_image_jobs WHERE token=?',(token,)).fetchone()
        if old:return {'job_id':old[0],'state':old[1],'batch_id':old[2],'count':old[3],'duplicate':True}
        p=db.execute('''SELECT p.task_id FROM local_image_permits p JOIN live_tasks t ON t.id=p.task_id
            JOIN chat_dispatch d ON d.task_id=t.id WHERE p.token=? AND p.expires>?
            AND t.owner=? AND t.status='processing' AND d.status IN ('attempted','queued')
            AND json_extract(d.detail,'$.provider')='astrbot'
            AND json_extract(d.detail,'$.session_id')=p.session_id''',(token,now,OWNER)).fetchone()
        if not p:raise ValueError('本轮生图许可无效或已过期')
        if db.execute("SELECT COUNT(DISTINCT COALESCE(batch_id,id)) FROM local_image_jobs WHERE state IN ('queued','starting','submitting','running','ready')").fetchone()[0]>=3:raise ValueError('生图队列已有3批任务，请稍后再试')
        key=uuid.uuid4().hex
        for index,value in enumerate(values,1):
            child=key if index==1 else uuid.uuid4().hex
            child_token=token if index==1 else token+':'+str(index)
            db.execute('INSERT INTO local_image_jobs(id,token,task_id,prompt,state,created,updated,batch_id,batch_index,batch_total,format) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(child,child_token,p[0],value,'queued',now,now,key,index,len(values),output_format))
    return {'job_id':key,'state':'queued','batch_id':key,'count':len(values),'format':output_format}


def api(path,body=None,timeout=5):
    if not path.startswith('/') or path.startswith('//'):raise ValueError('Local endpoint required')
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    q=urllib.request.Request(URL+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with op.open(q,timeout=timeout) as response:
        data=response.read(4_000_001)
    if len(data)>4_000_000:raise ValueError('Oversized ComfyUI response')
    return json.loads(data)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**kw):raise ValueError('Local redirect refused')


def workflow(prompt):
    return {
      '1':{'class_type':'UnetLoaderGGUF','inputs':{'unet_name':'qwen_image_2.1-Q4_K.gguf'}},
      '2':{'class_type':'CLIPLoader','inputs':{'clip_name':'qwen3vl_8b_w4a8.safetensors','type':'qwen_image','device':'default'}},
      '3':{'class_type':'VAELoader','inputs':{'vae_name':'qwen_image_2.1_vae_bf16.safetensors'}},
      '4':{'class_type':'TextEncodeQwenImage21','inputs':{'clip':['2',0],'prompt':prompt,'negative_prompt':'','resolution':768,'images':[],'vae':['3',0]}},
      '5':{'class_type':'EmptyLatentImage','inputs':{'width':768,'height':768,'batch_size':1}},
      '6':{'class_type':'KSampler','inputs':{'model':['1',0],'positive':['4',0],'negative':['4',1],'latent_image':['5',0],'seed':secrets.randbelow(2**32),'steps':8,'cfg':1.0,'sampler_name':'euler','scheduler':'simple','denoise':1.0}},
      '7':{'class_type':'VAEDecodeTiled','inputs':{'samples':['6',0],'vae':['3',0],'tile_size':512,'overlap':64,'temporal_size':64,'temporal_overlap':8}},
      '8':{'class_type':'SaveImage','inputs':{'images':['7',0],'filename_prefix':'douyin-local'}}}


def ensure_backend():
    global _process,_log
    try:api('/system_stats',timeout=2);return True
    except Exception:pass
    if _process is not None and _process.poll() is None:return False
    if _process is not None:raise RuntimeError('Managed ComfyUI exited; inspect backend log before restarting')
    py=BASE/'venv/Scripts/pythonw.exe';main=BASE/'ComfyUI/main.py'
    if not py.exists() or not main.exists():raise ValueError('本地生图运行环境不完整')
    _log=(BASE/'comfyui.log').open('a',encoding='utf-8')
    _process=subprocess.Popen([str(py),'-B','-X','utf8',str(main),'--listen','127.0.0.1','--port','8188','--lowvram','--preview-method','none','--disable-auto-launch'],cwd=main.parent,stdout=_log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return False


def queue_ready(db,row,path):
    from app.image_sender import validate_image
    path,digest=validate_image(path)
    key,task_id=row['id'],row['task_id']
    with db:
        db.execute('BEGIN IMMEDIATE')
        task=db.execute('SELECT owner,status FROM live_tasks WHERE id=?',(task_id,)).fetchone()
        if not task or task[1]!='done':return False
        from app.sdk_receiver import OWNER
        if task[0]!=OWNER:raise ValueError('Image owner mismatch')
        if not db.execute('SELECT 1 FROM reply_outbox WHERE task_id=?',(task_id,)).fetchone():return False
        seq=db.execute('SELECT COALESCE(MAX(seq),-1)+1 FROM reply_outbox WHERE task_id=?',(task_id,)).fetchone()[0]
        # Older databases require explicit text with no default. Only a duplicate
        # ID is ignorable; other integrity failures must never look like success.
        db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,kind,seq,path,sha256) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',('local-image:'+key,task_id,OWNER,'','image',seq,str(path),digest))
        saved=db.execute('SELECT task_id,owner,kind,path,sha256 FROM reply_outbox WHERE id=?',('local-image:'+key,)).fetchone()
        if not saved or tuple(saved)!=(task_id,OWNER,'image',str(path),digest):raise ValueError('Image outbox insert not confirmed')
        db.execute("UPDATE local_image_jobs SET state='outbox_queued',path=?,updated=? WHERE id=?",(str(path),time.time(),key))
    return True


def _saved_output(item,job_id,fmt='png'):
    images=[im for out in item.get('outputs',{}).values() for im in out.get('images',[])]
    if not images:raise ValueError('ComfyUI completed without image')
    image=images[0]
    if image.get('type')!='output':raise ValueError('Output image required')
    source=(BASE/'ComfyUI/output'/str(image.get('subfolder',''))/str(image['filename'])).resolve(strict=True)
    if not source.is_relative_to((BASE/'ComfyUI/output').resolve()):raise ValueError('Output path escaped')
    from PIL import Image
    output_format=batch_format(fmt)
    suffix={'png':'.png','jpeg':'.jpg','webp':'.webp','gif':'.gif'}[output_format]
    dest=ROOT/'outgoing-images'/('astrbot-local-'+job_id+suffix);dest.parent.mkdir(parents=True,exist_ok=True)
    with Image.open(source) as im:
        if im.width*im.height>4_000_000:raise ValueError('Image too large')
        if output_format=='jpeg':im.convert('RGB').save(dest,'JPEG',quality=95)
        elif output_format=='webp':
            mode='RGBA' if 'A' in im.getbands() else 'RGB'
            im.convert(mode).save(dest,'WEBP',quality=95,method=4)
        elif output_format=='gif':im.convert('RGB').convert('P',palette=Image.ADAPTIVE).save(dest,'GIF')
        else:im.convert('RGB').save(dest,'PNG')
    return dest


def step(db):
    global _released
    schema(db)
    _notify_problems(db)
    row=db.execute("SELECT * FROM local_image_jobs WHERE state IN ('starting','submitting','running','ready','queued') ORDER BY CASE WHEN state='queued' THEN 1 ELSE 0 END,created,batch_index,id LIMIT 1").fetchone()
    if not row:
        if _process is not None and _process.poll() is None and not _released:
            latest=db.execute('SELECT MAX(updated) FROM local_image_jobs').fetchone()[0]
            if latest and time.time()-latest>300:
                api('/free',{'unload_models':True,'free_memory':True});_released=True
        # The model is already unloaded above; this releases the process itself
        # so an idle machine keeps its memory and VRAM.
        if _process is not None and _process.poll() is None and idle_expired():
            stop_backend('manager-idle')
        return
    row=dict(row);key=row['id'];state=row['state'];now=time.time()
    if state=='ready':
        if queue_ready(db,row,row['path']):return
        if now-row['updated']>600:
            with db:db.execute("UPDATE local_image_jobs SET state='needs_attention',error='Original private reply did not commit' WHERE id=?",(key,))
        return
    # Waiting behind earlier pictures is not execution time. Each child receives
    # its own budget only after it is selected for generation.
    if state in ('starting','submitting','running') and now-row['updated']>1200:
        with db:db.execute("UPDATE local_image_jobs SET state='outcome_uncertain',error='Generation timed out; not resubmitted',updated=? WHERE id=?",(now,key))
        return
    if state=='queued':
        if not settings().get('enabled'):return
        with db:
            claimed=db.execute("UPDATE local_image_jobs SET state='starting',updated=? WHERE id=? AND state='queued'",(now,key)).rowcount
        if not claimed:return
        state='starting'
    if state=='starting':
        if not ensure_backend():return
        with db:
            claimed=db.execute("UPDATE local_image_jobs SET state='submitting',updated=? WHERE id=? AND state='starting'",(now,key)).rowcount
        if not claimed:return
        # Persist before POST. Failure/uncertain submission never goes to queued.
        try:
            value=api('/prompt',{'prompt':workflow(row['prompt']),'client_id':'douyin-local-'+key},timeout=30)
            prompt_id=value.get('prompt_id')
            if not isinstance(prompt_id,str) or not re.fullmatch(r'[a-f0-9-]{36}',prompt_id):raise ValueError('Submission not confirmed')
            with db:db.execute("UPDATE local_image_jobs SET state='running',prompt_id=?,updated=? WHERE id=?",(prompt_id,now,key))
            _released=False
            touch('manager-submit')
        except urllib.error.HTTPError as exc:
            rejected=400<=exc.code<500
            with db:db.execute("UPDATE local_image_jobs SET state=?,error=?,updated=? WHERE id=?",('failed' if rejected else 'outcome_uncertain','ComfyUI rejected submission' if rejected else 'Submission result unknown; not retried',now,key))
        except Exception:
            with db:db.execute("UPDATE local_image_jobs SET state='outcome_uncertain',error='Submission result unknown; not retried',updated=? WHERE id=?",(now,key))
        return
    if state=='submitting':
        with db:db.execute("UPDATE local_image_jobs SET state='outcome_uncertain',error='Interrupted submission; not retried',updated=? WHERE id=?",(now,key))
        return
    if state=='running':
        item=api('/history/'+row['prompt_id']).get(row['prompt_id'])
        if not item:return
        if (item.get('status') or {}).get('status_str')=='error':
            with db:db.execute("UPDATE local_image_jobs SET state='failed',error='ComfyUI execution failed; see local backend log',updated=? WHERE id=?",(now,key))
            return
        path=_saved_output(item,key,row.get('format') or 'png')
        with db:db.execute("UPDATE local_image_jobs SET state='ready',path=?,updated=? WHERE id=?",(str(path),now,key))


def loop(stop):
    global _log
    if not LOCK.acquire(blocking=False):return
    db=sqlite3.connect(ROOT/'assistant.sqlite3',timeout=10);db.row_factory=sqlite3.Row
    try:
        while not stop.is_set():
            try:step(db)
            except Exception as exc:
                db.rollback()
                with db:db.execute("UPDATE local_image_jobs SET state='failed',error=?,updated=? WHERE state='starting'",('Backend startup failed: '+type(exc).__name__,time.time()))
                (BASE/'worker-error.json').write_text(json.dumps({'time':time.time(),'error_class':type(exc).__name__}),encoding='utf-8')
            stop.wait(2)
    finally:
        db.close()
        shutdown()
        LOCK.release()


def shutdown():
    """Only release the backend process created by this management process."""
    global _log
    if _process is not None and _process.poll() is None:_process.terminate()
    if _log is not None:
        _log.close();_log=None


def _notify_problems(db):
    """Report a failed/uncertain generation once, using the same ordered outbox."""
    from app.sdk_receiver import OWNER
    rows=db.execute("SELECT id,task_id,state,batch_index,batch_total FROM local_image_jobs WHERE state IN ('failed','outcome_uncertain','needs_attention') AND notified=0").fetchall()
    for key,task_id,state_name,index,total in rows:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM live_tasks WHERE id=? AND owner=? AND status='done'",(task_id,OWNER)).fetchone():continue
            if not db.execute('SELECT 1 FROM reply_outbox WHERE task_id=?',(task_id,)).fetchone():continue
            text='这次本地画图失败了，没生成出可发送的图片。' if state_name=='failed' else '这次画图的结果暂时无法确认，我先不重复生成；需要检查本地生图状态。'
            if total>1:text=f'第{index}/{total}张：'+text
            seq=db.execute('SELECT COALESCE(MAX(seq),-1)+1 FROM reply_outbox WHERE task_id=?',(task_id,)).fetchone()[0]
            db.execute('INSERT OR IGNORE INTO reply_outbox(id,task_id,owner,text,kind,seq) VALUES(?,?,?,?,?,?)',('local-image-error:'+key,task_id,OWNER,text,'text',seq))
            db.execute('UPDATE local_image_jobs SET notified=1 WHERE id=?',(key,))


def state():
    result={'enabled':bool(settings().get('enabled')),'backend_online':False,'jobs':{},'error':None,'max_batch_images':MAX_BATCH_IMAGES,'batches':[]}
    try:api('/system_stats',timeout=1);result['backend_online']=True
    except Exception:pass
    try:
        db=sqlite3.connect('file:'+str(ROOT/'assistant.sqlite3')+'?mode=ro',uri=True,timeout=1)
        try:
            result['jobs']=dict(db.execute('SELECT state,COUNT(*) FROM local_image_jobs GROUP BY state').fetchall())
            columns={r[1] for r in db.execute('PRAGMA table_info(local_image_jobs)')}
            if 'batch_id' in columns:
                batches=db.execute('''SELECT COALESCE(batch_id,id),MAX(batch_total),
                    SUM(state='outbox_queued'),SUM(state IN ('failed','outcome_uncertain','needs_attention')),
                    SUM(state IN ('queued','starting','submitting','running','ready'))
                    FROM local_image_jobs GROUP BY COALESCE(batch_id,id) ORDER BY MIN(created) DESC LIMIT 3''').fetchall()
                result['batches']=[{'id':b[0],'total':b[1],'queued_to_outbox':b[2],'problems':b[3],'remaining':b[4]} for b in batches]
            row=db.execute('SELECT state,error FROM local_image_jobs WHERE error IS NOT NULL ORDER BY updated DESC LIMIT 1').fetchone()
            if row:result['error']=row[1]
        finally:db.close()
    except sqlite3.Error:pass
    return result
