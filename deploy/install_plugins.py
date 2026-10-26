"""Copy code-only adapters into an initialized AstrBot; does not restart it."""
import argparse,json,shutil
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime-dir',required=True,type=Path);p.add_argument('--with-gpt-sovits',action='store_true');a=p.parse_args()
root=a.runtime_dir.resolve();cfg=json.loads((root/'deployment.json').read_text(encoding='utf-8'));installed=Path(cfg['installed'])
dest=root/'astrbot/runtime/data/plugins';dest.mkdir(parents=True,exist_ok=True)
sources=[(installed/'integrations/astrbot_local_image','douyin_local_image')]
if a.with_gpt_sovits:sources.append((installed/'third_party/astrbot_plugin_GPT_SoVITS','astrbot_plugin_GPT_SoVITS'))
if any((dest/name).exists() for _,name in sources):raise SystemExit('Plugin exists; back up before replacing. No files changed.')
for source,name in sources:
    shutil.copytree(source,dest/name,ignore=shutil.ignore_patterns('README.md','*.txt','*.md','__pycache__'))
print('Adapters copied. Enable/configure them in AstrBot; perform a normal restart when idle.')
