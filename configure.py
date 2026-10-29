"""Compile a relocatable deployment from this clean source snapshot; never start services."""
import argparse,json,os,re,secrets,shutil,sys
from pathlib import Path
VERSION='0.3.0'
REPO=Path(__file__).resolve().parent

def safe_path(value):
    path=Path(value).expanduser().resolve()
    if any(c in str(path) for c in "'\"\n\r`$"):raise ValueError('Path contains unsupported shell/source characters')
    return path

def configure(cfg):
    runtime=safe_path(cfg['runtime_dir']);installed=runtime/'installed'/VERSION
    if runtime.is_relative_to(REPO):raise ValueError('Runtime must be outside the public source repository')
    if installed.exists():raise ValueError('Deployment already exists. Back up first; choose a new runtime for a clean install.')
    for key in ('owner_uid','account_uid'):
        if not re.fullmatch(r'\d{1,25}',cfg[key]):raise ValueError(key+' must be a numeric UID, not a nickname or Douyin ID')
    if not re.fullmatch(r'/user/[A-Za-z0-9_-]{8,180}',cfg['owner_profile']):raise ValueError('owner_profile must be /user/<sec_uid>')
    workspace=safe_path(cfg['codex_workspace']);user=Path.home();dsh=safe_path(cfg.get('dsh_home') or user/'.dsh')
    nickname=cfg.get('owner_nickname') or '主人'
    if not isinstance(nickname,str) or len(nickname)>80 or any(c in nickname for c in "'\"\\\n\r"):raise ValueError('owner_nickname contains unsupported quote/control characters')
    python=safe_path(cfg.get('python') or sys.executable)
    values={'__RUNTIME_ROOT__':runtime.as_posix(),'__PROJECT_DIR__':installed.as_posix(),'__PROJECT_URI__':installed.as_uri(),
      '__OWNER_UID__':cfg['owner_uid'],'__ACCOUNT_UID__':cfg['account_uid'],'__OWNER_PROFILE__':cfg['owner_profile'],'__OWNER_NICKNAME__':nickname,
      '__CODEX_HOME__':safe_path(cfg.get('codex_home') or user/'.codex').as_posix(),'__CODEX_WORKSPACE__':workspace.as_posix(),
      '__USER_HOME__':user.as_posix(),'__DSH_HOME__':dsh.as_posix(),'__DSH_RESOURCES__':safe_path(cfg.get('dsh_resources') or dsh/'resources').as_posix(),
      '__PYTHON__':python.as_posix(),'__PYTHONW__':python.with_name('pythonw.exe').as_posix(),
      '__IMAGE_OUTPUT__':(runtime/'manual-images/output').as_posix(),'__TEMP_PARENT__':runtime.parent.as_posix()}
    for source in REPO.rglob('*'):
        rel=source.relative_to(REPO)
        if not source.is_file() or any(p in ('.git','__pycache__','.venv','.github','android') for p in rel.parts):continue
        if rel.name in ('deployment.local.json',) or source.suffix in ('.apk','.zip','.log','.pyc'):continue
        dest=installed/rel;dest.parent.mkdir(parents=True,exist_ok=True)
        try:
            text=source.read_text(encoding='utf-8')
            for key,value in values.items():text=text.replace(key,value)
            dest.write_text(text,encoding='utf-8')
        except UnicodeDecodeError:shutil.copy2(source,dest)
    defaults={
      'inbound-policy.json':{'standalone':True,'enabled':True,'transport':'sdk'},
      'reply-policy.json':{'drop_process_narration':False},
      'qq-channel.json':{'enabled':False,'owner_id':'','account_id':'','port':8796,'napcat_dir':'','qq_exe':''},
      'dm-policy.json':{'enabled':True,'min_send_gap_seconds':1},
      'shared-comment-policy.json':{'enabled':False,'daily_limit':10,'mention_owner':False},
      'video-round-config.json':{'enabled':False,'auto_comment':False},
      'autonomy-policy.json':{'browse_enabled':False},
      'local-image.json':{'enabled':False,'idle_minutes':10},
      'gpt-sovits.json':{'enabled':False,'voice_id':'dafeiyu','idle_minutes':5},
      'whale-channel.json':{'installed':True,'standalone':True,'enabled':True,'remote_management':False,'devices':[],'network':{'mode':'disabled'}},
      'dispatch-target.json':{'provider':'codex','mode':'explicit','thread_id':None,'candidates':[],'state_db':str(safe_path(cfg.get('codex_home') or user/'.codex')/'state_5.sqlite')},
      'dsh-bridge.json':{'token':secrets.token_hex(32)},
    }
    for name,value in defaults.items():
        p=runtime/name
        if not p.exists():p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    secret=runtime/'whale-bridge-secret.txt'
    if not secret.exists():secret.write_text(secrets.token_hex(32),encoding='utf-8')
    # Independent loopback credentials; keep existing operator-provided values.
    for name in ('qq-onebot-secret.txt','qq-webui-secret.txt'):
        target=runtime/name
        if not target.exists():target.write_text(secrets.token_hex(32),encoding='utf-8')
    for folder in ('tests','outgoing-images','outgoing-audio','backups','task-context','media','tools','manual-images/output'):(runtime/folder).mkdir(parents=True,exist_ok=True)
    (runtime/'deployment.json').write_text(json.dumps({**cfg,'installed':str(installed),'version':VERSION},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Prepared:',installed)
    print('Next: python deploy/install_components.py --runtime-dir <runtime> --component sdk')
    print('Then: install/configure your selected AI, and open',installed/'管理界面.vbs')
    return installed

def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(errors='backslashreplace')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=REPO/'deployment.local.json')
    p.add_argument('--runtime-dir');p.add_argument('--owner-uid');p.add_argument('--account-uid');p.add_argument('--owner-profile');p.add_argument('--codex-workspace')
    args=p.parse_args();cfg=json.loads(args.config.read_text(encoding='utf-8')) if args.config.exists() else {}
    for key in ('runtime_dir','owner_uid','account_uid','owner_profile','codex_workspace'):
        if getattr(args,key):cfg[key]=getattr(args,key)
        if not cfg.get(key):p.error('Missing '+key+'; copy deployment.example.json to deployment.local.json')
    configure(cfg)
if __name__=='__main__':main()
