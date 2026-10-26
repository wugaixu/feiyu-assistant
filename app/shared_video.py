"""Owner-shared video identity, isolated download, comments and frame evidence."""
import json,re,subprocess,sys,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(r'__RUNTIME_ROOT__')
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='shared-video-evidence')
_jobs={}


def identify(page,message):
    from app.upstream_observation import observe
    state=observe(page);video=state.get('video') or {}
    author=message.get('card_author')
    if not author or state.get('verificationRequired') or state.get('loginRequired'):return None
    # Normal DOM reader has overly broad author text on some layouts. Require
    # an exact visible leaf for the card author as a separate attribution check.
    exact=page.evaluate('''author=>[...document.querySelectorAll('a,span,p')].some(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return !e.closest('[data-stack-layer="chat"]')&&r.width>0&&r.height>0&&s.visibility!=='hidden'&&e.children.length===0&&e.textContent.trim().replace(/^@/,'')===author})''',author)
    if not exact or not re.fullmatch(r'\d{15,25}',str(video.get('id',''))):return None
    return {'video_id':video['id'],'url':'https://www.douyin.com/video/'+video['id'],
            'author':author,'description':str(video.get('title') or '')[:600],
            'duration_seconds':video.get('duration'),'source':'owner_shared_normal_player'}


def verify_douk_output(value,directory):
    """Verify existing task-owned outputs without downloading or publishing."""
    if value.get('status')!='saved':return value
    from app.media_capture import validate_video
    if value.get('media_type') in ('image_gallery','live_photo_collection'):
        from PIL import Image
        if not value.get('files'):raise ValueError('Empty collection')
        for file in value['files']:
            output=Path(file['path']).resolve(strict=True)
            if not output.is_relative_to(Path(directory).resolve()):raise ValueError('Unexpected output')
            if value['media_type']=='image_gallery':
                with Image.open(output) as image:image.verify()
            else:
                validate_video(output);file['decode_verified']=True
    else:
        output=Path(value['path']).resolve(strict=True)
        if not output.is_relative_to(Path(directory).resolve()):raise ValueError('Unexpected output')
        validate_video(output);value['decode_verified']=True
    return value


