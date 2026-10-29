"""Paired Whale Chat transport served by the independent, manager-owned gateway.

Only a local connector holding the shared secret and an enrolled device hash can
submit owner turns or proxy selected management routes. Device cookies never
leave the original pairing service. Outgoing IDs survive reconnect/restart.
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from app.live_service import ROOT
from app import channels

LOCK=threading.Lock()
CONFIG=ROOT/'whale-channel.json'
SECRET=ROOT/'whale-bridge-secret.txt'
REGISTRY=ROOT/'whale-data/whale-devices.json'
DEVICE_RE=re.compile(r'[0-9a-f]{64}')
GET_ROUTES={'/','/favicon.ico','/api/overview','/api/threads','/api/agents/dsh','/api/agents/astrbot','/api/activity','/api/tasks','/api/outbound','/api/comments','/api/comment-login','/api/storage','/api/channels','/api/app/ping'}
POST_ROUTES={'/api/target','/api/agent','/api/astrbot/session','/api/policy','/api/activity','/api/channels','/api/local-image'}
GET_ROUTES.add('/api/groups')
POST_ROUTES.add('/api/groups')


def settings():
    try:
        value=json.loads(CONFIG.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def save(patch):
    if set(patch)-{'enabled','remote_management','devices','device_names'}:raise ValueError('Unknown channel setting')
    for key in ('enabled','remote_management'):
        if key in patch and not isinstance(patch[key],bool):raise ValueError('Boolean required')
    if 'devices' in patch and (not isinstance(patch['devices'],list) or len(patch['devices'])>16 or any(not isinstance(x,str) or not DEVICE_RE.fullmatch(x) for x in patch['devices'])):raise ValueError('Invalid device list')
    if 'device_names' in patch:
        names=patch['device_names']
        if not isinstance(names,dict) or len(names)>16 or any(not isinstance(k,str) or not DEVICE_RE.fullmatch(k) or not isinstance(v,str) or not 1<=len(v.strip())<=30 or re.search(r'[\x00-\x1f\x7f]',v) for k,v in names.items()):raise ValueError('设备备注需1–30字')
    with LOCK:
        value=settings();value.update(patch)
        tmp=CONFIG.with_suffix('.json.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(CONFIG)
    return value


def authenticate(token,device,management=False):
    value=settings()
    try:expected=SECRET.read_text(encoding='utf-8').strip()
    except OSError:return False
    return bool(value.get('enabled') and isinstance(token,str) and len(expected)>=48 and hmac.compare_digest(token,expected)
                and isinstance(device,str) and DEVICE_RE.fullmatch(device) and device in value.get('devices',[])
                and (not management or value.get('remote_management')))


def authenticate_secret(token):
    """Bridge token only; used by the gateway to register a just-paired device."""
    try:expected=SECRET.read_text(encoding='utf-8').strip()
    except OSError:return False
    return bool(len(expected)>=48 and isinstance(token,str) and hmac.compare_digest(token,expected))


def enroll(device):
    """Register a device produced by the gateway's one-time pairing flow.

    Pairing already consumed the token and optional tunnel password; the gateway
    passes only the SHA-256 device hash through the loopback bridge. This keeps
    the first scan usable without a second manual sync.
    """
    if not isinstance(device,str) or not DEVICE_RE.fullmatch(device):raise ValueError('Invalid device hash')
    value=settings()
    if not value.get('enabled') or not value.get('standalone'):raise ValueError('Whale channel is not enabled')
    devices=list(value.get('devices',[]))
    if device not in devices:
        if len(devices)>=16:raise ValueError('Whale device limit reached')
        devices.append(device);value=save({'devices':devices})
    return {'ok':True,'enrolled':True,'device':device,'devices':value.get('devices',[])}


def schema(db):
    channels.schema(db)
    db.execute('''CREATE TABLE IF NOT EXISTS whale_messages(
        seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,device TEXT NOT NULL,
        role TEXT NOT NULL,kind TEXT NOT NULL,text TEXT NOT NULL,path TEXT,sha256 TEXT,
        task_id TEXT,created REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS whale_messages_device ON whale_messages(device,seq)')
    if 'duration_ms' not in {r[1] for r in db.execute('PRAGMA table_info(whale_messages)')}:
        db.execute('ALTER TABLE whale_messages ADD COLUMN duration_ms INTEGER')
    db.commit()


def receive(db,device,message_id,text):
    from app.sdk_receiver import OWNER
    if not settings().get('enabled') or device not in settings().get('devices',[]):raise ValueError('Whale device is not enrolled')
    if not isinstance(message_id,str) or not re.fullmatch(r'[0-9a-f-]{32,36}',message_id):raise ValueError('Stable message ID required')
    if not isinstance(text,str) or not 1<=len(text.strip())<=6000:raise ValueError('Message requires 1..6000 characters')
    schema(db);text=text.strip();key='whale-in:'+device+':'+message_id
    with db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT task_id,text FROM whale_messages WHERE id=?',(key,)).fetchone()
        if old:
            if old[1]!=text:raise ValueError('Message ID reused with different content')
            return {'ok':True,'task_id':old[0],'duplicate':True}
        task=uuid.uuid4().hex;now=time.time()
        raw={'kind':'text','raw_text':text,'links':[],'direction':'incoming','transport':'whale','reply_device':device,'whale_message_id':message_id}
        db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',(task,OWNER,now,json.dumps(raw,ensure_ascii=False),'pending'))
        db.execute('INSERT INTO whale_messages(id,device,role,kind,text,task_id,created) VALUES(?,?,?,?,?,?,?)',(key,device,'user','text',text,task,now))
        channels.observe(db,'whale',device)
    return {'ok':True,'task_id':task,'duplicate':False}


def deliver(db,device,outbox_id,kind,text,path=None,digest=None,work=None):
    if not settings().get('enabled') or device not in settings().get('devices',[]):raise ValueError('Whale channel/device disabled')
    duration_ms=None
    if kind=='image':
        from app.image_sender import validate_image
        actual,sha=validate_image(path)
        if sha!=digest:raise ValueError('Queued image changed')
        path=str(actual)
    elif kind=='audio':
        from app.audio_sender import validate_audio
        actual,sha,duration=validate_audio(path);duration_ms=round(duration*1000)
        if sha!=digest:raise ValueError('Queued audio changed')
        path=str(actual)
    elif kind=='share':
        work=json.loads(work or '{}');video=str(work.get('itemId',''))
        if not re.fullmatch(r'\d{15,25}',video):raise ValueError('Invalid work ID')
        text=str(work.get('title') or '分享作品')+'\nhttps://www.douyin.com/video/'+video;kind='text'
    elif kind!='text':raise ValueError('Unsupported Whale message')
    schema(db);key='whale-out:'+outbox_id
    with db:
        db.execute('INSERT INTO whale_messages(id,device,role,kind,text,path,sha256,created,duration_ms) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',(key,device,'assistant',kind,text or '',path,digest,time.time(),duration_ms))
        row=db.execute('SELECT device,kind,text,path,sha256 FROM whale_messages WHERE id=?',(key,)).fetchone()
        if tuple(row)!=(device,kind,text or '',path,digest):raise ValueError('Conflicting Whale outgoing ID')
    return {'result':'server_accepted','channel':'whale','message_id':key,'receipt_scope':'saved_in_local_whale_history','recipient_delivery_confirmed':False}


def history(db,device,since=None):
    schema(db)
    if since is None:
        rows=list(reversed(db.execute('SELECT seq,id,role,kind,text,duration_ms FROM whale_messages WHERE device=? ORDER BY seq DESC LIMIT 100',(device,)).fetchall()))
    else:
        if not isinstance(since,int) or isinstance(since,bool) or not 0<=since<=2**53-1:raise ValueError('Invalid history cursor')
        rows=db.execute('SELECT seq,id,role,kind,text,duration_ms FROM whale_messages WHERE device=? AND seq>? ORDER BY seq LIMIT 500',(device,since)).fetchall()
    items=[]
    for seq,key,role,kind,text,duration_ms in rows:
        item={'id':key,'role':role,'kind':kind,'text':text,'seq':seq}
        if kind=='image':item['image']='/api/wechat/assistant/image?id='+str(seq)
        if role=='user' and kind in ('image','file'):
            item['attachment']='/api/wechat/assistant/file?id='+str(seq)
            if kind=='image':item['image']=item['attachment']
        if kind=='audio':item.update(audio='/api/wechat/assistant/audio?id='+str(seq),duration_ms=duration_ms)
        items.append(item)
    last=rows[-1][0] if rows else (since or 0)
    has_dispatch=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_dispatch' AND type='table'").fetchone())
    join='LEFT JOIN chat_dispatch d ON d.task_id=t.id' if has_dispatch else ''
    dispatch='d.status,d.detail' if has_dispatch else 'NULL,NULL'
    pending=db.execute(f"SELECT t.id,t.status,{dispatch} FROM live_tasks t {join} WHERE t.status IN ('pending','processing') AND json_valid(t.raw_json) AND json_extract(t.raw_json,'$.transport')='whale' AND json_extract(t.raw_json,'$.reply_device')=? ORDER BY t.received DESC",(device,)).fetchall()
    # An attempted/uncertain dispatch is not evidence that a model is thinking.
    # A task actually claimed as processing remains busy, regardless of receipt.
    busy=any(state=='processing' or delivery not in ('outcome_uncertain','rejected') for _,state,delivery,_ in pending)
    attention=None
    for key,state,delivery,detail in pending:
        if state!='pending' or delivery not in ('outcome_uncertain','rejected'):continue
        try:provider=json.loads(detail or '{}').get('provider')
        except (ValueError,AttributeError):provider=None
        label={'dsh':'DSH','codex':'Codex','astrbot':'AstrBot'}.get(provider,'AI会话')
        attention={'task_id':key,'state':delivery,'message':f'上一条消息{label}投递'+('被拒绝' if delivery=='rejected' else '结果未确认')+'，没有自动重发。可以继续发送新消息，切换主体不会重投旧消息。'}
        break
    return {'ok':True,'history':items,'seq':last,'busy':busy,'workflow':True,'dispatch_attention':attention}


def image(db,device,seq):
    schema(db)
    row=db.execute("SELECT path,sha256 FROM whale_messages WHERE seq=? AND device=? AND kind='image'",(int(seq),device)).fetchone()
    if not row:raise ValueError('Image unavailable for this device')
    from app.image_sender import validate_image
    path,digest=validate_image(row[0])
    if digest!=row[1]:raise ValueError('Image changed')
    import mimetypes
    return path.read_bytes(),mimetypes.guess_type(str(path))[0] or 'image/png'


def state(db):
    config=settings();route=channels.current(db)
    pairing_url=None
    try:
        port=json.loads((ROOT/'whale-endpoint.json').read_text(encoding='utf-8'))['port']
        if isinstance(port,int) and not isinstance(port,bool) and 1024<=port<=65535:pairing_url=f'http://127.0.0.1:{port}/whale-panel'
    except (OSError,ValueError,KeyError):pass
    gateway=gateway_health() if config.get('standalone') else {}
    if config.get('standalone'):pairing_url='http://127.0.0.1:8795/whale-panel'
    from app import qq_channel
    return {'qq':qq_channel.state(),'enabled':bool(config.get('enabled')),'remote_management':bool(config.get('remote_management')),
            'standalone':bool(config.get('standalone')),'gateway_online':gateway.get('ok') is True,'public_network':public_health(),
            'devices':config.get('devices',[]),'device_info':device_info(config),
            'device_groups':device_groups(device_info(config),route.get('device')),'active':route,'pairing_url':pairing_url,
            'note':'私聊固定回复到来源；此选择用于主动联系和作品分享。鲸聊为自制App，并非腾讯微信。'}


def audio(db,device,seq):
    schema(db)
    row=db.execute("SELECT path,sha256 FROM whale_messages WHERE seq=? AND device=? AND kind='audio'",(int(seq),device)).fetchone()
    if not row:raise ValueError('Audio unavailable for this device')
    from app.audio_sender import validate_audio
    path,digest,_=validate_audio(row[0])
    if digest!=row[1]:raise ValueError('Audio changed')
    return path.read_bytes(),'audio/wav'


def paired_devices():
    data=json.loads(REGISTRY.read_text(encoding='utf-8'))
    if not isinstance(data,dict):raise ValueError('Pairing registry unavailable')
    return [hashlib.sha256(key.encode()).hexdigest() for key,value in data.items()
            if isinstance(key,str) and isinstance(value,dict) and isinstance(value.get('lastSeenAt'),(int,float))
            and time.time()*1000-value['lastSeenAt']<30*24*3600*1000]


def device_info(config):
    try:
        data=json.loads(REGISTRY.read_text(encoding='utf-8'))
        records={hashlib.sha256(key.encode()).hexdigest():value for key,value in data.items() if isinstance(key,str) and isinstance(value,dict)}
    except (OSError,ValueError,AttributeError):records={}
    names=config.get('device_names') or {};result=[]
    for index,key in enumerate(config.get('devices',[]),1):
        row=records.get(key,{})
        ua=str(row.get('userAgent') or '')[:500]
        platform='Android 手机' if 'Android' in ua else 'iPhone' if 'iPhone' in ua else 'iPad' if 'iPad' in ua else 'Windows 浏览器' if 'Windows' in ua else 'Mac 浏览器' if 'Macintosh' in ua else '未识别设备'
        model=''
        match=re.search(r'Android[^;)]*;\s*(?:[a-z]{2}[-_][A-Z]{2};\s*)?([^;)]+)',ua)
        if match:
            model=re.sub(r'\s+Build/.*','',match[1]).strip()
            if model in ('K','wv') or len(model)>60:model=''
        name=names.get(key) or ((platform+' · '+model) if model else platform+' '+str(index))
        def timestamp(field):
            value=row.get(field)
            return value/1000 if isinstance(value,(int,float)) and not isinstance(value,bool) and 0<value<1e15 else None
        # Display grouping is deliberately not an identity/permission decision.
        # Same-model phones can have identical UAs; never merge their credentials,
        # history, enrollment or immutable reply addresses on this basis.
        group=hashlib.sha256((ua+'\n'+str(names.get(key,''))).encode()).hexdigest() if ua and model else key
        seen=timestamp('lastSeenAt')
        available=bool(row) and seen is not None and time.time()-seen<30*24*3600
        result.append({'id':key,'name':name,'custom_name':names.get(key,''),'platform':platform,'model':model,'last_seen':seen,'paired_at':timestamp('createdAt'),'display_group':group,'pairing_available':available})
    return result


def device_groups(records,active_device=None):
    groups={}
    for row in records:
        if row.get('pairing_available') is False:continue
        groups.setdefault(row.get('display_group',row['id']),[]).append(row)
    result=[]
    for members in groups.values():
        members=sorted(members,key=lambda x:x.get('last_seen') or 0,reverse=True)
        chosen=next((x for x in members if x['id']==active_device),members[0])
        result.append({**chosen,'pairings':members,'pairing_count':len(members),
                       'last_seen':max((m.get('last_seen') or 0 for m in members),default=0) or None})
    return sorted(result,key=lambda x:x.get('last_seen') or 0,reverse=True)


def gateway_health():
    import urllib.request
    try:
        op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open('http://127.0.0.1:8795/health',timeout=1) as response:value=json.loads(response.read(1000))
        return value if value.get('service')=='feiyu-whale' and value.get('standalone') is True else {}
    except (OSError,ValueError):return {}


def configure(db,body):
    if set(body)-{'enabled','remote_management','channel','device','sync_paired','rename_device','device_name'}:raise ValueError('Unknown setting')
    patch={k:body[k] for k in ('enabled','remote_management') if k in body}
    if 'rename_device' in body or 'device_name' in body:
        value=settings();key=body.get('rename_device');name=body.get('device_name')
        if key not in value.get('devices',[]):raise ValueError('只能备注已登记的设备')
        if not isinstance(name,str) or not 1<=len(name.strip())<=30:raise ValueError('设备备注需1–30字')
        names=dict(value.get('device_names') or {});names[key]=name.strip();patch['device_names']=names
    if 'sync_paired' in body:
        if body['sync_paired'] is not True:raise ValueError('Explicit pairing sync required')
        patch['devices']=paired_devices()
    value={**settings(),**patch}
    if 'channel' in body:
        if body['channel'] not in ('douyin','whale'):raise ValueError('Unknown message channel')
        if body['channel']=='whale' and (not value.get('enabled') or body.get('device') not in value.get('devices',[])):raise ValueError('Select an enrolled Whale device')
    save(patch)
    if 'channel' in body:
        channels.schema(db)
        with db:channels.observe(db,body['channel'],body.get('device'))
    return state(db)


def public_health():
    """Only fresh direct-ingress checks count; a local gateway is insufficient."""
    try:
        value=json.loads((ROOT/'whale-data/public-health.json').read_text(encoding='utf-8'))
        if not 0<=time.time()-float(value.get('checked_at',0))<180:raise ValueError('stale')
        if value.get('reachable') not in (True,False,None):raise ValueError('invalid')
        return {'reachable':value.get('reachable'),'state':value.get('state'),'checked_at':value.get('checked_at')}
    except (OSError,ValueError,TypeError):return {'reachable':None,'state':'unverified'}
