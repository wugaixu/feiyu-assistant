"""Send reply text through the local SDK desktop-channel sender service.

The SDK receipt confirms submission only. Business check codes require separate
classification; a server message ID does not prove recipient visibility.
"""
import json
import urllib.request

BASE_URL = 'http://127.0.0.1:8791'


def health(timeout=3.0):
    """Return the service status dict when it is online, else None."""
    try:
        with urllib.request.urlopen(BASE_URL + '/health', timeout=timeout) as response:
            value = json.loads(response.read().decode('utf-8'))
    except Exception:
        return None
    return value if isinstance(value, dict) and value.get('ok') else None


def classify(value):
    """Map a sender payload onto our outbound result vocabulary."""
    if not isinstance(value, dict):
        return 'outcome_uncertain'
    def code(v):
        if isinstance(v,bool) or not isinstance(v,(int,str)):return None
        try:return int(v) if v is not None and str(v).strip() else None
        except (ValueError,TypeError,OverflowError):return None
    status=code(value.get('statusCode'))
    check=code(value.get('checkCode'))
    if status is not None and status!=0:return 'platform_not_accepted'
    # 8610 is explicitly rejected by the SDK. It calls 8101 "delivered", but
    # the owner reports missing messages with that code. Treat its meaning as
    # unresolved, never as definitive rejection eligible for an automatic retry.
    if check==8610:return 'platform_not_accepted'
    if value.get('checkCode') is not None and check!=0:return 'outcome_uncertain'
    if value.get('ok') is True and status==0:
        identity=str(value.get('serverMessageId') or '')
        if identity.isdigit() and int(identity)>0:return 'server_accepted'
    response=value.get('response')
    if isinstance(response,dict):
        raw_status=code(response.get('status_code'))
        if raw_status in (8101,10502):return 'outcome_uncertain'
        if raw_status is not None and raw_status!=0:return 'platform_not_accepted'
    return 'outcome_uncertain'


def send(text, timeout=25.0):
    return post('/send',{'text':text},timeout)

def post(endpoint,body,timeout=25.0):
    if endpoint not in ('/send','/send-image','/send-share','/group-send'):raise ValueError('Unsupported SDK action')
    payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        BASE_URL + endpoint, data=payload,
        headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode('utf-8'))
    except Exception as exc:
        return {'ok': False, 'result': 'outcome_uncertain',
                'reason': 'sdk_sender_unreachable', 'error_class': type(exc).__name__}
    result = classify(value)
    value['result'] = result
    return value
