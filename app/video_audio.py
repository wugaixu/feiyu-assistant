"""Bounded, asynchronous local SenseVoice evidence for new video messages."""
import hashlib,json,shutil,subprocess,wave
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import imageio_ffmpeg

ROOT=Path(r'__RUNTIME_ROOT__')
RUNTIME=ROOT/'tools/sensevoice'
ENGINE='sensevoice-small-onnx-int8-v1'
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='video-audio')
_jobs={}
FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)
LIMIT_SECONDS=600


def atomic(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


def analyze(record):
    from app.resource_storage import resource_dir,media_output_allowed
    directory=Path(record['manifest_path']).parent.resolve()
    if not media_output_allowed(directory,ROOT):raise ValueError('Unapproved media directory')
    dest=directory/'audio';dest.mkdir(parents=True,exist_ok=True)
    results=[];uncached=[];remaining=LIMIT_SECONDS
    for index,file in enumerate(record['files'],1):
        source=Path(file['path']).resolve(strict=True)
        if not source.is_relative_to(directory) or source.is_symlink():raise ValueError('Audio source outside task directory')
        if remaining<=0:
            results.append({'clip_index':index,'status':'skipped_limit','reason':'Total video audio budget is 600 seconds'});continue
        wav=dest/f'clip_{index:02}.wav'
        decoded=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-y','-i',str(source),'-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le','-t',str(remaining+.1),str(wav)],capture_output=True,timeout=45,creationflags=FLAGS)
        if decoded.returncode:
            error=decoded.stderr.decode('utf-8','replace')
            no_audio='matches no streams' in error or 'does not contain any stream' in error
            results.append({'clip_index':index,'source_file':str(source),'status':'no_audio' if no_audio else 'failed','reason':'No audio track' if no_audio else 'Audio decode failed'})
            continue
        with wave.open(str(wav),'rb') as stream:duration=stream.getnframes()/stream.getframerate()
        truncated=duration>remaining;remaining-=duration
        with wav.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        cache=resource_dir('analysis',ROOT)/digest[:16]/ENGINE/'transcript.json'
        cache.parent.mkdir(parents=True,exist_ok=True)
        item={'clip_index':index,'source_file':str(source),'audio_file':str(wav),'audio_sha256':digest,'duration_seconds':duration,'truncated':truncated,'cache_file':str(cache)}
        cached=None
        if cache.is_file():
            try:cached=json.loads(cache.read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
        if cached and cached.get('engine')==ENGINE and cached.get('audio_sha256')==digest and isinstance(cached.get('segments'),list):
            item.update(status=cached['status'],segments=cached['segments'],cache_hit=True)
        else:
            item['status']='pending';uncached.append(item)
        results.append(item)
    if uncached:
        node=shutil.which('node')
        if not node:raise RuntimeError('Node.js unavailable for local SenseVoice')
        response=subprocess.run([node,str(Path(__file__).with_name('sensevoice_worker.cjs'))],input=json.dumps({'runtime':str(RUNTIME),'files':[r['audio_file'] for r in uncached]}),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180,creationflags=FLAGS)
        if response.returncode:
            (dest/'inference-error.log').write_text(response.stderr[-8000:],encoding='utf-8')
            raise RuntimeError('SenseVoice local inference failed; see audio/inference-error.log')
        value=json.loads(response.stdout)
        if value.get('ok') is not True or len(value.get('clips',[]))!=len(uncached):raise ValueError('Invalid speech worker response')
        for item,clip in zip(uncached,value['clips']):
            if clip.get('file')!=item['audio_file'] or not isinstance(clip.get('segments'),list):raise ValueError('Speech clip response mismatch')
            segments=clip['segments']
            status='done' if any(s.get('text','').strip() for s in segments) else 'no_speech_recognized'
            item.update(status=status,segments=segments,cache_hit=False)
            atomic(Path(item['cache_file']),{'engine':ENGINE,'audio_sha256':item['audio_sha256'],'status':status,'segments':segments})
    successes=[r for r in results if r['status'] in ('done','no_speech_recognized')]
    text='\n'.join(f"[片段{r['clip_index']} {s['start']:.1f}–{s['end']:.1f}秒] {s['text']}" for r in successes for s in r.get('segments',[]) if s.get('text'))
    if any(r['status'] in ('failed','skipped_limit') or r.get('truncated') for r in results):status='partial' if successes else 'failed'
    elif text:status='done'
    elif successes:status='no_speech_recognized'
    else:status='no_audio'
    report={'status':status,'engine':ENGINE,'text':text,'clips':results,
        'evidence_limit':'Automatic recognition; chunk time ranges, not word alignment. Music lyrics and narration may mix. Emotion/event tags are model estimates.'}
    output=dest/'transcript.json';atomic(output,report)
    plain=dest/'transcript.txt';plain.write_text(text,encoding='utf-8')
    return {**report,'path':str(output),'text_path':str(plain)}


def start(record):
    if record.get('status')=='cleaned':return {'status':'cleaned'}
    if record.get('media_type')=='image_gallery':return {'status':'not_applicable','reason':'Still image gallery'}
    files=[f for f in record.get('files',[]) if f.get('status')=='saved' and f.get('decode_verified')]
    if record.get('status')!='saved' or not files:return {'status':'unavailable','reason':'No verified saved video'}
    task=record['task_id']
    if task not in _jobs:_jobs[task]=_pool.submit(analyze,{**record,'files':files})
    return poll(task)


def poll(task):
    job=_jobs.get(task)
    if job is None:return None
    if not job.done():return {'status':'processing','engine':ENGINE}
    try:return job.result()
    except Exception as exc:return {'status':'failed','engine':ENGINE,'reason':str(exc)[:200],'automatic_retry':False}
