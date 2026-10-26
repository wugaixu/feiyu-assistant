"""Local management application for the Douyin AI assistant.

One small local app (bound to 127.0.0.1 only, stdlib only) that consolidates
the project: it owns the workflow lifecycle (starts the SDK service and the
worker with the app, and stops them with the app), serves the UI, lists Codex
sessions and switches the private-message delivery target, shows status / task
queue / public comments / logs and the main switches. It never reads Douyin
login credentials or directly talks to Douyin; a local DSH connector token
is used only for the selected provider's read-only session health probe.
"""
import json
import ntpath
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(r'__RUNTIME_ROOT__')
PROJECT = Path(__file__).resolve().parents[1]
STATE_DB = Path(r'__CODEX_HOME__\state_5.sqlite')
ASSISTANT_DB = ROOT / 'assistant.sqlite3'
TARGET_FILE = ROOT / 'dispatch-target.json'
HTML_FILE = Path(__file__).resolve().parent / 'console.html'


def ui_document():
    source = HTML_FILE.read_text(encoding='utf-8')
    revision = hashlib.sha256(source.encode('utf-8')).hexdigest()[:12]
    return source.replace('__UI_REVISION__', revision), revision

ICON_FILE = Path(__file__).resolve().parent / 'icon.ico'
HOST, PORT = '127.0.0.1', 8793
THREAD_RE = re.compile(r'[0-9a-f-]{36}')
CODEX_APP_STATE = Path(r'__CODEX_HOME__\.codex-global-state.json')
DEFAULT_THREAD = '00000000-0000-0000-0000-000000000000'
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
NODE_EXE = shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
# The server may run under pythonw; worker process names must remain python.exe
# for the application's existing lifecycle/stop rules. CREATE_NO_WINDOW hides it.
PYTHON_EXE = str(Path(sys.executable).with_name('python.exe'))
PYTHONW_EXE = str(Path(sys.executable).with_name('pythonw.exe'))
ASTRBOT_URL = 'http://127.0.0.1:6185/'
_astrbot_launch_at = 0.0
_whale_launch_at = 0.0

POLICY_FILES = {
    'shared_comment': ('shared-comment-policy.json', {'enabled', 'daily_limit', 'enabled_since', 'mention_owner'}),
    'video_round': ('video-round-config.json', {'enabled', 'auto_comment'}),
    'dm': ('dm-policy.json', {'enabled', 'min_send_gap_seconds'}),
}
LOG_FILES = ['console.stdout.log', 'console.stderr.log', 'local-runtime.stderr.log', 'sdk-local.stderr.log',
             'workflow-daemon.log']
_stop = threading.Event()
# Children this console started. Tracking them lets "关闭程序" stop them even
# when the PowerShell cleanup call is unavailable (pythonw/WMI sessions can
# reject subprocess pipes with WinError 6).
_children = []


# ---------------------------------------------------------------- workflow
def _spawn(argv, cwd, log_name):
    global _children
    try:
        stream = open(ROOT / log_name, 'ab')
    except OSError:
        stream = subprocess.DEVNULL
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), creationflags=CREATE_NO_WINDOW,
                                   stdout=stream, stderr=stream)
    except Exception:
        if stream is not subprocess.DEVNULL:
            try:
                stream.close()
            except OSError:
                pass
        raise
    _children = [p for p in _children if p.poll() is None]
    _children.append(process)
    return process


def _sdk_up():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8791/health', timeout=2) as response:
            json.load(response)
        return True
    except Exception:
        return False


def _receiver_alive():
    try:
        return (time.time() - (ROOT / 'sdk-receiver-status.json').stat().st_mtime) < 12
    except OSError:
        return False


