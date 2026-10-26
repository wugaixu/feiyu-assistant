"""Save normal UI media resources. Never uses private application state/APIs."""
import base64
import hashlib
import io
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image

ROOT = Path(r'__RUNTIME_ROOT__')
LIMIT = 64 * 1024 * 1024
SUFFIXES = ('douyinpic.com', 'douyincdn.com', 'douyinvod.com', 'douyinaudio.com',
            'douyinstatic.com', 'byteimg.com', 'bytecdn.cn', 'bytedance.com', 'pstatp.com')


def allowed_url(url):
    p = urlsplit(url)
    return p.scheme == 'https' and not p.username and not p.password and p.port in (None, 443) and any(
        p.hostname == s or (p.hostname or '').endswith('.' + s) for s in SUFFIXES)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError('Resource redirect outside permitted CDN domains')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def resource_bytes(page, url):
    if url.startswith('data:image/'):
        header, encoded = url.split(',', 1)
        if ';base64' not in header or len(encoded) > LIMIT * 2:
            raise ValueError('Unsupported or oversized inline image')
        data = base64.b64decode(encoded, validate=True)
    elif url.startswith('blob:https://www.douyin.com/'):
        encoded = page.evaluate('''async ({url,limit})=>{
            const r=await fetch(url); const b=await r.blob();
            if(b.size>limit) throw Error('Media exceeds byte limit');
            return await new Promise((resolve,reject)=>{const f=new FileReader();f.onload=()=>resolve(f.result.split(',')[1]);f.onerror=reject;f.readAsDataURL(b);});
        }''', {'url': url, 'limit': LIMIT})
        data = base64.b64decode(encoded)
    else:
        if not allowed_url(url):
            raise ValueError('No permitted normal CDN resource URL')
        opener = urllib.request.build_opener(SafeRedirect())
        request = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0', 'Referer':'https://www.douyin.com/'})
        with opener.open(request, timeout=15) as response:
            data = response.read(LIMIT + 1)
    if not data or len(data) > LIMIT:
        raise ValueError('Empty media or exceeds 64 MiB limit')
    return data


def save_image(data, directory, name):
    from app import media_formats as formats
    with Image.open(io.BytesIO(data)) as img:
        if img.width * img.height > formats.MAX_PIXELS:
            raise ValueError('Image exceeds 50 megapixels')
        fmt, size = img.format, list(img.size)
        img.verify()
    if fmt not in formats.INBOUND_IMAGE:
        raise ValueError('Unsupported image format')
    ext = formats.save_suffix(fmt)
    if not ext:
        raise ValueError('Unsupported image format')
    path = directory / (name + ext)
    if formats.stored_as_is(fmt):
        path.write_bytes(data)
    else:
        # BMP/TIFF land as PNG so stored media matches what can be sent back.
        with Image.open(io.BytesIO(data)) as img:
            img.convert('RGB').save(path, 'PNG')
    raw = path.read_bytes()
    return {'path':str(path), 'status':'saved', 'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest(),
            'format':fmt, 'size':size, 'source':'normal_image_url'}


def decode_audio(source, directory):
    import imageio_ffmpeg
    wav = directory / 'voice.wav'
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner', '-n',
                             '-i',str(source),'-t','120','-ar','16000','-ac','1',str(wav)],
                            capture_output=True, timeout=30, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('Audio decoding failed: ' + result.stderr.decode('utf-8','replace')[-500:])
    return str(wav)


def validate_video(path):
    import imageio_ffmpeg
    decoded=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-i',str(path),'-map','0:v:0','-t','0.2','-f','null','-'],
                           capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if decoded.returncode:
        raise ValueError('File lacks a decodable video track; not treating it as usable video')


def transcript_from_visible(text):
    text = (text or '').strip()
    if not text or text in ('[未识别到文字]','未识别到文字','正在转文字','转文字中'):
        return {'status':'no_speech_recognized','text':'','reason':text or 'No displayed transcript'}
    return {'status':'transcribed','text':text,'engine':'Douyin normal UI 转文字',
            'uncertainty':'Automatic transcription can be inaccurate; not a permission grant.'}


