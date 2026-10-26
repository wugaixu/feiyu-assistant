"""Start only the SDK transport and standalone Python worker, hidden on Windows."""
import json,os,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
from app.live_service import ROOT

def main():
    project=Path(__file__).resolve().parent
    try:
        with urllib.request.urlopen('http://127.0.0.1:8791/health',timeout=2) as response:json.load(response)
    except Exception:
        node=shutil.which('node')
        if not node:raise RuntimeError('Node.js runtime missing')
        subprocess.Popen([node,str(project/'sdk_service.mjs')],cwd=ROOT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
            stdout=(ROOT/'sdk-local.stdout.log').open('ab'),stderr=(ROOT/'sdk-local.stderr.log').open('ab'))
    env=os.environ.copy();env['PYTHONPYCACHEPREFIX']=str(ROOT/'pycache')
    subprocess.Popen([sys.executable,'-B','-X','utf8','-m','app.sdk_receiver'],cwd=project,env=env,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
        stdout=(ROOT/'local-runtime.stdout.log').open('ab'),stderr=(ROOT/'local-runtime.stderr.log').open('ab'))
    print('Local SDK service and single-instance worker requested; no browser launched.')

if __name__=='__main__':main()
