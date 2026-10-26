"""Start the independently installed AstrBot without a console or auto-start entry."""
import json
import os
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(r'__RUNTIME_ROOT__\astrbot')

def main():
    try:
        with urllib.request.urlopen('http://127.0.0.1:6185/', timeout=2) as response:
            if response.status == 200:
                return
    except OSError:
        pass
    runtime = ROOT / 'runtime'
    executable = ROOT / 'tools' / 'astrbot' / 'Scripts' / 'python.exe'
    env = dict(os.environ, ASTRBOT_ROOT=str(runtime), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', TEMP=str(ROOT / 'temp'), TMP=str(ROOT / 'temp'))
    (ROOT / 'temp').mkdir(exist_ok=True)
    with (ROOT / 'service.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen([str(executable), '-B', '-m', 'astrbot.cli', 'run'], cwd=runtime, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    (ROOT / 'process.json').write_text(json.dumps({'pid': process.pid, 'url': 'http://127.0.0.1:6185/', 'version': '4.29.1'}, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
