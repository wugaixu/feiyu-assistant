"""Explicit loopback AstrBot instances; no cross-instance fallback or shared data."""
import contextvars
import json
import re
import socket
import time
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(r'__RUNTIME_ROOT__')
CURRENT=contextvars.ContextVar('astrbot_instance',default='default')
_attempts={}


def registry():
    try:values=json.loads((ROOT/'astrbot-instances.json').read_text(encoding='utf-8'))
    except FileNotFoundError:return {}
    if not isinstance(values,dict):raise ValueError('Invalid AstrBot instance registry')
    result={}
    for key,value in values.items():
        if not re.fullmatch(r'[a-z][a-z0-9_-]{1,30}',key) or key=='default':raise ValueError('Invalid instance ID')
        port=value.get('port')
        if type(port) is not int or not 6186<=port<=6199:raise ValueError('Invalid instance port')
        result[key]={**value,'root':ROOT/'astrbot-instances'/key,'url':f'http://127.0.0.1:{port}/'}
    return result


def info(key=None):
    key=key or CURRENT.get()
    if key=='default':return {'root':ROOT/'astrbot','url':'http://127.0.0.1:6185/','title':'麦麦','port':6185}
    value=registry().get(key)
    if value is None:raise ValueError('AstrBot instance unavailable; no fallback')
    return value


def session_info(session):
    try:items=json.loads((ROOT/'astrbot-sessions.json').read_text(encoding='utf-8'))
    except FileNotFoundError:return {}
    return next((x for x in items if x.get('id')==session),{})


@contextmanager
def scope(key):
    info(key)
    token=CURRENT.set(key)
    try:yield
    finally:CURRENT.reset(token)


def session_scope(session):return scope(session_info(session).get('instance_id','default'))


def data_root():return info()['root']/'runtime/data'


def ensure(spawn,project):
    started=[]
    for key,value in registry().items():
        if not value.get('enabled'):continue
        try:
            with socket.create_connection(('127.0.0.1',value['port']),timeout=.2):continue
        except OSError:pass
        now=time.monotonic()
        if now-_attempts.get(key,0)<45:continue
        _attempts[key]=now
        import sys
        spawn([str(Path(sys.executable).with_name('pythonw.exe')),'-B','-X','utf8',str(project/'start_astrbot.py'),'--instance',key],
              project,'astrbot-'+key+'-launch.log')
        started.append(key)
    return started