def transcribe_via_ui(page, bubble):
    displayed=bubble.locator('.MessageItemAudiovoiceText')
    if not displayed.count():
        bubble.locator('.MessageItemAudioaudioBoxWrapper').click(button='right',timeout=5000)
        control=page.get_by_text('转文字',exact=True)
        if control.count()!=1:
            page.keyboard.press('Escape')
            return {'status':'unavailable','reason':'Normal UI 转文字 action missing or ambiguous'}
        control.click(timeout=5000)
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            if displayed.count() and displayed.inner_text().strip() not in ('','正在转文字','转文字中'):
                break
            page.wait_for_timeout(500)
    result=transcript_from_visible(displayed.inner_text() if displayed.count() else '')
    return result


PLAY_HOOK = '''() => {
    if(window.__localAssistantMedia?.version===4) return;
    const old=window.__localAssistantMedia;
    window.__localAssistantMedia={version:4,events:old?.events||[],buffers:[]};
    if(window.AudioBufferSourceNode){
        const start=AudioBufferSourceNode.prototype.start;
        AudioBufferSourceNode.prototype.start=function(...args){
            if(this.buffer){window.__localAssistantMedia.buffers.push({buffer:this.buffer,at:Date.now()});window.__localAssistantMedia.buffers=window.__localAssistantMedia.buffers.slice(-8);}
            return Reflect.apply(start,this,args);
        };
    }
    const track=(m,url)=>{
        window.__localAssistantMedia.events.push({kind:m.tagName.toLowerCase(),url:url||m.currentSrc||m.src,element:m,at:Date.now()});
        window.__localAssistantMedia.events=window.__localAssistantMedia.events.slice(-40);
    };
    const src=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'src');
    if(src?.set) Object.defineProperty(HTMLMediaElement.prototype,'src',{...src,set(value){track(this,value);return src.set.call(this,value);}});
    const NativeAudio=window.Audio;
    if(NativeAudio){
        function TrackedAudio(...args){const a=Reflect.construct(NativeAudio,args,NativeAudio);track(a,args[0]);return a;}
        Object.setPrototypeOf(TrackedAudio,NativeAudio);TrackedAudio.prototype=NativeAudio.prototype;window.Audio=TrackedAudio;
    }
    const original=HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play=function(...args){
        track(this);
        return Reflect.apply(original,this,args);
    };
}'''


def playback_pcm(page, started, duration_text):
    duration=float(re.match(r'^\d+',duration_text or '').group(0)) if re.match(r'^\d+',duration_text or '') else 0
    return page.evaluate('''async ({started,expected,limit})=>{
        const unique=[...new Set(window.__localAssistantMedia.buffers.filter(e=>e.at>=started).map(e=>e.buffer))];
        const candidates=unique.filter(b=>b.duration>0 && b.duration<=120 && (!expected||Math.abs(b.duration-expected)<1.5));
        if(candidates.length!==1) throw Error('No unique normal Web Audio playback buffer for this voice');
        const b=candidates[0],size=44+b.length*2;
        if(size>limit)throw Error('Playback audio exceeds byte limit');
        const bytes=new ArrayBuffer(size),v=new DataView(bytes);
        const tag=(p,s)=>{for(let i=0;i<s.length;i++)v.setUint8(p+i,s.charCodeAt(i));};
        tag(0,'RIFF');v.setUint32(4,size-8,true);tag(8,'WAVE');tag(12,'fmt ');v.setUint32(16,16,true);
        v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,b.sampleRate,true);v.setUint32(28,b.sampleRate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);tag(36,'data');v.setUint32(40,b.length*2,true);
        const channels=Array.from({length:b.numberOfChannels},(_,i)=>b.getChannelData(i));
        for(let i=0;i<b.length;i++){let s=0;for(const c of channels)s+=c[i]/channels.length;s=Math.max(-1,Math.min(1,s));v.setInt16(44+i*2,s<0?s*32768:s*32767,true);}
        const encoded=await new Promise((res,rej)=>{const f=new FileReader();f.onload=()=>res(f.result.slice(f.result.indexOf(';base64,')+8));f.onerror=rej;f.readAsDataURL(new Blob([bytes],{type:'audio/wav'}));});
        return {encoded,duration:b.duration,sample_rate:b.sampleRate};
    }''',{'started':started,'expected':duration,'limit':LIMIT})


