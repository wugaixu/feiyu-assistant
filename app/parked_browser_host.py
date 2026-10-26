"""Keep an already-open Edge alive while retiring its legacy UI work loop.

Used once during live migration; standalone startup never imports or starts Edge.
The retained Playwright context stays owned by its original process.
"""
import time

class ParkedPage:
    def is_closed(self):return False
    def locator(self,selector):
        class Marker:
            def is_visible(self):return selector.startswith('[data-stack-layer="chat"]')
        if selector not in ('[id^="login-full-panel"]','[data-stack-layer="chat"] .StackLayoutStackChatHeaderuser'):
            raise RuntimeError('Legacy browser operations retired')
        return Marker()
    def wait_for_timeout(self,milliseconds):time.sleep(milliseconds/1000)

def park_existing_host():
    import sys,__main__
    from pathlib import Path
    frames=list(sys._current_frames().values())
    # Playwright's sync caller is suspended in a parent greenlet while its
    # event loop services remote_exec; that stack is not in _current_frames.
    from greenlet import getcurrent
    current=getcurrent()
    while current:
        if current.gr_frame:frames.append(current.gr_frame)
        current=current.parent
    for frame in frames:
        while frame:
            if frame.f_code.co_name=='main' and Path(frame.f_code.co_filename).name=='listen.py':
                page=frame.f_locals.get('page')
                if page is not None:
                    __main__._retained_browser_page=page
                    frame.f_locals['page']=ParkedPage()
                    return True
            frame=frame.f_back
    return False
