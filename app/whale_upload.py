"""Paired-device uploads: bounded local files, never executable instructions."""
import hashlib,json,re,time,uuid,shutil,zipfile,io
from pathlib import Path
from xml.etree import ElementTree
from app import whale_channel
from app.resource_storage import resource_dir,media_output_allowed,LOCK

MAX_BYTES=32*1024*1024
from app.media_formats import INBOUND_EXTENSIONS
IMAGE_EXT=INBOUND_EXTENSIONS
VIDEO_EXT={'.mp4','.webm','.mov','.m4v','.mkv'}
AUDIO_EXT={'.wav','.mp3','.m4a','.ogg','.flac','.aac'}


def receive(db,device,message_id,name,digest):
    from app.sdk_receiver import OWNER
    if not whale_channel.settings().get('enabled') or device not in whale_channel.settings().get('devices',[]):raise ValueError('Whale device is not enrolled')
    if not isinstance(message_id,str) or not re.fullmatch(r'[0-9a-f-]{32,36}',message_id):raise ValueError('Stable upload ID required')
    if not isinstance(name,str) or not 1<=len(name)<=160 or re.search(r'[\\/\x00-\x1f\x7f]',name) or name in ('.','..'):raise ValueError('Invalid file name')
    if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):raise ValueError('Invalid file digest')
    whale_channel.schema(db)
    key='whale-in:'+device+':'+message_id
    old=db.execute('SELECT task_id,text,sha256 FROM whale_messages WHERE id=?',(key,)).fetchone()
    if old:
        if old[1]!=name or old[2]!=digest:raise ValueError('Upload ID reused with different file')
        return {'ok':True,'task_id':old[0],'duplicate':True,'id':key}
    spool=(whale_channel.ROOT/'whale-data/uploads'/device/(message_id+'.upload'))
    if spool.is_symlink() or not spool.is_file() or not 0<spool.stat().st_size<=MAX_BYTES:raise ValueError('Upload unavailable or too large')
    raw=spool.read_bytes()
    if len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Upload fingerprint mismatch')
    ext=Path(name).suffix.lower()
    kind='image' if ext in IMAGE_EXT else 'video' if ext in VIDEO_EXT else 'audio' if ext in AUDIO_EXT else 'file'
    if kind=='image' and len(raw)>10*1024*1024:raise ValueError('图片不能超过10MiB')
    if kind=='image':
        from PIL import Image
        from app.media_formats import INBOUND_IMAGE,MAX_PIXELS,open_inbound
        try:
            with open_inbound(raw) as image:
                if image.format not in INBOUND_IMAGE or image.width*image.height>MAX_PIXELS:raise ValueError('Unsupported or oversized image')
                image.verify()
        except (OSError,Image.DecompressionBombError) as exc:
            raise ValueError('图片损坏或格式无法识别') from exc
    task=uuid.uuid4().hex;now=time.time()
    with LOCK:
        directory=resource_dir('media')/task;directory.mkdir(parents=True,exist_ok=False)
        path=directory/('upload'+(ext if re.fullmatch(r'\.[a-z0-9]{1,10}',ext) else '.bin'))
        path.write_bytes(raw)
        message={'kind':kind,'original_kind':kind,'raw_text':'[用户上传文件] '+name,'links':[],
                 'direction':'incoming','transport':'whale','reply_device':device,'whale_message_id':message_id,
                 'association_policy':'media_10s_v1','association_received_at':now,
                 'upload':{'path':str(path),'name':name,'bytes':len(raw),'sha256':digest}}
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',(task,OWNER,now,json.dumps(message,ensure_ascii=False),'media_pending'))
            db.execute('INSERT INTO whale_messages(id,device,role,kind,text,path,sha256,task_id,created) VALUES(?,?,?,?,?,?,?,?,?)',(key,device,'user','image' if kind=='image' else 'file',name,str(path),digest,task,now))
            from app import channels
            channels.observe(db,'whale',device)
    return {'ok':True,'task_id':task,'duplicate':False,'id':key,'kind':kind,'status':'uploaded'}


