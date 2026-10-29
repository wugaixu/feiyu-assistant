"""Owner-only OneBot 11 adapter. All AI work and delivery use the common queue."""
import hashlib
import hmac
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(r'__RUNTIME_ROOT__')
MAX_EVENT = 1024 * 1024
MAX_FILE = 32 * 1024 * 1024


def read_event(handler):
    """Node's native HTTP client uses chunked transfer, with no Content-Length."""
    encoding=handler.headers.get('Transfer-Encoding','').lower().strip()
    length=handler.headers.get('Content-Length')
    if not encoding:
        count=int(length or 0)
        if not 0<count<=MAX_EVENT:raise ValueError('QQ event size denied')
        body=handler.rfile.read(count)
        if len(body)!=count:raise ValueError('Truncated QQ event')
        return body
    if encoding!='chunked' or length is not None:raise ValueError('Ambiguous QQ event framing')
    parts=[];total=0
    while True:
        line=handler.rfile.readline(128)
        if not re.fullmatch(rb'[0-9a-fA-F]{1,8}\r\n',line):raise ValueError('Invalid QQ event chunk')
        size=int(line.strip(),16)
        if size==0:
            for _ in range(16):
                trailer=handler.rfile.readline(1024)
                if trailer==b'\r\n':return b''.join(parts)
                if not trailer.endswith(b'\r\n'):break
            raise ValueError('Invalid QQ event trailer')
        total+=size
        if total>MAX_EVENT:raise ValueError('QQ event size denied')
        part=handler.rfile.read(size)
        if len(part)!=size or handler.rfile.read(2)!=b'\r\n':raise ValueError('Truncated QQ event chunk')
        parts.append(part)


