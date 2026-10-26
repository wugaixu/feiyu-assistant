"""Manage only the bound dedicated browser's window visibility, never its lifetime."""
import ctypes
from ctypes import wintypes as W
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(r'__RUNTIME_ROOT__')
CONFIG=ROOT/'window-visibility-config.json'


@contextmanager
def control_lock():
    import msvcrt
    with (ROOT/'window-visibility-control.lock').open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
        try:yield
        finally:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


def write(path,value):
    tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)


class Native:
    def __init__(self):
        if os.name!='nt':raise RuntimeError('Windows required')
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];self.kernel.OpenProcess.restype=W.HANDLE
        self.kernel.CloseHandle.argtypes=[W.HANDLE]
        self.kernel.GetProcessTimes.argtypes=[W.HANDLE]+[ctypes.POINTER(W.FILETIME)]*4
        self.kernel.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)]
        self.user.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        self.user.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,ctypes.c_int]
        self.user.GetWindow.argtypes=[W.HWND,W.UINT];self.user.GetWindow.restype=W.HWND
        self.user.IsWindowVisible.argtypes=[W.HWND];self.user.IsWindowVisible.restype=W.BOOL
        self.user.ShowWindowAsync.argtypes=[W.HWND,ctypes.c_int];self.user.ShowWindowAsync.restype=W.BOOL

    def identity(self,pid):
        handle=self.kernel.OpenProcess(0x1000,False,pid)
        if not handle:raise RuntimeError('Bound browser process unavailable')
        try:
            created,ended,kernel,user=(W.FILETIME() for _ in range(4))
            if not self.kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in (created,ended,kernel,user)]):raise ctypes.WinError(ctypes.get_last_error())
            name=ctypes.create_unicode_buffer(32768);size=W.DWORD(len(name))
            if not self.kernel.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
            return {'pid':pid,'created_filetime':(created.dwHighDateTime<<32)|created.dwLowDateTime,'executable':name.value}
        finally:self.kernel.CloseHandle(handle)

    def windows(self,pid):
        found=[]
        callback=ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        def collect(hwnd,_):
            owner=W.DWORD();self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
            name=ctypes.create_unicode_buffer(256);self.user.GetClassNameW(hwnd,name,len(name))
            if owner.value==pid and name.value=='Chrome_WidgetWin_1' and not self.user.GetWindow(hwnd,4):
                found.append({'hwnd':int(hwnd),'visible':bool(self.user.IsWindowVisible(hwnd))})
            return True
        cb=callback(collect)
        self.user.EnumWindows.argtypes=[callback,W.LPARAM]
        self.user.EnumWindows(cb,0)
        return found

    def show(self,hwnd,visible):
        # Async avoids blocking when a browser UI thread is temporarily busy.
        self.user.ShowWindowAsync(hwnd,9 if visible else 0)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def fault(now=None):
    from app.local_runtime import enabled as local_enabled
    if local_enabled():return None  # Old browser is retained, but no longer a workflow dependency.
    now=now or time.time()
    for filename in ('live-service-status.json','listener-status.json'):
        try:value=read(ROOT/filename)
        except (OSError,ValueError):return 'listener_status_unavailable'
        if now-value.get('updated_at',0)>90:return 'listener_heartbeat_stale'
        if value.get('state') in ('waiting_manual_login','stopped','failed','disconnected'):
            return value['state']
        # One media timeout is recoverable in-process, not a request for a
        # visible window. Persistent recovery still exposes the window.
        if value.get('state') == 'waiting_recovery' and now-value.get('updated_at',now)>30:
            return 'waiting_recovery'
    if (ROOT/'video-auth-required.json').exists():return 'video_identity_verification'
    path=ROOT/'window-browser-health.json'
    if path.exists():
        try:
            value=read(path)
            if now-value.get('updated_at',0)>90:return 'browser_health_stale'
            if value.get('needs_attention'):return value.get('reason','browser_needs_attention')
        except (OSError,ValueError):return 'browser_health_unavailable'
    return None


def validate(native,config):
    actual=native.identity(config['binding']['pid'])
    if actual!=config['binding'] or Path(actual['executable']).name.lower()!='msedge.exe':
        raise RuntimeError('Browser identity changed; refusing to touch another process')


def apply(native,config,visible):
    validate(native,config)
    windows=native.windows(config['binding']['pid'])
    if not windows:raise RuntimeError('No windows for bound dedicated browser')
    for window in windows:
        if window['visible']!=visible:native.show(window['hwnd'],visible)
    return windows


def bind(native,pid):
    binding=native.identity(pid)
    if Path(binding['executable']).name.lower()!='msedge.exe':raise ValueError('Dedicated Edge required')
    if CONFIG.exists():raise ValueError('Existing binding preserved; inspect it instead')
    value={'binding':binding,'intent':'visible','attention_latched':False,'bound_at':time.time(),
           'profile':str(ROOT/'edge-profile')}
    write(CONFIG,value)
    return value


def command(action,native=None):
    with control_lock():return command_locked(action,native)


def command_locked(action,native=None):
    native=native or Native();config=read(CONFIG)
    validate(native,config)
    if action not in ('hide','show','status'):raise ValueError('Unknown window command')
    if action=='hide':
        reason=fault()
        if reason:raise RuntimeError('Repair before hiding: '+reason)
        config.update(intent='hidden',attention_latched=False,last_attention=None)
    elif action=='show':config.update(intent='visible',attention_latched=False)
    if action!='status':
        write(CONFIG,config)
        apply(native,config,action=='show')
        time.sleep(.3)
    validate(native,config)
    windows=native.windows(config['binding']['pid'])
    return {'intent':config['intent'],'attention_latched':config.get('attention_latched',False),
            'pid':config['binding']['pid'],'windows':windows,'all_hidden':bool(windows) and all(not w['visible'] for w in windows)}


def guard_step(native=None,attention_override=None):
    with control_lock():return guard_step_locked(native,attention_override)


def guard_step_locked(native=None,attention_override=None):
    native=native or Native();config=read(CONFIG)
    if attention_override not in (None,'recovery_self_test'):raise ValueError('Unknown recovery self-test')
    reason=attention_override or fault()
    if reason and config['intent']=='hidden':
        # Latch visible. A healthy heartbeat must never hide a repair window again.
        config.update(intent='visible',attention_latched=True,last_attention=reason,attention_at=time.time())
        write(CONFIG,config)
        apply(native,config,True)
    elif config['intent']=='hidden':apply(native,config,False)
    return command_locked('status',native) | {'updated_at':time.time(),'fault':reason}


def observe_browser(context):
    if not CONFIG.exists():return
    reason=None
    try:
        for page in context.pages:
            if page.is_closed() or not page.url.startswith('https://www.douyin.com/'):continue
            value=page.evaluate('''()=>{const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
            return [...document.querySelectorAll('input[placeholder="请输入验证码"],[id^="login-full-panel"],iframe[src*="captcha"],.captcha_verify_container')].some(visible)}''')
            if value:reason='visible_login_or_verification';break
    except Exception:reason='browser_health_read_failed'
    write(ROOT/'window-browser-health.json',{'updated_at':time.time(),'needs_attention':bool(reason),'reason':reason})
