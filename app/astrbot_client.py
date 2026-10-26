"""Scoped loopback AstrBot client. Never exposes provider/API secrets to the UI."""
import json
import copy
import threading
import mimetypes
import re
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(r'__RUNTIME_ROOT__')
BASE = 'http://127.0.0.1:6185/api/v1'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ConnectionError('AstrBot redirect refused')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

def config():
    value=json.loads((ROOT/'astrbot-bridge.json').read_text(encoding='utf-8'))
    if not str(value.get('api_key','')).startswith('abk_') or value.get('username')!='douyin-owner-__OWNER_UID__':
        raise ValueError('AstrBot connector credentials invalid')
    return value

def open_request(path, body=None, timeout=5, content_type='application/json', method=None):
    if not path.startswith('/') or path.startswith('//'): raise ValueError('Local API path required')
    data = json.dumps(body,ensure_ascii=False).encode('utf-8') if isinstance(body,(dict,list)) else body
    req=urllib.request.Request(BASE+path,data=data,headers={'Authorization':'Bearer '+config()['api_key'],'Content-Type':content_type},method=method)
    try: return OPENER.open(req,timeout=timeout)
    except Exception as exc: raise ConnectionError('AstrBot本地接口不可用，请检查服务和连接密钥') from None

def request(path,body=None,timeout=5,method=None):
    with open_request(path,body,timeout,method=method) as response: value=json.loads(response.read(4_000_000))
    if value.get('status')!='ok': raise ConnectionError('AstrBot接口未确认操作；请查看AstrBot日志')
    return value.get('data')