def document_text(path):
    suffix=path.suffix.lower();limit=20000
    if suffix in ('.txt','.md','.csv','.json','.log','.yaml','.yml','.py','.js','.html','.xml'):
        raw=path.read_bytes()[:200000]
        if b'\x00' in raw:raise ValueError('Binary text file')
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:text=raw.decode('gb18030')
    elif suffix=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(str(path))
        if reader.is_encrypted:raise ValueError('Encrypted PDF')
        text='';total=len(reader.pages)
        for page in reader.pages[:100]:
            text+='\n'+(page.extract_text() or '')
            if len(text)>=limit:break
        if not text.strip():raise ValueError('PDF has no extractable text; scanned pages were not OCRed')
    elif suffix=='.docx':
        with zipfile.ZipFile(path) as archive:
            entry=archive.getinfo('word/document.xml')
            if entry.file_size>4*1024*1024:raise ValueError('Document XML too large')
            xml=ElementTree.fromstring(archive.read(entry))
            text='\n'.join(''.join(p.itertext()) for p in xml.iter() if p.tag.endswith('}p'))
    else:return {'status':'unavailable','reason':'该格式已保存，未自动解析；可由具备文件工具的会话按需读取'}
    return {'status':'done','text':text[:limit],'truncated':len(text)>limit,'untrusted_content':True}


def capture(task,message):
    upload=message.get('upload') or {};path=Path(upload['path']).resolve(strict=True);directory=path.parent
    if directory.name!=task or not media_output_allowed(directory) or not path.name.startswith('upload.'):raise ValueError('Unregistered upload path')
    if not 0<path.stat().st_size<=MAX_BYTES or hashlib.sha256(path.read_bytes()).hexdigest()!=upload.get('sha256'):raise ValueError('Uploaded file changed')
    kind=message['kind']
    file={'path':str(path),'bytes':path.stat().st_size,'sha256':upload['sha256'],'status':'saved','source':'whale_upload','name':upload['name']}
    record={'task_id':task,'kind':kind,'received_at':time.time(),'source':'whale_upload','status':'saved',
            'files':[file],'manifest_path':str(directory/'manifest.json'),'filename':upload['name'],'transcript':None}
    if kind=='image':
        from app.media_capture import save_image
        file=save_image(path.read_bytes(),directory,'image_1');file.update(source='whale_upload',name=upload['name']);record['files']=[file]
    elif kind=='video':
        import subprocess
        from app.audio_sender import ffmpeg_exe
        check=subprocess.run([ffmpeg_exe(),'-v','error','-nostdin','-protocol_whitelist','file,pipe','-i',str(path),'-map','0:v:0','-frames:v','1','-f','null','-'],capture_output=True,timeout=35,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if check.returncode:raise ValueError('No decodable video stream')
        file['decode_verified']=True
    elif kind=='audio':
        from app.video_audio import analyze
        analysis=analyze(record);record['audio_analysis']=analysis
        record['transcript']={'status':'transcribed' if analysis.get('text') else 'failed','text':analysis.get('text','')}
    else:
        try:record['document_analysis']=document_text(path)
        except Exception as exc:record['document_analysis']={'status':'unavailable','reason':str(exc)[:180]}
    return record


def attachment(db,device,seq):
    row=db.execute("SELECT path,sha256,task_id,kind FROM whale_messages WHERE seq=? AND device=? AND role='user' AND kind IN ('image','file')",(int(seq),device)).fetchone()
    if not row:raise ValueError('Attachment unavailable for this device')
    path=Path(row[0]).resolve(strict=True)
    if path.parent.name!=row[2] or not media_output_allowed(path.parent) or not 0<path.stat().st_size<=MAX_BYTES:raise ValueError('Unregistered attachment')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=row[1]:raise ValueError('Attachment changed')
    mime='application/octet-stream'
    if row[3]=='image':
        from PIL import Image
        from app.media_formats import open_inbound
        with open_inbound(raw) as image:
            if image.width*image.height>50000000:raise ValueError('Image too large')
            if image.format=='HEIF':
                output=io.BytesIO();image.convert('RGB').save(output,format='PNG')
                if output.tell()>MAX_BYTES:raise ValueError('Converted image exceeds byte limit')
                return output.getvalue(),'image/png'
            mime={'PNG':'image/png','JPEG':'image/jpeg','GIF':'image/gif','WEBP':'image/webp','BMP':'image/bmp','TIFF':'image/tiff'}.get(image.format,mime)
            image.verify()
    return raw,mime
