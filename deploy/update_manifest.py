"""Refresh hashes of publishable Git files; never include ignored runtime files."""
import hashlib,json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
raw=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root)
files=sorted(set(raw.decode('utf-8').split('\0'))-{'' ,'SOURCE_MANIFEST.json'})
manifest={}
for name in files:
    path=root/name
    if path.is_symlink():raise ValueError('Symlink is not a release input: '+name)
    if path.is_file():manifest[name]=hashlib.sha256(path.read_bytes()).hexdigest()
(root/'SOURCE_MANIFEST.json').write_text(json.dumps({'version':'0.3.2','files':manifest},ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
print('Source manifest files:',len(manifest))
