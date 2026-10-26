"""Store an AstrBot-local API key through a hidden prompt, never a model provider key."""
import argparse,getpass,json,urllib.request
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime-dir',required=True,type=Path);args=p.parse_args()
root=args.runtime_dir.resolve();cfg=json.loads((root/'deployment.json').read_text(encoding='utf-8'))
key=getpass.getpass('AstrBot local API key (abk_..., not model API key): ').strip()
if not key.startswith('abk_'):raise SystemExit('Expected the AstrBot local API key')
req=urllib.request.Request('http://127.0.0.1:6185/api/v1/chat/configs',headers={'Authorization':'Bearer '+key})
with urllib.request.urlopen(req,timeout=8) as response:
    if json.load(response).get('status')!='ok':raise SystemExit('AstrBot did not accept this key')
dest=root/'astrbot-bridge.json'
if dest.exists():raise SystemExit('Bridge config exists; back it up before replacing')
dest.write_text(json.dumps({'api_key':key,'username':'douyin-owner-'+cfg['owner_uid']},indent=2),encoding='utf-8')
print('Local bridge saved. Open manager → 会话投递 → AstrBot → create/select a session.')
