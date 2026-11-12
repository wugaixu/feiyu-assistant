"""Local management of native SDK sign-in; no remote management or secrets in UI."""
import base64,json,secrets,time,urllib.request
from pathlib import Path
from app.live_service import ROOT

OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def state():
    try:
        with OPENER.open('http://127.0.0.1:8791/health',timeout=3) as r:value=json.load(r)
    except Exception:return {'service_online':False,'online':False,'state':'service_offline'}
    from app.sdk_receiver import ACCOUNT_UID
    online=value.get('online') is True
    result={'service_online':True,'online':online,'account_matches':online and str(value.get('uid'))==ACCOUNT_UID,
            'nickname':value.get('nickname') if online else None,'state':str(value.get('note') or ('online' if online else 'offline'))}
    stamp=value.get('qr_created_at')
    if result['state'].startswith('qr_required') and isinstance(stamp,(int,float)) and 0<=time.time()-stamp<60:
        try:
            data=(ROOT/'sdk-login.png').read_bytes()
            if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)<1_000_000:
                result['qr_image']='data:image/png;base64,'+base64.b64encode(data).decode()
        except OSError:pass
    return result

def start():
    path=ROOT/'sdk-management-secret.txt'
    if not path.exists():
        try:
            with path.open('x',encoding='utf-8') as f:f.write(secrets.token_hex(32))
        except FileExistsError:pass
    secret=path.read_text('utf-8').strip()
    if len(secret)<48:raise ValueError('SDK管理凭据无效，请检查本机安装')
    req=urllib.request.Request('http://127.0.0.1:8791/login',data=b'{}',headers={'Content-Type':'application/json','X-SDK-Management':secret})
    try:
        with OPENER.open(req,timeout=4) as response:result=json.load(response)
    except Exception:raise ValueError('SDK登录入口尚未加载或不可用，请正常重启管理台及SDK后再试') from None
    if result.get('ok') is not True:raise ValueError('SDK未接受登录请求，请检查本机日志')
    return {'ok':True,'state':result.get('state'),'message':'已请求重新连接；需要扫码或验证时将显示提示'}
