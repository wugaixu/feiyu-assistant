"""Standalone hidden launcher for the installed official ComfyUI App Mode.

No AI provider, owner task, permit, database or messaging outbox is involved.
The formal launcher owns only a backend it starts; repeated opens reuse it.
"""
import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from app import local_images

PROJECT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT / 'integrations/comfy_manual'
APP_URL = 'http://127.0.0.1:8189/'


def ensure_portal():
    """Start the installed loopback portal independently of the browser."""
    import urllib.request
    def online():
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open(APP_URL + 'health', timeout=2) as response:
                return json.load(response).get('service') == 'manual-qwen-portal'
        except Exception:
            return False
    if online():
        return
    py = local_images.BASE / 'venv/Scripts/pythonw.exe'
    with (local_images.BASE / 'manual-portal.log').open('a', encoding='utf-8') as log:
        subprocess.Popen([str(py), '-B', '-X', 'utf8', '-m', 'app.manual_portal'],
                         cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 20
    while not online():
        if time.monotonic() > deadline:
            raise RuntimeError('手动生图页面启动失败，请查看 manual-portal.log')
        time.sleep(.25)


def install_assets():
    """Add an opt-in frontend extension, leaving vendor files untouched."""
    site = local_images.BASE / 'venv/Lib/site-packages/comfyui_frontend_package/static'
    if not (site / 'scripts/app.js').is_file():
        raise RuntimeError('ComfyUI frontend is missing')
    dest = site / 'extensions/feiyu_manual'
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / 'manual.js', dest / 'manual.js')
    graph = local_images.workflow('一只圆滚滚的蓝色小鲸鱼，清新插画，柔和光线，简洁背景')
    graph['8']['inputs']['filename_prefix'] = 'manual-qwen'
    (dest / 'workflow.json').write_text(json.dumps(graph, ensure_ascii=False), encoding='utf-8')
    return dest


def open_app():
    webbrowser.open(APP_URL, new=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    mutex = None
    if sys.platform == 'win32':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        mutex = kernel.CreateMutexW(None, False, 'Local\\FeiyuManualComfyUI')
        if not mutex:
            raise OSError(ctypes.get_last_error(), 'Cannot create launcher mutex')
        duplicate = ctypes.get_last_error() == 183
    else:
        duplicate = False
    try:
        if not duplicate:
            install_assets()
        deadline = time.monotonic() + 180
        while True:
            try:
                if duplicate:
                    local_images.api('/system_stats', timeout=2)
                    ready = True
                else:
                    ready = local_images.ensure_backend()
                if ready:
                    break
            except (OSError, ValueError):
                if not duplicate:
                    raise
            if time.monotonic() >= deadline:
                raise TimeoutError('ComfyUI startup exceeded 180 seconds; inspect comfyui.log')
            time.sleep(1)
        ensure_portal()
        if not args.no_browser:
            open_app()
        local_images.touch('manual-open')
        # This is the installed GUI launcher, not a tool-session subprocess.
        # It ends only the backend it started, after being idle as configured;
        # a reused backend is never terminated here.
        if not duplicate and local_images._process is not None:
            while local_images._process is not None and local_images._process.poll() is None:
                if local_images.idle_expired():
                    local_images.stop_backend('manual-idle')
                    break
                time.sleep(5)
    finally:
        if not duplicate:
            local_images.shutdown()
        if mutex:
            kernel.CloseHandle(mutex)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        log = local_images.BASE / 'manual-launch-error.txt'
        log.write_text(type(exc).__name__ + ': ' + str(exc), encoding='utf-8')
        if sys.platform == 'win32':
            ctypes.windll.user32.MessageBoxW(None, '本地生图启动失败，请查看：\n' + str(log), '本地生图', 16)
        raise