def _astrbot_up():
    try:
        with urllib.request.urlopen(ASTRBOT_URL, timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def _astrbot_provider_selected():
    provider = (_read_json(TARGET_FILE, {}) or {}).get('provider')
    return provider == 'astrbot'


def ensure_astrbot():
    """Start the independent AstrBot only while it is the selected provider.

    Never starts it for a Codex/DSH workflow; at most one launch is attempted
    per 45 seconds so a broken install cannot spawn a pile of processes.
    """
    global _astrbot_launch_at
    if not _astrbot_provider_selected() or _astrbot_up():
        return False
    if time.time() - _astrbot_launch_at < 45:
        return False
    _astrbot_launch_at = time.time()
    _spawn([PYTHONW_EXE, '-B', '-X', 'utf8', str(PROJECT / 'start_astrbot.py')],
           PROJECT, 'astrbot-launch.log')
    return True


def ensure_children():
    """Start the SDK service / worker if they are not up (cheap health checks)."""
    started = []
    if ensure_whale():started.append('whale_gateway')
    if not _sdk_up():
        _spawn([NODE_EXE, str(PROJECT / 'sdk_service.mjs')], ROOT, 'sdk-local.stdout.log')
        started.append('sdk_service')
    if not _receiver_alive():
        _spawn([PYTHON_EXE, '-B', '-X', 'utf8', '-m', 'app.sdk_receiver'], PROJECT, 'local-runtime.stdout.log')
        started.append('sdk_receiver')
    try:
        if ensure_astrbot():
            started.append('astrbot')
        from app.gpt_sovits import ensure as ensure_gpt_sovits
        if ensure_gpt_sovits(_spawn):started.append('gpt_sovits')
        from app.dsh_registry import ensure as ensure_dsh_registry
        if ensure_dsh_registry().get('changed'):started.append('dsh_registry')
    except Exception:
        pass
    return started


def ensure_whale():
    """The manager owns the independent phone transport, regardless of AI choice."""
    global _whale_launch_at
    from app import whale_channel
    config=whale_channel.settings()
    if not config.get('standalone') or not config.get('enabled') or whale_channel.gateway_health().get('ok'):return False
    if any(p.poll() is None and getattr(p,'_whale_gateway',False) for p in _children):return False
    if time.time()-_whale_launch_at<30:return False
    _whale_launch_at=time.time()
    process=_spawn([NODE_EXE,str(PROJECT/'whale_service.mjs')],PROJECT,'whale-service.log')
    process._whale_gateway=True
    return True


def stop_whale():
    """Ask our gateway to dispose its own tunnel before ending the child."""
    from app import whale_channel
    if not any(getattr(p,'_whale_gateway',False) is True for p in _children):return
    if not whale_channel.settings().get('standalone'):return
    try:
        token=(ROOT/'whale-bridge-secret.txt').read_text(encoding='utf-8').strip()
        req=urllib.request.Request('http://127.0.0.1:8795/shutdown',data=b'{}',headers={'X-Whale-Bridge':token,'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req,timeout=4) as response:response.read(1000)
        until=time.time()+5
        while time.time()<until and whale_channel.gateway_health().get('ok'):time.sleep(.2)
    except (OSError,ValueError):pass


def stop_children():
    """Stop only our own node/python children, never unrelated processes.

    Tracked children are terminated first because the PowerShell path can fail
    with WinError 6 inside a pythonw/WMI session; in that case the tracked
    handles are the only reliable way to release the workflow processes.
    """
    global _children
    stop_whale()
    from app.local_images import shutdown as stop_local_images
    stop_local_images()
    tracked, _children = _children, []
    for process in tracked:
        try:
            if process.poll() is None:
                process.terminate()
        except Exception:
            pass
    deadline = time.time() + 6
    for process in tracked:
        remaining = max(0.1, deadline - time.time())
        try:
            process.wait(timeout=remaining)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
    script = ("Get-CimInstance Win32_Process | Where-Object { "
              "($_.Name -eq 'node.exe' -and $_.CommandLine -match 'sdk_service.mjs') -or "
              "($_.Name -eq 'node.exe' -and $_.CommandLine -match 'douyin_ai_assistant' -and $_.CommandLine -match 'sensevoice_worker.cjs') -or "
              "($_.Name -eq 'python.exe' -and $_.CommandLine -match 'app.sdk_receiver') -or "
              "($_.Name -eq 'python.exe' -and $_.CommandLine -match 'douyin_ai_assistant' -and $_.CommandLine -match 'app.gpt_sovits') } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        # No capture_output: redirecting pipes fails with OSError WinError 6 in
        # a pythonw process started through WMI, and that used to abort the
        # whole quit path before os._exit was scheduled.
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def supervisor_loop():
    while not _stop.is_set():
        try:
            ensure_children()
        except Exception:
            pass
        _stop.wait(8)


# ------------------------------------------------------------------- state
def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def _atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _title(row):
    for key in ('name', 'title'):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return ' '.join(value.split())
    return '(无标题)'


def read_threads(limit=200):
    state=_read_json(CODEX_APP_STATE,{}) or {}
    def normal(value):
        value=re.sub(r'\\+',lambda _: '\\',str(value or ''))
        return ntpath.normcase(ntpath.normpath(value.removeprefix('\\?\\')))
    base=normal(PROJECT.parent)
    project_ids={key for key,value in state.get('local-projects',{}).items()
        if any(normal(root)==base for root in value.get('rootPaths',[]))}
    assignments=state.get('thread-project-assignments',{})
    projectless=set(state.get('projectless-thread-ids',[]))
    if not STATE_DB.exists():
        return []
    try:
        db = sqlite3.connect(f'file:{STATE_DB}?mode=ro', uri=True, timeout=3)
    except sqlite3.Error:
        return []
    try:
        db.row_factory = sqlite3.Row
        columns = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
        order = [c for c in ('recency_at_ms', 'updated_at_ms', 'created_at_ms') if c in columns]
        if not order:
            return []
        recency = 'COALESCE(' + ','.join(order + ['0']) + ')'
        if 'cwd' not in columns:return []
        fields=[c for c in ('id','title','name','cwd','project_id','is_pinned','archived') if c in columns]
        rows = db.execute(
            'SELECT '+','.join(fields)+','+
            f'{recency} AS recency FROM threads WHERE COALESCE(archived,0)=0 '
            'ORDER BY recency DESC').fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    now_ms = time.time() * 1000
    out = []
    for row in rows:
        data = dict(row)
        membership=assignments.get(data['id'],{})
        if project_ids:
            if data['id'] in projectless or membership.get('projectId',data.get('project_id')) not in project_ids:continue
        elif normal(data.get('cwd'))!=base:continue
        data['title'] = _title(data)
        data['is_pinned'] = bool(data.get('is_pinned'))
        data['recency'] = int(data.get('recency') or 0)
        data['age_ms'] = max(0, int(now_ms - data['recency']))
        out.append(data)
    return out[:limit]


def codex_title(thread_id):
    if not thread_id or not STATE_DB.is_file():return None
    try:
        db=sqlite3.connect(f'file:{STATE_DB}?mode=ro',uri=True,timeout=2)
        try:
            db.row_factory=sqlite3.Row
            columns={r[1] for r in db.execute('PRAGMA table_info(threads)')}
            fields=[c for c in ('name','title') if c in columns]
            if not fields:return None
            row=db.execute('SELECT '+','.join(fields)+' FROM threads WHERE id=?',(thread_id,)).fetchone()
            return _title(dict(row)) if row else None
        finally:db.close()
    except sqlite3.Error:return None


def target_state():
    config = _read_json(TARGET_FILE, {}) or {}
    candidates = [c for c in config.get('candidates', []) if isinstance(c, str) and THREAD_RE.fullmatch(c)]
    effective, reason = None, 'default'
    try:
        from app.event_dispatch import choose_thread
        choice = choose_thread(config)
        effective, reason = choice.get('thread_id'), choice.get('reason')
    except Exception:
        pass
    provider=config.get('provider','codex')
    astrbot=config.get('astrbot') if isinstance(config.get('astrbot'),dict) else {}
    title=None
    if provider=='dsh':
        effective=config.get('dsh',{}).get('session_id');reason='dsh_explicit'
    elif provider=='astrbot':
        effective=astrbot.get('session_id');reason='astrbot_explicit'
        local=_read_json(ROOT/'astrbot-sessions.json',[]) or []
        title=next((x.get('title') for x in local if isinstance(x,dict) and x.get('id')==effective),None)
        title=title or ('AstrBot 专用会话' if effective else None)
    else:
        title=codex_title(effective)
    return {'provider':provider,'dsh_session_id':config.get('dsh',{}).get('session_id'),
            'astrbot':{'session_id':astrbot.get('session_id'),'config_id':astrbot.get('config_id','default')},
            'astrbot_session_id':astrbot.get('session_id'),'astrbot_config_id':astrbot.get('config_id','default'),
            'effective_title':title,
            'workspace':'__CODEX_WORKSPACE__',
            'mode': config.get('mode', 'most_recent'), 'candidates': candidates,
            'fallback_thread_id': config.get('fallback_thread_id'),
            'explicit_thread_id': config.get('thread_id'), 'effective': effective, 'reason': reason}


def workflow_state():
    receiver_path = ROOT / 'sdk-receiver-status.json'
    receiver = _read_json(receiver_path, {}) or {}
    try:
        receiver['age_seconds'] = max(0, int(time.time() - receiver_path.stat().st_mtime))
    except OSError:
        receiver['age_seconds'] = None
    sdk = {}
    try:
        with urllib.request.urlopen('http://127.0.0.1:8791/health', timeout=2) as response:
            sdk = json.load(response)
    except Exception:
        sdk = {'ok': False, 'online': False, 'note': 'unreachable'}
    return {'sdk': sdk, 'receiver': receiver,
            'app': {'pid': os.getpid(), 'started_at': _APP_STARTED, 'uptime': int(time.time() - _APP_STARTED)}}


def policies_state():
    return {name: (_read_json(ROOT / filename, {}) or {}) for name, (filename, _) in POLICY_FILES.items()}


def _assistant_db():
    db = sqlite3.connect(f'file:{ASSISTANT_DB}?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    return db


def tasks_state(limit=30):
    result = {'counts': {}, 'items': []}
    if not ASSISTANT_DB.exists():
        return result
    try:
        db = _assistant_db()
        try:
            for row in db.execute('SELECT status,count(*) FROM live_tasks GROUP BY status'):
                result['counts'][row[0]] = row[1]
            for row in db.execute('SELECT id,status,received,raw_json FROM live_tasks ORDER BY received DESC LIMIT ?', (limit,)):
                try:
                    message = json.loads(row['raw_json'])
                except ValueError:
                    message = {}
                result['items'].append({'id': row['id'], 'status': row['status'], 'received': row['received'],
                                        'kind': message.get('kind'),
                                        'text': ' '.join(str(message.get('raw_text') or '').split())[:110]})
        finally:
            db.close()
    except sqlite3.Error:
        pass
    return result


def comments_state(limit=15):
    from app.delivery_review import latest,automatic
    reviews=latest(ROOT/'delivery-reviews.sqlite3')
    result = {'outbox': [], 'legacy': []}
    if not ASSISTANT_DB.exists():
        return result
    try:
        db = _assistant_db()
        try:
            for row in db.execute('SELECT video_id,status,day,text,created,detail FROM shared_comment_outbox ORDER BY created DESC LIMIT ?', (limit,)):
                try:diagnostic=json.loads(row['detail'] or '{}')
                except ValueError:diagnostic={}
                result['outbox'].append({'video_id': row['video_id'], 'status': row['status'], 'day': row['day'],
                                         'text': row['text'], 'created':row['created'],
                                         'error_class':diagnostic.get('error_class'), 'diagnostic':diagnostic.get('diagnostic',{}),
                                         'review':reviews.get(('comment',row['video_id']),{})})
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='video_comments'").fetchone():
                for row in db.execute('SELECT video_id,status,day FROM video_comments ORDER BY rowid DESC LIMIT ?', (limit,)):
                    result['legacy'].append({'video_id': row['video_id'], 'status': row['status'], 'day': row['day']})
        finally:
            db.close()
    except sqlite3.Error:
        pass
    for item in result['outbox']:item['assessment']=automatic([item],'comment')
    return result


def outbound_state(limit=30):
    from app.sdk_sender import classify
    from app.delivery_review import latest,automatic
    reviews=latest(ROOT/'delivery-reviews.sqlite3')
    state={'items':[],'alerts':[],'window':limit}
    if not ASSISTANT_DB.exists():return state
    db=_assistant_db()
    try:
        for row in db.execute("SELECT * FROM reply_outbox ORDER BY rowid DESC LIMIT ?",(limit,)):
            try:receipt=json.loads(row['detail'] or '{}')
            except ValueError:receipt={}
            if not isinstance(receipt,dict):receipt={}
            channel='whale' if receipt.get('channel')=='whale' else 'douyin'
            if row['status'] in ('pending','attempted'):verdict=row['status']
            elif channel=='whale':
                verdict='server_accepted' if (row['status']=='server_accepted' and receipt.get('result')=='server_accepted' and receipt.get('receipt_scope')=='saved_in_local_whale_history') else 'outcome_uncertain'
            else:verdict=classify(receipt)
            keys=row.keys()
            try:work=json.loads(row['work_json'] or '{}') if 'work_json' in keys else {}
            except ValueError:work={}
            if not isinstance(work,dict):work={}
            entry={'id':row['id'],'task_id':row['task_id'],'status':verdict,'stored_status':row['status'],
                'check_code':receipt.get('checkCode'),'server_message_id':receipt.get('serverMessageId'),
                'text':row['text'],'kind':row['kind'] or 'text','channel':channel,
                'file_name':ntpath.basename(row['path'] or '') if 'path' in keys else '',
                'work_title':str(work.get('title') or '')[:300],
                'review':reviews.get(('reply',row['id']),{})}
            state['items'].append(entry)
            observation=entry['review'].get('receipt',{}).get('value')
            if observation=='missing' or (observation not in ('confirmed','deferred') and verdict in ('outcome_uncertain','platform_not_accepted','attempted')):state['alerts'].append(entry)
    except sqlite3.Error:pass
    finally:db.close()
    for item in state['items']:item['assessment']=automatic([item]+[x for x in state['items'] if x is not item])
    return state


def logs_state(lines=120):
    out = {}
    for name in LOG_FILES:
        path = ROOT / name
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
            out[name] = '\n'.join(text.splitlines()[-lines:])
        except OSError:
            out[name] = ''
    return out


def overview_state():
    workflow=workflow_state();target=target_state();policies=policies_state();outbound=outbound_state()
    from app.workflow_health import collect
    health=collect(workflow,target,policies,outbound,ROOT,ASSISTANT_DB)
    return {'workflow':workflow,'target':target,'policies':policies,
            'counts':tasks_state(limit=1)['counts'],'outbound':outbound,'health':health,'now':time.time()}


# ----------------------------------------------------------------- actions
def set_target(action, thread_id=None):
    if thread_id is not None and not THREAD_RE.fullmatch(thread_id):
        raise ValueError('invalid thread id')
    config = _read_json(TARGET_FILE, {}) or {}
    if not isinstance(config, dict):
        config = {}
    candidates = [c for c in config.get('candidates', []) if isinstance(c, str)]
    allowed={t['id'] for t in read_threads()}
    if action in ('pin','candidate_add','fallback') and thread_id not in allowed:
        raise ValueError('请选择 Work 1 项目内的未归档会话')
    config.setdefault('fallback_thread_id', DEFAULT_THREAD)
    config.setdefault('state_db', str(STATE_DB))
    if action == 'auto':
        candidates=[c for c in candidates if c in allowed]
        if not candidates:raise ValueError('请先在 Work 1 中选择自动跟随的候选会话')
        if config.get('fallback_thread_id') not in allowed:config['fallback_thread_id']=candidates[0]
        config['mode'] = 'most_recent'
        config.pop('thread_id', None)
    elif action == 'pin':
        if thread_id not in candidates:
            candidates.append(thread_id)
        config['mode'] = 'explicit'
        config['thread_id'] = thread_id
    elif action == 'candidate_add':
        if thread_id not in candidates:
            candidates.append(thread_id)
    elif action == 'candidate_remove':
        candidates = [c for c in candidates if c != thread_id]
        if config.get('mode') == 'explicit' and config.get('thread_id') == thread_id:
            config['mode'] = 'most_recent'
            config.pop('thread_id', None)
    elif action == 'fallback':
        config['fallback_thread_id'] = thread_id
    else:
        raise ValueError('unknown target action')
    config['candidates'] = candidates
    _atomic_json(TARGET_FILE, config)
    return target_state()


def agent_sessions_state():
    from app import dsh_client
    try:
        items=dsh_client.sessions()
        from app.workflow_protocol import VERSION
        capabilities=dsh_client.capabilities()
        ready=capabilities.get('protocol_version')==VERSION
        return {'online':True,'sessions':items,'protocol_ready':ready,'capabilities':capabilities,
                'error':None if ready else 'DSH连接器未加载统一协议版本'}
    except ConnectionError as exc:
        return {'online':False,'sessions':[],'error':str(exc)}


def astrbot_state():
    """AstrBot's own session/config list; secrets stay in the connector file."""
    from app import astrbot_client
    state=astrbot_client.state()
    from app.workflow_protocol import astrbot_reader
    state['material_reader']=astrbot_reader()
    from app.local_images import state as image_state
    state['local_image']=image_state()
    from app.voice_capabilities import state as voice_state
    state['voice']=voice_state()
    target=target_state()
    selected=target['astrbot']['session_id']
    state.update({'selected_session_id':selected,
                  'selected_config_id':target['astrbot']['config_id'],
                  'selected_title':next((x.get('title') for x in state.get('sessions',[]) if x.get('id')==selected),None),
                  'provider':target['provider'],
                  'bridge_configured':(ROOT/'astrbot-bridge.json').is_file(),
                  'url':ASTRBOT_URL})
    return state


def start_astrbot():
    """Start the independent AstrBot on demand and wait briefly for readiness."""
    global _astrbot_launch_at
    from app import astrbot_client
    if _astrbot_up():
        return {'ok':True,'started':False,'state':astrbot_state()}
    if time.time()-_astrbot_launch_at>5:
        _astrbot_launch_at=time.time()
        _spawn([PYTHONW_EXE,'-B','-X','utf8',str(PROJECT/'start_astrbot.py')],PROJECT,'astrbot-launch.log')
    deadline=time.time()+12
    while time.time()<deadline:
        time.sleep(0.4)
        if _astrbot_up():
            return {'ok':True,'started':True,'state':astrbot_state()}
    state=astrbot_client.state()
    return {'ok':False,'started':True,'state':state,'error':'AstrBot 启动后仍不可访问，请查看 astrbot/service.log'}


def set_agent(provider,session_id=None,config_id=None):
    if provider not in ('codex','dsh','astrbot'):raise ValueError('无效的处理主体')
    config=_read_json(TARGET_FILE,{}) or {}
    if provider=='dsh':
        from app import dsh_client
        if not isinstance(session_id,str) or not dsh_client.SESSION_RE.fullmatch(session_id):
            raise ValueError('请先选择DSH会话')
        if not any(x['id']==session_id and x.get('available') is not False for x in dsh_client.sessions()):
            raise ValueError('DSH会话不可用，可能缺少原 Agent 预设，请选择正常的工作流会话')
        config['dsh']={'session_id':session_id}
    elif provider=='astrbot':
        from app import astrbot_client
        if not isinstance(session_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',session_id):
            raise ValueError('请先选择 AstrBot 会话')
        config_id=config_id or 'default'
        if not isinstance(config_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',config_id):
            raise ValueError('AstrBot 人设配置无效')
        state=astrbot_client.state()
        if not state.get('online'):
            raise ValueError('AstrBot 未连接，请先启动 AstrBot 服务')
        if not any(x['id']==session_id for x in state['sessions']):
            raise ValueError('AstrBot 会话不在当前可用列表')
        if not any(x['id']==config_id for x in state['configs']):
            raise ValueError('AstrBot 人设配置不在当前可用列表')
        config['astrbot']={'session_id':session_id,'config_id':config_id}
    elif session_id is not None:
        if not isinstance(session_id,str) or session_id not in {t['id'] for t in read_threads()}:
            raise ValueError('请选择 Work 1 项目内的未归档会话')
        config['mode']='explicit'
        config['thread_id']=session_id
        candidates=config.get('candidates',[])
        config['candidates']=list(dict.fromkeys([*candidates,session_id]))
    config['provider']=provider
    _atomic_json(TARGET_FILE,config)
    return target_state()


def set_policy(name, patch):
    if name not in POLICY_FILES:
        raise ValueError('unknown policy')
    filename, allowed = POLICY_FILES[name]
    if not isinstance(patch, dict) or set(patch) - allowed or not patch:
        raise ValueError('disallowed policy key')
    config = _read_json(ROOT / filename, {})
    if not isinstance(config, dict):
        config = {}
    config.update(patch)
    _atomic_json(ROOT / filename, config)
    return config


def workflow_action(action):
    if action in ('start', 'restart'):
        return {'ok': True, 'action': action, 'started': ensure_children()}
    if action == 'stop':
        stop_children()
        return {'ok': True, 'action': action}
    raise ValueError('unknown workflow action')


_APP_STARTED = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = 'DouyinConsole/1'

    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        payload = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split('?')[0]
        try:
            if self.headers.get('X-Whale-Bridge'):
                from app import whale_channel
                if path not in whale_channel.GET_ROUTES or not whale_channel.authenticate(self.headers.get('X-Whale-Bridge'),self.headers.get('X-Whale-Device'),management=True):
                    self._send(403,{'error':'Remote management denied'});return
            if path == '/':
                self._send(200, ui_document()[0], 'text/html; charset=utf-8')
            elif path in ('/favicon.ico', '/icon.ico'):
                self._send(200, ICON_FILE.read_bytes(), 'image/x-icon')
            elif path == '/api/overview':
                self._send(200, overview_state())
            elif path == '/api/app/ping':
                self._send(200, {'ok': True, 'pid': os.getpid(), 'ui_revision': ui_document()[1], 'local_image_batch_version': 1, 'whale_channel_version':1, 'whale_audio_version':1})
            elif path == '/api/channels':
                from app import whale_channel
                with closing(sqlite3.connect(ASSISTANT_DB,timeout=3)) as db:
                    value=whale_channel.state(db)
                    if self.headers.get('X-Whale-Bridge'):value['current_device']=self.headers.get('X-Whale-Device')
                    self._send(200,value)
            elif path == '/api/threads':
                self._send(200, {'threads': read_threads(), 'target': target_state()})
            elif path == '/api/agents/dsh':
                self._send(200,agent_sessions_state())
            elif path == '/api/activity':
                from app.autonomy import activity_state
                with sqlite3.connect(ASSISTANT_DB,timeout=3) as activity_db:
                    self._send(200,activity_state(activity_db))
            elif path == '/api/agents/astrbot':
                self._send(200,astrbot_state())
            elif path == '/api/tasks':
                self._send(200, tasks_state())
            elif path == '/api/outbound':
                self._send(200,outbound_state())
            elif path == '/api/comments':
                self._send(200, comments_state())
            elif path == '/api/comment-login':
                from app.comment_login import status
                self._send(200,status())
            elif path == '/api/logs':
                self._send(200, logs_state())
            elif path == '/api/storage':
                from app.resource_storage import settings
                self._send(200,settings())
            else:
                self._send(404, {'error': 'not found'})
        except Exception as exc:
            self._send(500, {'error': type(exc).__name__})

    def do_POST(self):
        path = self.path.split('?')[0]
        from app import whale_channel
        bridge_token=self.headers.get('X-Whale-Bridge')
        bridge_device=self.headers.get('X-Whale-Device')
        if path=='/api/whale-bridge/enroll':
            if not whale_channel.authenticate_secret(bridge_token):
                self._send(403,{'error':'Whale bridge denied'});return
            try:
                self._send(200,whale_channel.enroll(bridge_device))
            except ValueError as exc:
                self._send(400,{'error':str(exc)})
            return
        if path=='/api/whale-bridge' or bridge_token:
            if not whale_channel.authenticate(bridge_token,bridge_device,management=path!='/api/whale-bridge') or (path!='/api/whale-bridge' and path not in whale_channel.POST_ROUTES):
                self._send(403,{'error':'Whale bridge denied'});return
        guarded = path.startswith('/api/storage') or path.startswith('/api/astrbot') or path in ('/api/agent','/api/review','/api/comment-login','/api/activity','/api/local-image','/api/channels','/api/whale-bridge')
        if guarded:
            origin=self.headers.get('Origin')
            if self.headers.get('Host') not in ('127.0.0.1:8793','localhost:8793') or (origin and origin not in ('http://127.0.0.1:8793','http://localhost:8793')) or 'application/json' not in self.headers.get('Content-Type',''):
                self._send(403,{'error':'Only local JSON management requests are allowed'});return
        length = int(self.headers.get('Content-Length') or 0)
        if guarded and not 0<=length<=32768:
            self._send(400,{'error':'Request too large'});return
        raw = self.rfile.read(length) if length else b'{}'
        try:
            body = json.loads(raw.decode('utf-8') or '{}')
        except ValueError:
            self._send(400, {'error': 'bad json'})
            return
        try:
            if path == '/api/whale-bridge':
                with closing(sqlite3.connect(ASSISTANT_DB,timeout=3)) as db:
                    action=body.get('action')
                    if action=='receive':self._send(200,whale_channel.receive(db,bridge_device,body.get('id'),body.get('text')))
                    elif action=='upload':
                        from app.whale_upload import receive
                        self._send(200,receive(db,bridge_device,body.get('id'),body.get('name'),body.get('sha256')))
                    elif action=='attachment':
                        from app.whale_upload import attachment
                        payload,mime=attachment(db,bridge_device,body.get('seq'));self._send(200,payload,mime)
                    elif action in ('state','poll'):self._send(200,whale_channel.history(db,bridge_device,body.get('since') if action=='poll' else None))
                    elif action=='image':
                        payload,mime=whale_channel.image(db,bridge_device,body.get('seq'));self._send(200,payload,mime)
                    elif action=='audio':
                        payload,mime=whale_channel.audio(db,bridge_device,body.get('seq'));self._send(200,payload,mime)
                    else:self._send(400,{'error':'Unknown bridge action'})
            elif path == '/api/channels':
                if bridge_token and body.get('sync_paired'):
                    self._send(403,{'error':'Device enrollment is local-only'});return
                with closing(sqlite3.connect(ASSISTANT_DB,timeout=3)) as db:self._send(200,whale_channel.configure(db,body))
            elif path == '/api/comment-login':
                from app.comment_login import start
                self._send(200,start(_stop))
            elif path == '/api/review':
                raise ValueError('人工核验入口已停用，程序自动根据回执和已有反馈判断')
            elif path == '/api/activity':
                from app.autonomy import save
                self._send(200,{'ok':True,'config':save(body.get('config'))})
            elif path == '/api/local-image':
                if not isinstance(body.get('enabled'),bool):raise ValueError('enabled must be boolean')
                from app.resource_storage import atomic
                from app.local_images import settings
                atomic(ROOT/'local-image.json',{**settings(),'enabled':body['enabled']})
                self._send(200,{'ok':True})
            elif path == '/api/storage':
                from app.resource_storage import save
                self._send(200,save(body.get('config'),body.get('confirm_cleanup') is True))
            elif path == '/api/storage/preview':
                from app.resource_storage import preview
                self._send(200,preview())
            elif path == '/api/storage/cleanup':
                from app.resource_storage import run_preview
                if body.get('confirmed') is not True:raise ValueError('请确认预览中的资源删除')
                self._send(200,run_preview(body.get('token')))
            elif path == '/api/target':
                self._send(200, {'ok': True, 'target': set_target(body.get('action'), body.get('thread_id'))})
            elif path == '/api/agent':
                self._send(200,{'ok':True,'target':set_agent(body.get('provider'),body.get('session_id'),body.get('config_id'))})
            elif path == '/api/astrbot/session':
                from app import astrbot_client
                self._send(200,{'ok':True,'session':astrbot_client.create_session(body.get('title'))})
            elif path == '/api/astrbot/start':
                self._send(200,start_astrbot())
            elif path == '/api/policy':
                self._send(200, {'ok': True, 'policy': set_policy(body.get('name'), body.get('patch') or {})})
            elif path == '/api/workflow':
                self._send(200, workflow_action(body.get('action')))
            elif path == '/api/app/quit':
                self._send(200, {'ok': True, 'message': 'stopping workflow and console'})
                _stop.set()
                # A safety net first: even if stopping children misbehaves, the
                # console must not keep the old build alive and block the next
                # launch from loading the current code.
                threading.Timer(20, lambda: os._exit(0)).start()
                try:
                    stop_children()
                except Exception:
                    pass
                threading.Timer(0.4, lambda: os._exit(0)).start()
            else:
                self._send(404, {'error': 'not found'})
        except ValueError as exc:
            self._send(400, {'error': str(exc)})
        except Exception as exc:
            self._send(500, {'error': type(exc).__name__})


def main():
    from app.console_visibility import hide_console
    hide_console()
    # Bind before any worker thread starts; a duplicate manager must not run jobs.
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    ensure_children()
    threading.Thread(target=supervisor_loop, name='workflow-supervisor', daemon=True).start()
    from app.resource_storage import cleanup_loop
    threading.Thread(target=cleanup_loop,args=(_stop,),name='resource-cleanup',daemon=True).start()
    from app.local_images import loop as local_image_loop
    threading.Thread(target=local_image_loop,args=(_stop,),name='local-image-jobs',daemon=True).start()
    print(f'console on http://{HOST}:{PORT}/ (pid {os.getpid()})', flush=True)
    try:
        server.serve_forever()
    finally:
        _stop.set()
        from app.local_images import shutdown
        shutdown()


if __name__ == '__main__':
    main()
