"""Start the independently installed AstrBot without a console or auto-start entry."""
import json
import os
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(r'__RUNTIME_ROOT__\astrbot')

def main():
    global ROOT
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--instance',default='default')
    args=parser.parse_args()
    url='http://127.0.0.1:6185/'
    executable=ROOT/'tools/astrbot/Scripts/python.exe'
    if args.instance!='default':
        from app.astrbot_instances import info
        value=info(args.instance);ROOT=value['root'];url=value['url']
        executable=ROOT/'tools/astrbot/Scripts/python.exe'
        if not executable.is_file():raise RuntimeError('Independent AstrBot environment is not installed')
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status == 200:
                return
    except OSError:
        pass
    runtime = ROOT / 'runtime'
    # Each independent instance has its own dependency environment and writable data.
    env = dict(os.environ, ASTRBOT_ROOT=str(runtime), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', TEMP=str(ROOT / 'temp'), TMP=str(ROOT / 'temp'))
    (ROOT / 'temp').mkdir(exist_ok=True)
    with (ROOT / 'service.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen([str(executable), '-B', '-m', 'astrbot.cli', 'run'], cwd=runtime, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    (ROOT / 'process.json').write_text(json.dumps({'pid': process.pid, 'url': url, 'version': '4.29.1'}, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