def _state(timeout=2):
    try:
        cfg=config(); configs=request('/chat/configs',timeout=timeout)
        if isinstance(configs,dict): configs=configs.get('info_list',[])
        value=request('/chat/sessions?'+urllib.parse.urlencode({'username':cfg['username'],'page_size':100}),timeout=timeout)
        remote=[{'id':x['session_id'],'title':x.get('display_name') or '抖音私信会话','cwd':'AstrBot 专用会话'} for x in value.get('sessions',[])]
        try: local=json.loads((ROOT/'astrbot-sessions.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):local=[]
        aliases={x['id']:x['title'] for x in local if isinstance(x,dict)}
        # Only sessions created here are valid DM targets. Scratch sessions from
        # self-tests or public-comment generation stay out of the picker; they
        # are still visible in AstrBot's own data pages.
        sessions=[{**x,'title':aliases[x['id']]} for x in remote if x['id'] in aliases]
        sessions += [x for x in local if isinstance(x,dict) and x['id'] not in {s['id'] for s in sessions}]
        return {'online':True,'sessions':sessions,'configs':configs,'error':None,'url':'http://127.0.0.1:6185/'}
    except Exception:
        return {'online':False,'sessions':[],'configs':[],'error':'AstrBot未连接，请启动服务或检查本地连接配置','url':'http://127.0.0.1:6185/'}

_state_cache={}
_state_lock=threading.Lock()


def state(timeout=2,cache_seconds=0):
    # Only background delivery opts into caching; UI/manual refresh stays fresh.
    if cache_seconds<=0:return _state(timeout)
    try:
        key=tuple((ROOT/name).stat().st_mtime_ns for name in ('astrbot-bridge.json','astrbot-sessions.json'))
    except OSError:key=None
    with _state_lock:
        now=time.monotonic()
        if _state_cache.get('key')==key and now<_state_cache.get('expires',0):return copy.deepcopy(_state_cache['value'])
        value=_state(timeout)
        _state_cache.update(key=key,expires=time.monotonic()+min(cache_seconds,10),value=copy.deepcopy(value))
        return value


def create_session(title):
    if not isinstance(title,str) or not 1<=len(title.strip())<=60:raise ValueError('会话名称须为1–60字')
    if not state()['online']:raise ConnectionError('请先启动AstrBot')
    from app.resource_storage import atomic
    path=ROOT/'astrbot-sessions.json'
    try:items=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):items=[]
    item={'id':str(uuid.uuid4()),'title':title.strip(),'cwd':'AstrBot 专用会话（首条消息时建立）'}
    atomic(path,items+[item]);return item

def upload(path):
    path=Path(path); raw=path.read_bytes()
    if not 0<len(raw)<=12*1024*1024: raise ValueError('Attachment size exceeded')
    boundary=uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="input{path.suffix.lower()}"\r\nContent-Type: {mimetypes.guess_type(path.name)[0] or "application/octet-stream"}\r\n\r\n').encode()+raw+f'\r\n--{boundary}--\r\n'.encode()
    with open_request('/file',body,30,'multipart/form-data; boundary='+boundary) as response: value=json.load(response)
    if value.get('status')!='ok': raise ConnectionError('AstrBot附件上传未确认')
    data=value.get('data') or {}; aid=data.get('attachment_id') or data.get('id')
    if not isinstance(aid,str):raise ConnectionError('AstrBot附件ID缺失')
    return aid

def save_image(raw,task_id):
    """Persist an inbound AstrBot image under the shared format contract.

    PNG/JPEG/GIF/WEBP are stored byte-for-byte so an animated GIF stays
    animated; BMP/TIFF are re-encoded to PNG so storage matches what the
    outbound transports accept.
    """
    from app import media_formats as formats
    if not isinstance(raw,bytes) or not 0<len(raw)<=formats.MAX_BYTES:raise ValueError('Image too large')
    import io,hashlib
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as img:
        fmt=img.format; width,height=img.size
        img.verify()
    if fmt not in formats.INBOUND_IMAGE or width*height>formats.MAX_PIXELS:
        raise ValueError('Unsupported image')
    suffix=formats.save_suffix(fmt)
    if not suffix:raise ValueError('Unsupported image')
    name='astrbot-'+task_id+'-'+hashlib.sha256(raw).hexdigest()[:16]
    dest=ROOT/'outgoing-images'/(name+suffix)
    dest.parent.mkdir(exist_ok=True)
    if formats.stored_as_is(fmt):
        dest.write_bytes(raw)
        return str(dest)
    with Image.open(io.BytesIO(raw)) as img:img.convert('RGB').save(dest,'PNG')
    return str(dest)


def download_image(attachment_id,task_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',attachment_id):raise ValueError('Invalid attachment')
    with open_request('/file?'+urllib.parse.urlencode({'attachment_id':attachment_id}),timeout=30) as response:
        raw=response.read(10*1024*1024+1)
    return save_image(raw,task_id)

def visible_text(text):
    """Drop AstrBot's known plain-text reasoning wrapper as a second defence.

    Non-streaming result decoration can turn reasoning into ordinary Plain
    events without chain_type. Do not forward an incomplete reasoning wrapper.
    """
    if re.match(r'^\s*🤔\s*思考\s*[:：]',text):
        boundary=re.search(r'(?:^|\n)[ \t]*────[─]*[ \t]*(?:\n|$)',text)
        return text[boundary.end():].strip() if boundary else ''
    return text.strip()


def download_audio(attachment_id,task_id):
    if not isinstance(attachment_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',attachment_id):raise ValueError('Invalid attachment')
    from app.audio_sender import MAX_BYTES,prepare
    with open_request('/file?'+urllib.parse.urlencode({'attachment_id':attachment_id}),timeout=30) as response:
        raw=response.read(MAX_BYTES+1)
    return prepare(raw,task_id)


def parse_stream(lines,deadline=None):
    """Only complete visible message parts. Ignore reasoning, tool output and token echoes."""
    parts=[]; buffer=''; ended=False; awaiting=set()
    def flush():
        nonlocal buffer
        shown=visible_text(buffer)
        if shown: parts.append({'type':'text','text':shown})
        buffer=''
    size=0
    for raw in lines:
        if deadline and time.monotonic()>deadline:raise TimeoutError('AstrBot generation deadline')
        size+=len(raw)
        if size>4_000_000:raise ValueError('AstrBot response too large')
        line=raw.decode('utf-8') if isinstance(raw,bytes) else raw
        if not line.startswith('data:'):continue
        event=json.loads(line[5:].strip());kind=event.get('type');chain=event.get('chain_type')
        if kind=='error':raise ConnectionError('AstrBot生成失败；不自动重试')
        if kind=='plain' and chain not in ('reasoning','tool_call','tool_call_result','agent_stats'):
            if not event.get('streaming'):flush()
            buffer+=str(event.get('data') or '')
            if not event.get('streaming'):flush()
        elif kind in ('image','record'):flush();awaiting.add(kind)
        elif kind=='attachment_saved':
            data=event.get('data') or {}
            if data.get('type') in ('image','record'):
                media=data['type'];parts.append({'type':'audio' if media=='record' else 'image','attachment_id':data['id']});awaiting.discard(media)
        elif kind=='complete':flush()  # data is an echo of streamed tokens, not another message.
        elif kind=='end':
            flush();ended=True;break
    if not ended or awaiting:raise ConnectionError('AstrBot回复不完整；不自动重试')
    if not parts:raise ValueError('AstrBot没有产生可发送内容')
    return parts

def streaming_enabled(config_id):
    """Read the selected dashboard profile on every request, without config-write scopes.

    Simply omitting the WebChat flag does NOT inherit this setting: AstrBot
    4.29.1 defaults omitted request flags to True. Use the saved profile value
    explicitly while keeping the HTTP SSE transport for complete message parts.
    """
    profiles=request('/chat/configs')
    if isinstance(profiles,dict):profiles=profiles.get('info_list',[])
    profile=next((x for x in profiles if x.get('id')==config_id),None)
    if not profile:raise ValueError('AstrBot所选配置已不可用')
    data_root=(ROOT/'astrbot/runtime/data').resolve()
    path=Path(profile['path']).resolve(strict=True)
    if path!=data_root/'cmd_config.json' and not (path.is_relative_to(data_root/'config') and path.suffix=='.json'):
        raise ValueError('AstrBot配置路径不在允许目录')
    saved=json.loads(path.read_text(encoding='utf-8-sig'))
    value=(saved.get('provider_settings') or {}).get('streaming_response',False)
    if type(value) is not bool:raise ValueError('AstrBot流式设置无效，请在页面重新保存配置')
    return value


def chat(session_id,message,config_id='default',timeout=180):
    # Do not pin selected_provider: the model, persona and plugins configured in
    # the AstrBot dashboard must be what actually answers. A pinned provider
    # here would silently override dashboard model changes.
    cfg=config()
    body={'username':cfg['username'],'session_id':session_id,'message':message,'config_id':config_id,
          # WebChat defaults enable_reasoning=True, overriding the dashboard's
          # display_reasoning_text=False. This controls display, not model thinking.
          'flags':{'enable_streaming':streaming_enabled(config_id),'enable_reasoning':False}}
    with open_request('/chat',body,timeout) as response:
        if 'text/event-stream' not in response.headers.get('Content-Type',''):raise ConnectionError('AstrBot未返回完整聊天流')
        return parse_stream(response,deadline=time.monotonic()+timeout)