def record_visible_video(page, author):
    if not author:
        raise ValueError('Cannot attribute player without visible card author')
    return page.evaluate('''async ({author,limit})=>{
        const candidates=[...document.querySelectorAll('video')].filter(v=>{
            if(!Number.isFinite(v.duration)||v.duration<=0||v.videoWidth<=0) return false;
            for(let p=v.parentElement,i=0;p&&i<9;i++,p=p.parentElement){
                if(p.querySelectorAll('video').length!==1||p.querySelector('[data-e2e="msg-item-content"]')) break;
                if(p.innerText?.includes(author)) return true;
            }
            return false;
        });
        if(candidates.length!==1) throw Error('Correct video player missing or ambiguous');
        const v=candidates[0];
        if(v.mediaKeys) throw Error('Protected media is not captured');
        if(v.duration>90) throw Error('Playback recording limited to videos at most 90 seconds');
        const duration=v.duration;
        v.currentTime=0;
        await new Promise(r=>setTimeout(r,500));
        await v.play();
        const stream=v.captureStream();
        if(!stream.getVideoTracks().length) {v.pause();throw Error('Player exposes no capturable video track');}
        const mime=['video/webm;codecs=vp8,opus','video/webm'].find(t=>MediaRecorder.isTypeSupported(t));
        if(!mime) {v.pause();throw Error('No supported normal playback recording format');}
        return await new Promise((resolve,reject)=>{
            const recorder=new MediaRecorder(stream,{mimeType:mime}); const chunks=[];let bytes=0,error=null;
            const stop=()=>{if(recorder.state!=='inactive')recorder.stop();};
            const timer=setTimeout(()=>{error='Playback recording timed out';stop();},(duration+8)*1000);
            const ended=()=>stop();v.addEventListener('ended',ended,{once:true});
            recorder.ondataavailable=e=>{bytes+=e.data.size;if(bytes>limit){error='Playback recording exceeds byte limit';stop();}else chunks.push(e.data);};
            recorder.onerror=()=>{error='MediaRecorder failed';stop();};
            recorder.onstop=async()=>{
                clearTimeout(timer);v.removeEventListener('ended',ended);v.pause();stream.getTracks().forEach(t=>t.stop());
                if(error){reject(Error(error));return;}
                const blob=new Blob(chunks,{type:mime});
                const base64=await new Promise((res,rej)=>{const f=new FileReader();f.onload=()=>res(f.result.slice(f.result.indexOf(';base64,')+8));f.onerror=rej;f.readAsDataURL(blob);});
                resolve({base64,mime,duration,width:v.videoWidth,height:v.videoHeight,author});
            };
            recorder.start(1000);
        });
    }''',{'author':author,'limit':LIMIT})