def download(page,identity,directory,work=None,backend=None):
    if backend is None:
        policy=ROOT/'download-policy.json'
        backend=json.loads(policy.read_text(encoding='utf-8')).get('preferred_backend','yt_dlp') if policy.exists() else 'yt_dlp'
    if backend not in ('yt_dlp','f2','douk'):raise ValueError('Unknown download backend')
    if backend=='douk':
        try:
            request={'video_id':identity['video_id'],'directory':str(directory),
                     'expected_images':identity.get('expected_images')}
            result=subprocess.run([sys.executable,'-B','-X','utf8',str(Path(__file__).with_name('douk_downloader_worker.py'))],
                cwd=str(ROOT),input=json.dumps(request),
                capture_output=True,text=True,encoding='utf-8',timeout=130,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            value=json.loads(result.stdout)
            verify_douk_output(value,directory)
            value['backend']='douk'
            return value
        except Exception as exc:
            return {'status':'unavailable','reason':'douk_adapter_failed','error_class':type(exc).__name__,'automatic_retry':False}
    from app.media_capture import allowed_url
    playback=page.evaluate('''author=>{const vs=[...document.querySelectorAll('video')].filter(v=>{for(let p=v.parentElement,i=0;p&&i<9;i++,p=p.parentElement){if(p.querySelectorAll('video').length!==1||p.querySelector('[data-e2e="msg-item-content"]'))break;if(p.innerText?.includes(author))return true;}return false;});return vs.length===1?vs[0].currentSrc:null;}''',identity['author'])
    if not playback or not allowed_url(playback):playback=None
    if work is None and backend=='yt_dlp':
        response=None
        try:
            from app.douyin_read_api import detail
            response=detail(page.context,identity['video_id'])
            if response.status==200:
                value=response.json()
                candidate=value.get('aweme_detail') if isinstance(value,dict) else None
                if isinstance(candidate,dict) and str(candidate.get('aweme_id'))==identity['video_id']:work=candidate
        except Exception:pass
        finally:
            if response is not None:response.dispose()
    request={'video_id':identity['video_id'],'directory':str(directory),
             'user_agent':page.evaluate('navigator.userAgent'),
             'cookies':page.context.cookies('https://www.douyin.com/'),'work':work,'playback_url':playback}
    try:
        interpreter=str(ROOT/'tools'/'f2-env'/'Scripts'/'python.exe') if backend=='f2' else sys.executable
        script='f2_downloader_worker.py' if backend=='f2' else 'downloader_worker.py'
        result=subprocess.run([interpreter,'-B','-X','utf8',str(Path(__file__).with_name(script))],cwd=str(ROOT),
            input=json.dumps(request),capture_output=True,text=True,encoding='utf-8',timeout=22,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        value=json.loads(result.stdout)
        if value.get('status')=='saved':
            from app.media_capture import validate_video
            output=Path(value['path']).resolve(strict=True)
            if not output.is_relative_to(Path(directory).resolve()):raise ValueError('Unexpected downloader output')
            validate_video(output)
            value['decode_verified']=True
        value['detail_source']='existing_session_detail' if work else 'normal_player_url' if playback else 'yt_dlp_native_extractor'
        if backend=='f2':value['detail_source']='f2_existing_browser_session'
        value['backend']=backend
        return value
    except subprocess.TimeoutExpired:return {'status':'unavailable','reason':'downloader_time_budget','automatic_retry':False}
    except Exception:return {'status':'unavailable','reason':'isolated_downloader_failed','automatic_retry':False}


def enrich(db,page,record,message):
    """Comments are independent of file success. Analysis never posts comments."""
    identity=record.get('video') or identify(page,message)
    if not identity:
        record['comments']={'status':'unavailable','reason':'No attributed platform video ID; comments not guessed'}
        return record['comments']
    record['video']=identity
    from app.douyin_comments import fetch
    comments=fetch(page.context,identity['video_id'])
    directory=Path(record['manifest_path']).parent
    comments_path=directory/'comments.json'
    comments_path.write_text(json.dumps(comments,ensure_ascii=False,indent=2),encoding='utf-8')
    record['comments']={'status':comments['status'],'path':str(comments_path),'items':comments.get('items',[]),'untrusted_content':True}
    return record['comments']


def build_frames(source,hint,identity):
    from app.video_evidence import extract
    from app.resource_storage import resource_dir
    result=extract(source,resource_dir('analysis',ROOT),hint)
    if identity:
        from app.douyin_content import transcript_path
        dest=transcript_path(identity['video_id']).parent;dest.mkdir(parents=True,exist_ok=True)
        (dest/'frames-v2.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        from app.douyin_frame_ocr import extract as ocr
        result['ocr']=ocr(identity['video_id'])
    return result


def start_frames(record):
    if record.get('status')=='cleaned' or record.get('shared_video_analysis',{}).get('status')=='cleaned':return
    task=record['task_id']
    if task in _jobs:return
    if record.get('media_type')=='live_photo_collection':
        files=record.get('files',[])
        if not files or not all(f.get('decode_verified') and f.get('status')=='saved' for f in files):return
        _jobs[task]=_pool.submit(build_collection_frames,files,record.get('video'))
        return
    source=next((f['path'] for f in record.get('files',[]) if f.get('decode_verified') and f.get('status')=='saved'),None)
    if not source:return
    hint=(record.get('playback_recording') or {}).get('duration')
    _jobs[task]=_pool.submit(build_frames,source,hint,record.get('video'))


def poll_frames(task):
    job=_jobs.get(task)
    if job is None:return None
    if not job.done():return {'status':'processing'}
    try:
        result=job.result()
        return {'status':'done','frames':result['frames'],'contact_sheet':result['contact_sheet'],
                'subtitle_strip':result.get('subtitle_strip'),'sample_times_seconds':result['sample_times_seconds'],
                'duration_seconds':result['duration_seconds'],'ocr':result.get('ocr'),
                **({k:result[k] for k in ('media_type','segments','clip_count') if k in result}),
                'evidence_limit':'Sampled visual frames and optional OCR, not full transcript or audio understanding'}
    except Exception as exc:return {'status':'unavailable','reason':type(exc).__name__}


def build_collection_frames(files,identity):
    """Bounded samples spanning every clip, preserving portrait/landscape."""
    import hashlib,math
    from PIL import Image,ImageOps,ImageDraw
    from app.video_evidence import extract
    from app.resource_storage import resource_dir
    if not 1<=len(files)<=20:raise ValueError('Invalid live-photo clip count')
    root=resource_dir('analysis',ROOT)
    budget=max(9,len(files));segments=[];frames=[];times=[];offset=0
    for index,file in enumerate(files):
        count=budget//len(files)+(index<budget%len(files))
        evidence=extract(file['path'],root,count=count)
        segments.append({'index':index+1,'source_file':file['path'],'duration_seconds':evidence['duration_seconds'],
            'frames':evidence['frames'],'sample_times_seconds':evidence['sample_times_seconds']})
        frames.extend(evidence['frames']);times.extend(round(offset+t,3) for t in evidence['sample_times_seconds'])
        offset+=evidence['duration_seconds']
    key=hashlib.sha256(json.dumps([str(Path(f['path']).resolve())+':'+str(f.get('sha256')) for f in files]).encode()).hexdigest()
    dest=root/key[:16]/'live-photo-v1';dest.mkdir(parents=True,exist_ok=True)
    sheet=Image.new('RGB',(1440,326*math.ceil(len(frames)/3)),'white');strips=[];position=0
    for segment in segments:
        for frame,t in zip(segment['frames'],segment['sample_times_seconds']):
            with Image.open(frame) as image:
                image=image.convert('RGB');tile=ImageOps.contain(image,(480,300))
                x,y=(position%3)*480,(position//3)*326
                sheet.paste(tile,(x+(480-tile.width)//2,y));ImageDraw.Draw(sheet).text((x+8,y+307),f"Clip {segment['index']} @ {t:.2f}s",fill='black')
                crop=ImageOps.contain(image.crop((0,int(image.height*.55),image.width,int(image.height*.98))),(960,320))
                strip=Image.new('RGB',(960,crop.height+24),'white');strip.paste(crop,(0,24));ImageDraw.Draw(strip).text((8,4),f"Clip {segment['index']} @ {t:.2f}s",fill='black');strips.append(strip)
            position+=1
    contact=dest/'contact-sheet.jpg';sheet.save(contact,quality=88)
    subtitles=Image.new('RGB',(960,sum(s.height for s in strips)),'white');y=0
    for strip in strips:subtitles.paste(strip,(0,y));y+=strip.height
    subtitle_path=dest/'subtitle-strip.jpg';subtitles.save(subtitle_path,quality=90)
    result={'media_type':'live_photo_collection','clip_count':len(files),'segments':segments,'frames':frames,
        'contact_sheet':str(contact),'subtitle_strip':str(subtitle_path),'duration_seconds':round(offset,3),'sample_times_seconds':times}
    if identity:
        from app.douyin_content import transcript_path
        directory=transcript_path(identity['video_id']).parent;directory.mkdir(parents=True,exist_ok=True)
        (directory/'frames-v2.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        from app.douyin_frame_ocr import extract as ocr
        result['ocr']=ocr(identity['video_id'])
    (dest/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
