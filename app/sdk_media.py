"""Save resources from authenticated SDK events; never substitute thumbnails for videos."""
import hashlib,json,re,shutil,subprocess,time
from pathlib import Path
from app.media_capture import ROOT,resource_bytes,save_image

NODE_EXE=shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
DECRYPT_HELPER=Path(__file__).resolve().parents[1]/'decrypt_image.mjs'

def decrypt_image_bytes(data,skey,directory):
    """SDK image messages are AES-256-GCM sealed (12-byte IV + tag). Decrypt via Node."""
    if not re.fullmatch(r'[0-9a-fA-F]{64}',str(skey or '')):raise ValueError('Invalid SDK image key')
    source=directory/'encrypted-image.bin';target=directory/'decrypted-image.bin'
    source.write_bytes(data)
    try:
        result=subprocess.run([NODE_EXE,str(DECRYPT_HELPER),str(source),str(skey),str(target)],
            capture_output=True,text=True,timeout=30,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode!=0:raise ValueError('Image decryption failed: '+(result.stderr or '')[-300:])
        return target.read_bytes()
    finally:
        for path in (source,target):
            try:path.unlink()
            except OSError:pass

def capture(page,task,message):
    from app.resource_storage import resource_dir
    directory=resource_dir('media',ROOT)/task;directory.mkdir(parents=True,exist_ok=True)
    from app.sdk_share import normalize
    kind=message['kind'];content=normalize(message['sdk_content'])
    if kind=='sdk_message' and content.get('kind')=='share':kind='video_card'
    record={'task_id':task,'kind':kind,'received_at':time.time(),'files':[],
        'status':'unavailable','failure_reason':None,'transcript':None,
        'manifest_path':str(directory/'manifest.json'),'source':'sdk'}
    try:
        if kind=='video_card':
            share=content.get('share') or {};video_id=str(share.get('itemId',''))
            if not re.fullmatch(r'\d{15,25}',video_id):raise ValueError('No valid shared work ID')
            value=content.get('value') or {}
            declared=value.get('image_count') if isinstance(value,dict) else None
            if not (isinstance(declared,int) and not isinstance(declared,bool) and 1<=declared<=20):declared=None
            identity={'video_id':video_id,'url':'https://www.douyin.com/video/'+video_id,
                'author':share.get('authorUid',''),'description':share.get('title',''),'source':'sdk_shared_work',
                'expected_images':declared}
            record['video']=identity
            from app.shared_video import download
            result=download(None,identity,directory,backend='douk');record['downloader']=result
            if result.get('status')!='saved':raise ValueError('DouK download unavailable')
            if result.get('media_type') in ('image_gallery','live_photo_collection'):
                record.update(media_type=result['media_type'],files=result['files'])
                for key in ('image_count','clip_count','expected_images','partial','completeness','douk_timeout'):
                    if key in result:record[key]=result[key]
                raw=content.get('value') or {}
                expected=raw.get('image_count') if isinstance(raw,dict) and raw.get('is_live_photo') else None
                if result['media_type']=='live_photo_collection' and isinstance(expected,int) and 1<=expected<=20:
                    record['expected_clip_count']=expected
                    if expected!=result['clip_count']:raise ValueError('Live-photo clip count differs from shared card')
            else:record['files']=[result]
        elif kind=='video':
            from app.sdk_video import download as download_video
            kwargs={'group_id':message['group_id']} if message.get('is_group') else {}
            record['files']=[download_video(message.get('sdk_message_id'),directory,**kwargs)]
            record['media_type']='video'
        elif kind=='image':
            image=content.get('image') or {}
            urls=sum([image.get(k,[]) for k in ('originUrls','largeUrls','mediumUrls','thumbUrls')],[])
            if content.get('kind')=='emoji':urls=[content.get('url')]
            if not urls:raise ValueError('No SDK image URL')
            data=resource_bytes(None,urls[0])
            skey=str(image.get('skey') or '')
            if re.fullmatch(r'[0-9a-fA-F]{64}',skey):
                data=decrypt_image_bytes(data,skey,directory)
                expected=image.get('dataSize')
                if isinstance(expected,int) and expected>0 and len(data)!=expected:
                    raise ValueError('Decrypted image size differs from SDK declaration')
            record['files']=[save_image(data,directory,'image_1')]
        elif kind=='audio':
            urls=(content.get('audio') or {}).get('urls') or []
            if not urls:raise ValueError('No SDK audio URL')
            data=resource_bytes(None,urls[0]);path=directory/'voice.bin';path.write_bytes(data)
            record['files']=[{'path':str(path),'status':'saved','bytes':len(data),
                'sha256':hashlib.sha256(data).hexdigest(),'source':'sdk_audio'}]
            # Use the same bounded offline engine/cache as video audio. Keep the
            # original even when decoding or recognition fails; never invent text.
            from app.video_audio import analyze
            try:
                analysis=analyze(record)
            except Exception as exc:
                analysis={'status':'failed','reason':str(exc)[:200],'automatic_retry':False}
            record['audio_analysis']=analysis
            for file in record['files']:
                if any(c.get('audio_file') for c in analysis.get('clips',[])):
                    file['decode_verified']=True
            record['transcript']={
                'status':'transcribed' if analysis.get('status')=='done' and analysis.get('text') else analysis.get('status','failed'),
                'text':analysis.get('text',''),'engine':analysis.get('engine',''),
                'reason':analysis.get('reason',''),'path':analysis.get('path',''),
                'text_path':analysis.get('text_path',''),
                'uncertainty':'Automatic recognition may be inaccurate; not additional authorization.'}
        else:raise ValueError('SDK direct video resource needs separate media acceptance; no poster substitution')
        record['status']='saved'
    except Exception as exc:record['failure_reason']=str(exc)[:250]
    Path(record['manifest_path']).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record
