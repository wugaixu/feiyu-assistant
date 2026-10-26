"""User-triggered main-site login check. No publishing and no SDK auth checks."""
import threading,time
from app.sdk_receiver import ACCOUNT_UID

_lock=threading.Lock()
_thread=None
_recheck=threading.Event()
_state={'state':'not_checked','message':'尚未检查抖音主站登录'}


def status():
    with _lock:return dict(_state)


def _set(state,message,**extra):
    with _lock:
        _state.clear();_state.update(state=state,message=message,checked_at=time.time(),**extra)


def classify(identity):
    uid=str(identity.get('user_uid') or '')
    if uid==ACCOUNT_UID:return {'state':'logged_in','message':'抖音主站已登录，账号与发布小号一致','nickname':identity.get('nickname','')}
    if uid and uid!='0':return {'state':'wrong_account','message':'网页登录的不是发布小号，请在打开的窗口切换账号'}
    return {'state':'needs_login','message':'主站未取得有效登录，请在打开的抖音窗口手动登录小号'}


def _run(stop_event,browser_factory=None):
    from app.browser_publish import HiddenCommentBrowser
    from app.comment_profile import ProfileBusy
    browser=(browser_factory or HiddenCommentBrowser)()
    try:
        with browser:
            try:identity=browser.self_identity()
            except ValueError:identity={}
            result=classify(identity)
            if result['state']=='logged_in':_set(**result);return
            browser.show_for_login()
            _set(**result,window_open=True)
            # Keep this one window and profile alive while the user logs in.
            # No reloads, SMS actions, CAPTCHA solving, or second browser launch.
            while not stop_event.wait(3):
                if browser.page.is_closed():_set('window_closed','登录窗口已关闭，可点击重新检查');return
                try:
                    if _recheck.is_set():
                        _recheck.clear();identity=browser.self_identity()
                    else:identity=browser.read_identity()
                except ValueError:
                    _set('needs_login','请在抖音窗口完成登录或验证；完成后可点击重新检查',window_open=True);continue
                result=classify(identity)
                if result['state']=='logged_in':_set(**result);return
                _set(**result,window_open=True)
            _set('stopped','管理程序正在关闭')
    except ProfileBusy as exc:_set('busy',str(exc))
    except Exception as exc:_set('error','登录检查未完成，请稍后重试',error_class=type(exc).__name__)


def start(stop_event):
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            _recheck.set();return dict(_state)
        _recheck.clear()
        _state.clear();_state.update(state='checking',message='正在检查抖音主站登录，请稍候',checked_at=time.time())
        _thread=threading.Thread(target=_run,args=(stop_event,),name='comment-main-site-login',daemon=True)
        _thread.start()
        return dict(_state)