def capture(page, task_id, message):
    if not re.fullmatch('[0-9a-f]{32}', task_id):
        raise ValueError('Local UUID required')
    from app.resource_storage import resource_dir
    directory = resource_dir('media',ROOT) / task_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / 'manifest.json'
    if manifest.exists():
        return json.loads(manifest.read_text(encoding='utf-8'))
    record = {'task_id':task_id, 'kind':message.get('kind'), 'received_at':time.time(), 'files':[],
              'status':'unavailable','failure_reason':None,'transcript':None,'manifest_path':str(manifest)}
    responses = []
    works = []
    played_sources = []
    bubble = None
    scroll = page.locator('[data-stack-layer="chat"] .messageMessageListlist')
    initial_scroll = scroll.evaluate('(e)=>e.scrollTop') if scroll.count()==1 else None
    def on_response(response):
        parsed=urlsplit(response.url)
        if parsed.hostname=='www.douyin.com' and parsed.path=='/aweme/v1/web/aweme/detail/':
            try:
                value=response.json().get('aweme_detail')
                if isinstance(value,dict):works.append(value)
            except Exception:pass
        content_type = response.headers.get('content-type','').split(';')[0]
        if content_type.startswith(('video/','audio/')) and allowed_url(response.url):
            responses.append({'url':response.url,'type':content_type})
    try:
        if message.get('kind') == 'image':
            for i, image in enumerate(message.get('images', [])[:8]):
                record['files'].append(save_image(resource_bytes(page,image['url']), directory, 'image_'+str(i+1)))
            if not record['files']:
                raise ValueError('Image URL is not available in the visible message DOM')
            record['status'] = 'saved'
        else:
            # Locate only the exact visible bubble observed in this verified chat.
            bubbles = page.locator('[data-stack-layer="chat"] [data-e2e="msg-item-content"]')
            exact_dom = message.get('dom')
            indices = bubbles.evaluate_all('(nodes,dom)=>nodes.map((n,i)=>n.outerHTML===dom?i:-1).filter(i=>i>=0)', exact_dom)
            if not indices:
                indices=bubbles.evaluate_all('''(nodes,m)=>nodes.map((n,i)=>{
                    if(n.closest('.messageMessageBoxisFromMe')) return -1;
                    if(m.kind==='video_card') return n.querySelector('.MessageItemShareAwemecontainer,.BulletBulletVideocontainer') && n.querySelector('.MessageItemShareAwemeauthorName,.BulletBulletVideoauthorName')?.textContent===m.card_author && n.innerText===m.raw_text ? i:-1;
                    if(m.kind==='audio') return n.querySelector('.MessageItemAudioduration')?.textContent===(m.audio_duration||m.raw_text.split('\\n')[0]) ? i:-1;
                    return -1;
                }).filter(i=>i>=0)''',message)
            if len(indices) != 1:
                raise ValueError('Media bubble is missing or ambiguous; not clicking')
            bubble = bubbles.nth(indices[0])
            editor = page.locator('[data-e2e="msg-input"] [contenteditable="true"]')
            if editor.count() == 1 and editor.inner_text().strip('\u200b \n'):
                raise ValueError('User draft present; media playback capture deferred')
            if message['kind'] == 'audio':
                try:
                    record['transcript'] = transcribe_via_ui(page,bubble)
                except Exception as exc:
                    record['transcript'] = {'status':'failed','reason':str(exc)}
            page.evaluate(PLAY_HOOK)
            page.context.on('response', on_response)
            started = page.evaluate('Date.now()')
            if message['kind'] == 'audio':
                bubble.locator('.MessageItemAudioaudioBox').click(timeout=5000)
            else:
                bubble.click(timeout=5000)
            page.wait_for_timeout(3500)
            events = page.evaluate('(start)=>window.__localAssistantMedia.events.filter(e=>e.at>=start).map(e=>({kind:e.kind,url:e.element?.currentSrc||e.element?.src||e.url,at:e.at}))',started)
            if message['kind']=='audio' and not any(e['kind']=='audio' and e.get('url') for e in events) and not responses:
                # A reused player may pause on the first click. One bounded second
                # click is allowed; this is playback only, never a send retry.
                bubble.locator('.MessageItemAudioaudioBox').click(timeout=5000)
                page.wait_for_timeout(1500)
                events = page.evaluate('(start)=>window.__localAssistantMedia.events.filter(e=>e.at>=start).map(e=>({kind:e.kind,url:e.element?.currentSrc||e.element?.src||e.url,at:e.at}))',started)
            kind = 'audio' if message['kind'] == 'audio' else 'video'
            sources = [e['url'] for e in events if e['kind'] == kind and e.get('url')]
            played_sources = sources[:]
            if kind == 'video':
                # Network media responses may belong to other open players. Never
                # infer video identity from a response arriving near the click.
                from app.shared_video import identify,download
                identity=identify(page,message)
                downloaded={'status':'unavailable','reason':'Platform video identity unavailable'}
                if identity:
                    record['video']=identity
                    work=next((w for w in works if str(w.get('aweme_id'))==identity['video_id']),None)
                    downloaded=download(page,identity,directory,work)
                record['downloader']=downloaded
                if downloaded.get('status')=='saved' and downloaded.get('media_type')=='image_gallery':
                    record.update(status='saved',media_type='image_gallery',files=downloaded['files'],image_count=downloaded['image_count'])
                    manifest.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
                    return record
                if downloaded.get('status')=='saved':
                    path=Path(downloaded['path']);data=path.read_bytes()
                else:
                    recorded=record_visible_video(page,message.get('card_author'))
                    data=base64.b64decode(recorded.pop('base64'))
                    path=directory/'video-playback.webm'
                    record['playback_recording']=recorded
            else:
                sources = [s for s in sources if not s.startswith('blob:')] + [r['url'] for r in responses if r['type'].startswith('audio/')]
                if sources:
                    data = resource_bytes(page, sources[0])
                    path = directory / 'voice.bin'
                else:
                    captured=playback_pcm(page,started,message.get('audio_duration'))
                    data=base64.b64decode(captured.pop('encoded'))
                    record['audio_playback_capture']=captured
                    path=directory/'voice-playback.wav'
            path.write_bytes(data)
            downloader=record.get('downloader') or {}
            # Name the real acquisition route. The old label always said yt_dlp
            # whenever any downloader succeeded, which misreported DouK files.
            if kind=='video':
                source=downloader.get('backend','yt_dlp') if downloader.get('status')=='saved' else 'normal_playback_recording'
            else:
                source='normal_web_audio_playback_pcm' if record.get('audio_playback_capture') else 'normal_playback_url'
            record['files'].append({'path':str(path),'status':'saved','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'source':source})
            record['status'] = 'saved'
            if kind == 'audio':
                try:
                    wav_path=decode_audio(path, directory)
                except Exception as exc:
                    record['decode_failure'] = str(exc)
                    if not record.get('transcript'):
                        try:
                            record['transcript']=transcribe_via_ui(page,bubble)
                        except Exception as fallback:
                            record['transcript']={'status':'failed','reason':str(exc),'fallback_reason':str(fallback)}
                wav = directory/'voice.wav'
                if wav.exists():
                    wav_data=wav.read_bytes()
                    record['files'].append({'path':str(wav),'status':'saved','format':'WAV','decode_verified':True,
                                            'bytes':len(wav_data),'sha256':hashlib.sha256(wav_data).hexdigest(),'source':'decoded_audio'})
            else:
                try:
                    validate_video(path)
                except Exception:
                    record['files'][-1]['status']='decode_failed'
                    raise
                record['files'][-1]['decode_verified'] = True
    except Exception as exc:
        record['failure_reason'] = str(exc)
        if message.get('kind')=='audio' and bubble is not None and not record.get('transcript'):
            try:
                record['transcript']=transcribe_via_ui(page,bubble)
            except Exception as fallback:
                record['transcript']={'status':'failed','reason':str(fallback)}
        if record['files']:
            record['status'] = 'partial'
    finally:
        page.context.remove_listener('response', on_response)
        if played_sources:
            page.evaluate('''sources=>{for(const m of document.querySelectorAll('audio,video')){
                if(sources.includes(m.currentSrc||m.src)) m.pause();}}''',played_sources)
        if initial_scroll is not None and scroll.count()==1:
            scroll.evaluate('(e,top)=>{e.scrollTop=top}',initial_scroll)
    if record.get('transcript'):
        (directory/'transcript.json').write_text(json.dumps(record['transcript'],ensure_ascii=False,indent=2),encoding='utf-8')
    manifest.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record
