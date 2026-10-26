"""Detach only this server's console; never hide unrelated terminal windows."""
import os,sys
from pathlib import Path

def hide_console():
    if os.name!='nt':return {'detached':False,'reason':'not_windows'}
    import ctypes
    from ctypes import wintypes
    from app.live_service import ROOT
    ROOT.mkdir(parents=True,exist_ok=True)
    # A detached console handle becomes invalid: redirect before detaching.
    for name,filename in (('stdout','console.stdout.log'),('stderr','console.stderr.log')):
        old=getattr(sys,name)
        if old is not None:
            try:old.flush()
            except (OSError,ValueError):pass
        setattr(sys,name,(ROOT/filename).open('a',encoding='utf-8',buffering=1))
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetConsoleWindow.argtypes=[];kernel.GetConsoleWindow.restype=wintypes.HWND
    kernel.FreeConsole.argtypes=[];kernel.FreeConsole.restype=wintypes.BOOL
    before=bool(kernel.GetConsoleWindow())
    detached=bool(kernel.FreeConsole()) if before else True
    return {'pid':os.getpid(),'had_console':before,'detached':detached,'console_attached':bool(kernel.GetConsoleWindow())}