def settings():
    try: return json.loads((ROOT/'qq-channel.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): return {'enabled': False, 'owner_id': '', 'account_id': '', 'port': 8796}


def qq_id(value):
    value = str(value)
    if not re.fullmatch(r'[1-9][0-9]{4,14}', value): raise ValueError('有效的 QQ 号不能为空')
    return value


def route(config=None):
    value = config or settings()
    return 'qq:'+qq_id(value.get('account_id'))+':'+qq_id(value.get('owner_id'))


def permitted(device):
    try: return settings().get('enabled') is True and hmac.compare_digest(str(device), route())
    except ValueError: return False


def signature_ok(raw, signature):
    try: secret = (ROOT/'qq-onebot-secret.txt').read_bytes().strip()
    except OSError: return False
    expected = 'sha1='+hmac.new(secret, raw, hashlib.sha1).hexdigest()
    return bool(secret) and isinstance(signature, str) and hmac.compare_digest(signature, expected)


def api(action, body=None, timeout=8):
    if not re.fullmatch(r'[a-z_]+', action): raise ValueError('Invalid OneBot action')
    config = settings()
    port = int(config.get('port', 8796))
    if not 1024 <= port <= 65535: raise ValueError('Invalid OneBot port')
    token = (ROOT/'qq-onebot-secret.txt').read_text(encoding='utf-8').strip()
    request = urllib.request.Request(f'http://127.0.0.1:{port}/{action}',
        data=json.dumps(body or {}).encode(), headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    # Local OneBot never inherits a system HTTP proxy or follows redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    limit=MAX_FILE*2 if action in ('get_record','get_image','get_file') else MAX_EVENT
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(limit+1)
    if len(raw)>limit: raise ValueError('OneBot response too large')
    result = json.loads(raw)
    if result.get('status') != 'ok' or result.get('retcode') != 0: raise ValueError('OneBot action rejected')
    return result.get('data') or {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise ValueError('Redirect refused')


def health():
    try:
        info = api('get_login_info', timeout=2)
        account = qq_id(info.get('user_id'))
        config = settings()
        return {'online':True,'account_id':account,'bound':account==str(config.get('account_id')),
                'nickname':str(info.get('nickname') or '')[:80]}
    except Exception: return {'online':False,'bound':False}


def state():
    config = settings()
    return {'enabled':bool(config.get('enabled')),'owner_id':config.get('owner_id',''),
            'account_id':config.get('account_id',''),'health':health(),
            'login_url':'http://127.0.0.1:6099/webui',
            'note':'仅主账号 QQ 私聊；群聊和陌生人不接入。与三种智能体共用十秒合并、资料包和回复队列。'}


def configure(db, body):
    config = settings()
    if body.get('action') == 'open_login':
        start()
        # Open only locally; the private WebUI token never enters API output.
        token = (ROOT/'qq-webui-secret.txt').read_text(encoding='utf-8').strip()
        from urllib.parse import quote
        os.startfile('http://127.0.0.1:6099/webui?token='+quote(token, safe=''))
    elif body.get('action') == 'bind':
        status = health()
        if not status.get('online'): raise ValueError('请先在 NapCat 页面登录小号')
        owner = qq_id(body.get('owner_id',config.get('owner_id')))
        if status['account_id']==owner: raise ValueError('当前登录的是主人主账号，请换成机器人小号')
        prepare_media()
        config.update(owner_id=owner,account_id=status['account_id'],enabled=True,enabled_since=time.time())
        save(config)
    elif body.get('action') == 'pause':
        config['enabled']=False; save(config)
    elif body.get('action') == 'activate':
        if not permitted(route()): raise ValueError('请先核验并启用已登录的小号')
        from app import channels
        channels.schema(db)
        with db: channels.observe(db,'qq',route())
    else: raise ValueError('Unknown QQ management action')
    return state()


def prepare_media():
    """Ask NapCat for inline media bytes, rather than granting arbitrary disk access."""
    config=settings();nap=Path(config['napcat_dir'])
    token=json.loads((nap/'config/webui.json').read_text(encoding='utf-8'))['token']
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def request(path, body, credential=None):
        headers={'Content-Type':'application/json'}
        if credential:headers['Authorization']='Bearer '+credential
        req=urllib.request.Request('http://127.0.0.1:6099/api'+path,data=json.dumps(body).encode(),headers=headers)
        with opener.open(req,timeout=15) as response:value=json.loads(response.read(MAX_EVENT))
        if value.get('code')!=0:raise ValueError('NapCat media configuration unavailable')
        return value.get('data')
    login=request('/auth/login',{'hash':hashlib.sha256((token+'.napcat').encode()).hexdigest()})
    credential=(login or {}).get('Credential')
    if not credential:raise ValueError('请在 NapCat 页面完成 WebUI 身份验证')
    value=request('/OB11Config/GetConfig',{},credential)
    if value.get('enableLocalFile2Url') is not True:
        value['enableLocalFile2Url']=True
        request('/OB11Config/SetConfig',{'config':json.dumps(value)},credential)


def save(value):
    target=ROOT/'qq-channel.json'; temp=target.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(target)


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS qq_events(id TEXT PRIMARY KEY,received REAL NOT NULL,task_ids TEXT NOT NULL)')
    db.commit()
    from app import channels
    channels.schema(db)


def receive(db, event):
    config = settings()
    if not config.get('enabled'): return {'ok':True,'ignored':'disabled'}
    if event.get('post_type')!='message' or event.get('message_type')!='private': return {'ok':True,'ignored':'not_owner_private'}
    if str(event.get('self_id'))!=str(config.get('account_id')) or str(event.get('user_id'))!=str(config.get('owner_id')):
        return {'ok':True,'ignored':'identity'}
    if str(event.get('user_id'))==str(event.get('self_id')): return {'ok':True,'ignored':'self'}
    stamp=float(event.get('time',0)); now=time.time()
    if stamp+1 < max(float(config.get('enabled_since',now)),now-120) or stamp>now+60:
        return {'ok':True,'ignored':'historical'}
    mid=str(event.get('message_id',''))
    if not re.fullmatch(r'-?[0-9]{1,30}',mid): raise ValueError('Stable QQ message ID required')
    segments=event.get('message')
    if not isinstance(segments,list) or not 1<=len(segments)<=100: raise ValueError('OneBot array message required')
    schema(db);key=str(event['self_id'])+':'+mid
    from app.sdk_receiver import OWNER
    tasks=[]
    with db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT task_ids FROM qq_events WHERE id=?',(key,)).fetchone()
        if old:return {'ok':True,'duplicate':True,'task_ids':json.loads(old[0])}
        for segment in segments:
            kind=segment.get('type');data=segment.get('data') or {}
            if kind=='text':
                text=data.get('text')
                if not isinstance(text,str) or not text.strip():continue
                if len(text)>20000:raise ValueError('QQ text too long')
                text=text.strip();status='pending';extra={}
            elif kind in ('image','record','video','file'):
                extra={'qq_media':{'type':kind,'data':{k:data[k] for k in ('file','url','file_id','name') if k in data}}}
                kind={'record':'audio'}.get(kind,kind);text='[QQ附件]';status='media_pending'
            else:
                text='[QQ未解析附件：'+str(kind)[:30]+']';kind='text';status='pending';extra={}
            task=uuid.uuid4().hex
            raw={'kind':kind,'original_kind':kind,'raw_text':text,'direction':'incoming','transport':'qq',
                 'reply_device':route(config),'qq_message_id':mid,'links':[],**extra}
            if status=='media_pending':
                raw.update(association_policy='media_10s_v1',association_received_at=now)
            db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                (task,OWNER,now,json.dumps(raw,ensure_ascii=False),status));tasks.append(task)
        db.execute('INSERT INTO qq_events VALUES(?,?,?)',(key,now,json.dumps(tasks)))
        if tasks:
            from app import channels
            channels.observe(db,'qq',route(config))
    return {'ok':True,'duplicate':False,'task_ids':tasks}


def fetch_media(url):
    from urllib.parse import urlsplit
    parsed=urlsplit(str(url));host=(parsed.hostname or '').lower()
    domains=('qpic.cn','qlogo.cn','qq.com','qq.com.cn','gtimg.cn','gtimg.com')
    if parsed.scheme not in ('http','https') or parsed.username or parsed.password or parsed.port not in (None,80,443):
        raise ValueError('Invalid QQ media URL')
    if not any(host==d or host.endswith('.'+d) for d in domains):raise ValueError('QQ media host denied')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(url,timeout=20) as response:
        if int(response.headers.get('Content-Length',0))>MAX_FILE:raise ValueError('QQ attachment exceeds 32MiB')
        data=response.read(MAX_FILE+1)
        mime=response.headers.get_content_type()
    if not 0<len(data)<=MAX_FILE:raise ValueError('QQ attachment exceeds 32MiB')
    return data,mime


def capture(task,message):
    from app.resource_storage import resource_dir
    from app.whale_upload import capture as capture_upload
    item=message['qq_media'];kind=item['type'];data=item['data']
    action={'image':'get_image','record':'get_record','video':'get_file','file':'get_file'}[kind]
    params={'file':data.get('file','')}
    if kind=='record':params['out_format']='wav'
    if kind in ('video','file'):params={'file_id':data.get('file_id') or data.get('file','')}
    resolved={}
    try:resolved=api(action,params,timeout=25)
    except Exception:
        if not data.get('url'):raise ValueError('QQ attachment unavailable')
    # A trusted CDN URL is preferred. Local file access is confined to NapCat cache.
    url=resolved.get('url') or data.get('url')
    if resolved.get('base64'):
        import base64
        raw=base64.b64decode(resolved['base64'],validate=True);mime=''
        if not 0<len(raw)<=MAX_FILE:raise ValueError('QQ attachment exceeds 32MiB')
    elif str(url).startswith(('http://','https://')):raw,mime=fetch_media(url)
    else:
        candidate=Path(str(resolved.get('file') or resolved.get('file_path') or '')).resolve()
        cache=(Path(settings().get('napcat_dir',''))/'cache').resolve()
        if not candidate.is_relative_to(cache) or not candidate.is_file() or candidate.is_symlink():raise ValueError('QQ cache path denied')
        if not 0<candidate.stat().st_size<=MAX_FILE:raise ValueError('QQ attachment exceeds 32MiB')
        raw=candidate.read_bytes();mime=''
    name=Path(str(data.get('name') or resolved.get('file_name') or '')).name
    suffix=Path(name).suffix.lower()
    if kind=='image':
        import io
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:suffix={ 'JPEG':'.jpg','PNG':'.png','WEBP':'.webp','GIF':'.gif','BMP':'.bmp','TIFF':'.tiff'}.get(image.format,'.bin')
        if len(raw)>10*1024*1024:raise ValueError('QQ image exceeds 10MiB')
    elif kind=='record':suffix='.wav' if raw[:4]==b'RIFF' else '.amr' if raw.startswith(b'#!AMR') else '.ogg'
    elif kind=='video':suffix='.mp4'
    if not re.fullmatch(r'\.[a-z0-9]{1,10}',suffix):suffix='.bin'
    directory=resource_dir('media')/task;directory.mkdir(parents=True,exist_ok=True)
    path=directory/('upload'+suffix);path.write_bytes(raw)
    value={**message,'upload':{'path':str(path),'name':name or ('QQ附件'+suffix),'sha256':hashlib.sha256(raw).hexdigest()}}
    record=capture_upload(task,value);record['source']='qq_upload'
    for file in record['files']:file['source']='qq_upload'
    return record


def deliver(device,kind,text,path=None,digest=None,work=None):
    if not permitted(device):raise ValueError('QQ channel/account changed or disabled')
    status=health()
    if not status.get('online') or not status.get('bound'):raise ValueError('QQ account is offline or changed')
    if kind in ('image','audio'):
        file=Path(path).resolve(strict=True)
        if not 0<file.stat().st_size<=MAX_FILE or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:raise ValueError('Queued attachment changed')
        if kind=='image':
            from app.image_sender import validate_image
            validate_image(str(file))
        segments=[{'type':'image' if kind=='image' else 'record','data':{'file':file.as_uri()}}]
    elif kind=='share':
        value=json.loads(work) if isinstance(work,str) else work or {}
        video_id=str(value.get('itemId') or value.get('video_id') or value.get('aweme_id') or '')
        if not re.fullmatch(r'\d{10,26}',video_id):raise ValueError('Invalid work ID')
        segments=[{'type':'text','data':{'text':(text or '')+'\nhttps://www.douyin.com/video/'+video_id}}]
    elif kind=='text':segments=[{'type':'text','data':{'text':text}}]
    else:raise ValueError('Unsupported QQ reply type')
    result=api('send_private_msg',{'user_id':int(settings()['owner_id']),'message':segments,'auto_escape':True},timeout=30)
    if result.get('message_id') is None:return {'result':'outcome_uncertain','channel':'qq'}
    return {'result':'server_accepted','channel':'qq','message_id':str(result['message_id']),
            'receipt_scope':'onebot_submit','recipient_delivery_confirmed':False}


def start():
    """Launch only our isolated NapCat bootstrap; never alter installed QQ files."""
    import socket
    with socket.socket() as sock:
        sock.settimeout(.3)
        if sock.connect_ex(('127.0.0.1',6099))==0:return None
    config=settings();nap=Path(config['napcat_dir'])
    env=dict(os.environ)
    env.update({k:str(nap/v) for k,v in {'NAPCAT_PATCH_PACKAGE':'qqnt.json','NAPCAT_LOAD_PATH':'loadNapCat.js',
        'NAPCAT_INJECT_PATH':'NapCatWinBootHook.dll','NAPCAT_LAUNCHER_PATH':'NapCatWinBootMain.exe','NAPCAT_MAIN_PATH':'napcat.mjs'}.items()})
    with (ROOT/'napcat-launch.log').open('ab') as log:
        argv=[str(nap/'NapCatWinBootMain.exe'),config['qq_exe'],str(nap/'NapCatWinBootHook.dll')]
        if config.get('account_id'):argv+=['-q',qq_id(config['account_id'])]
        process=subprocess.Popen(argv,
            cwd=nap,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return process
