"""Save an authenticated incoming IM video using the recipient's attachment key."""
import hashlib,json,re,subprocess,urllib.request
from pathlib import Path
from app.media_capture import ROOT,resource_bytes,validate_video

def download(message_id,directory,group_id=None):
    if not re.fullmatch(r'\d{1,26}',str(message_id)):raise ValueError('Invalid SDK message ID')
    directory=Path(directory).resolve()
    from app.resource_storage import media_output_allowed
    if not media_output_allowed(directory,ROOT):raise ValueError('Output outside approved media directories')
    directory.mkdir(parents=True,exist_ok=True)
    body={'message_id':str(message_id)}
    if group_id is not None:
        if not isinstance(group_id,str) or not re.fullmatch(r'\d{1,26}',group_id):raise ValueError('Invalid group ID')
        body['group_id']=group_id
    request=urllib.request.Request('http://127.0.0.1:8791/resolve-video',
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=20) as response:value=json.load(response)
    if not value.get('ok'):raise ValueError('SDK attachment resolution failed')
    key=value.get('key','')
    if not re.fullmatch(r'[0-9a-fA-F]{32}',key):raise ValueError('Unsupported IM attachment key format')
    # CDN allowlist, redirect restrictions and 64 MiB bound are shared with images.
    data=resource_bytes(None,value['url'])
    encrypted=directory/'incoming-video.encrypted.mp4'
    encrypted.write_bytes(data)
    output=directory/'incoming-video.mp4'
    import imageio_ffmpeg
    result=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-y',
        '-protocol_whitelist','file,pipe','-decryption_key',key,'-i',str(encrypted),'-map','0:v:0','-map','0:a?','-c','copy',str(output)],
        capture_output=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('IM attachment decoding failed')
    if not 0<output.stat().st_size<=64*1024*1024:raise ValueError('Output size invalid')
    validate_video(output)
    return {'path':str(output),'status':'saved','bytes':output.stat().st_size,
        'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'source':'sdk_direct_video',
        'decode_verified':True,'format':'MP4'}
