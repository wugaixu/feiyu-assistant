"""Authenticated loopback link to the connector in the existing DSH host."""
import json
import re
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(r'__RUNTIME_ROOT__')
SESSION_RE = re.compile(r'[A-Za-z0-9_-]{1,128}')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class DeliveryRejected(ConnectionError):
    """A bounded explicit pre-enqueue rejection, not a transport timeout."""
    def __init__(self,code):
        self.code=code
        super().__init__('DSH明确拒绝投递：'+code+'；任务未自动重试')


def request(path, body=None, timeout=None):
    try:
        config = json.loads((ROOT/'dsh-bridge.json').read_text(encoding='utf-8-sig'))
        token = config['token']
        if not isinstance(token,str) or len(token)<32:raise ValueError()
        data = None if body is None else json.dumps(body,ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request('http://127.0.0.1:8794'+path,data=data,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with OPENER.open(req,timeout=timeout if timeout is not None else (35 if body else 12)) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            with exc:data=json.loads(exc.read(4096))
            code=data.get('error')
        except (OSError,ValueError,AttributeError):code=None
        if exc.code in (400,409) and code in ('invalid_request','destination_changed','session_unavailable'):
            raise DeliveryRejected(code) from None
        raise ConnectionError('DSH投递回执未确认；不自动重试，请检查连接记录') from None
    except Exception as exc:
        # Never expose local authorization headers or server error bodies.
        raise ConnectionError('DSH连接未就绪，请保持DSH运行并检查本地连接插件') from exc


def capabilities(timeout=3):
    result=request('/capabilities',timeout=timeout)
    if result.get('ok') is not True or result.get('mode')!='queue':raise ConnectionError('DSH连接器协议检查失败')
    return result


def sessions(timeout=None):
    result=request('/sessions',timeout=timeout) if timeout is not None else request('/sessions')
    if result.get('ok') is not True or not isinstance(result.get('items'),list):
        raise ConnectionError('DSH会话列表响应无效')
    return [x for x in result['items'] if isinstance(x,dict) and isinstance(x.get('id'),str) and SESSION_RE.fullmatch(x['id'])]


def prompt(session_id,task_id,message):
    # Reject synthetic/mismatched tasks before reaching a live session. This is
    # also a guard against tests accidentally inheriting the user's provider.
    db=sqlite3.connect('file:'+str(ROOT/'assistant.sqlite3')+'?mode=ro',uri=True,timeout=3)
    try:
        row=db.execute('''SELECT t.owner,t.status,d.status,d.detail FROM live_tasks t
            JOIN chat_dispatch d ON d.task_id=t.id WHERE t.id=?''',(task_id,)).fetchone()
    finally:db.close()
    from app.sdk_receiver import OWNER
    if not row or row[0]!=OWNER or row[1]!='pending' or row[2]!='attempted':
        raise ValueError('DSH投递任务未通过本地身份及预留状态核对')
    snapshot=json.loads(row[3] or '{}')
    if snapshot.get('provider')!='dsh' or snapshot.get('session_id')!=session_id:
        raise ValueError('DSH投递目的地与预留记录不一致')
    result=request('/prompt',{'session_id':session_id,'task_id':task_id,'message':message})
    if result.get('accepted') is not True or result.get('request_id')!='douyin-'+task_id:
        raise ConnectionError('DSH未确认接受消息；不自动重试')
    return result['request_id']


def instructions():
    return ('\n项目：__PROJECT_DIR__。先读取该目录AGENTS.md。'
            '沿用本地bridge领取与回复队列，不在会话中直接发送抖音消息。'
            'Python解释器：__PYTHON__；'
            '使用 -B -X utf8，临时文件和UTF-8回复存入 __RUNTIME_ROOT__。'
            '工具权限以当前DSH会话现有授权为准；无法读取文件或执行bridge时如实报告，不提升权限。')
