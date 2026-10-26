"""Explicit optional downloads. No login, secret import or service startup."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,sys,urllib.request
REPO=Path(__file__).resolve().parents[1]
def run(args,cwd=None):subprocess.run([str(x) for x in args],cwd=cwd,check=True)
def download(url,dest,digest):
    def checksum(p):
        with p.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    if dest.exists() and checksum(dest)==digest:return
    dest.parent.mkdir(parents=True,exist_ok=True);partial=dest.with_suffix(dest.suffix+'.partial')
    with urllib.request.urlopen(url,timeout=90) as response,partial.open('wb') as target:shutil.copyfileobj(response,target)
    if checksum(partial)!=digest:raise ValueError('SHA256 mismatch: '+dest.name)
    partial.replace(dest)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime-dir',required=True,type=Path);p.add_argument('--component',required=True,choices=['sdk','asr','astrbot'])
    a=p.parse_args();root=a.runtime_dir.resolve();root.mkdir(parents=True,exist_ok=True)
    npm=shutil.which('npm.cmd') or shutil.which('npm')
    if a.component in ('sdk','asr') and not npm:p.error('Install Node.js 22 LTS (includes npm) first')
    if a.component=='sdk':
        for name in ('package.json','package-lock.json'):
            source=REPO/'deploy/sdk'/name;target=root/name
            if target.exists() and target.read_bytes()!=source.read_bytes():raise ValueError('Existing npm metadata differs; use a clean runtime or back it up: '+name)
        for name in ('package.json','package-lock.json'):shutil.copy2(REPO/'deploy/sdk'/name,root/name)
        run([npm,'ci','--prefix',root])
    elif a.component=='asr':
        base=root/'tools/sensevoice';base.mkdir(parents=True,exist_ok=True)
        run([npm,'install','--prefix',base,'--save-exact','sherpa-onnx-node@1.13.8'])
        manifest=json.loads((REPO/'deploy/sensevoice-assets.json').read_text(encoding='utf-8'))
        for item in (manifest['models']['int8'],manifest['tokens']):download(item['url'],base/'models'/item['name'],item['sha256'])
    else:
        base=root/'astrbot';venv=base/'tools/astrbot';runtime=base/'runtime';runtime.mkdir(parents=True,exist_ok=True)
        run([sys.executable,'-m','venv',venv]);python=venv/'Scripts/python.exe'
        run([python,'-m','pip','install','astrbot==4.29.1','imageio-ffmpeg==0.6.0','Pillow==12.3.0'])
        run([python,'-m','astrbot.cli','init'],cwd=runtime)
        print('Set AstrBot dashboard to 127.0.0.1:6185 before using the manager. See docs/DEPLOYMENT.md.')
if __name__=='__main__':main()
