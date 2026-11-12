"""Standalone hidden launcher for the lightweight manual image portal.

No AI provider, owner task, permit, database or messaging outbox is involved.
Opening starts or reuses only the page service. The portal owns model wake-up and idle exit.
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
    # The portal owns backend wake-up and idle exit. Merely opening its page
    # must not require ComfyUI, GPU initialization or workflow installation.
    ensure_portal()
    if not args.no_browser:
        open_app()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        log = local_images.BASE / 'manual-launch-error.txt'
        log.write_text(type(exc).__name__ + ': ' + str(exc), encoding='utf-8')
        if sys.platform == 'win32':
            ctypes.windll.user32.MessageBoxW(None, '本地生图启动失败，请查看：\n' + str(log), '本地生图', 16)
        raise
