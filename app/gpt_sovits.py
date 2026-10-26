"""Local GPT-SoVITS inference and manual UI; never sends a chat message.

The manager starts this service only after a short-lived demand signal
(``gpt_sovits.wake``) and the running service exits after being idle, so it
never holds several gigabytes of memory around the clock.
"""
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

ROOT=Path(r'__RUNTIME_ROOT__')
ENGINE=ROOT/'tools/gpt-sovits'
PROJECT=Path(__file__).resolve().parents[1]
WAKE=ROOT/'gpt-sovits-wake.json'
WAKE_SECONDS=180
_launch_at=0


def voice_profile(profile_id=None):
    """Only locally registered voices; no model/path supplied by chat text."""
    if profile_id is None:
        try:profile_id=json.loads((ROOT/'gpt-sovits.json').read_text(encoding='utf-8-sig')).get('voice_id','chii')
        except (OSError,ValueError,AttributeError):profile_id='chii'
    profiles={
        'chii':{'name':'小叽 / Chii','gpt':'chii-e10.ckpt','sovits':'chii_e10_s1210.pth','lang':'ja'},
        'dafeiyu':{'name':'大肥鱼 / OhWhale','gpt':'s1v3.ckpt','sovits':'dafeiyu_e14_s280.pth','lang':'zh'},
    }
    if not isinstance(profile_id,str) or profile_id not in profiles:raise ValueError('未登记的音色')
    value=dict(profiles[profile_id]);folder=ENGINE/'app/voices'/profile_id
    value.update(id=profile_id,gpt=str(folder/value['gpt']),sovits=str(folder/value['sovits']),
                 reference=str(folder/'ref_audio.wav'),reference_text=str(folder/'ref_text.txt'))
    return value


def settings():
    try:value=json.loads((ROOT/'gpt-sovits.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {'enabled':False,'device':'cpu','idle_minutes':5}
    idle=value.get('idle_minutes')
    if not isinstance(idle,int) or isinstance(idle,bool) or not 1<=idle<=60:idle=5
    return {'enabled':value.get('enabled') is True,
            'device':'cuda' if value.get('device')=='cuda' else 'cpu',
            'idle_minutes':idle}


def wake(reason='voice'):
    """Ask the manager to start the service; the signal expires on its own."""
    try:WAKE.write_text(json.dumps({'at':time.time(),'reason':str(reason)[:40]}),encoding='utf-8')
    except OSError:return False
    return True


def wake_due(seconds=WAKE_SECONDS):
    try:
        value=json.loads(WAKE.read_text(encoding='utf-8'))
        stamp=float(value.get('at'))
    except (OSError,ValueError,TypeError):return False
    return 0<=time.time()-stamp<=seconds


def consume_wake():
    try:WAKE.unlink()
    except OSError:pass


def ensure(spawn):
    """Start the service only while a fresh demand signal exists."""
    global _launch_at
    if not settings().get('enabled'):return False
    try:
        with socket.create_connection(('127.0.0.1',9880),timeout=.2):return False
    except OSError:pass
    if not wake_due():return False
    if time.monotonic()-_launch_at<90:return False
    python=ENGINE/'venv/Scripts/python.exe'
    try:profile=voice_profile()
    except ValueError:return False
    if not python.is_file() or not all(Path(profile[key]).is_file() for key in ('gpt','sovits','reference','reference_text')):return False
    _launch_at=time.monotonic()
    consume_wake()
    spawn([str(python),'-B','-X','utf8',str(Path(__file__)),'serve','--manager-pid',str(os.getpid())],PROJECT,'gpt-sovits-service.log')
    return True


def tts_params(text,profile_id=None):
    if not isinstance(text,str) or not 1<=len(text.strip())<=400:raise ValueError('请输入1–400字')
    profile=voice_profile(profile_id)
    params={'text':text.strip(),'text_lang':'zh','ref_audio_path':profile['reference'],
            'prompt_text':Path(profile['reference_text']).read_text(encoding='utf-8').strip(),
            'prompt_lang':profile['lang'],'media_type':'wav','streaming_mode':False,'text_split_method':'cut5',
            'batch_size':1,'parallel_infer':False,'speed_factor':1.0,'seed':42}
    if profile['id']=='dafeiyu':params.update(temperature=.7,top_p=.9,top_k=5,repetition_penalty=1.35)
    return params


def serve(manager_pid=None,port=9880,profile_id=None):
    if sys.stdout is None or sys.stderr is None:
        stream=(ROOT/'gpt-sovits-service.log').open('a',encoding='utf-8',buffering=1)
        sys.stdout=stream;sys.stderr=stream
    import runpy
    import urllib.request
    import uuid
    appdir=ENGINE/'app';os.chdir(appdir);sys.path[:0]=[str(appdir),str(appdir/'GPT_SoVITS')]
    temp=ENGINE/'temp';temp.mkdir(exist_ok=True)
    os.environ.update(TEMP=str(temp),TMP=str(temp),HF_HOME=str(ENGINE/'hf-cache'),
                      HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONUTF8='1',
                      OPEN_JTALK_DICT_DIR='../venv/Lib/site-packages/pyopenjtalk/open_jtalk_dic_utf_8-1.11',
                      NLTK_DATA=str(ENGINE/'nltk-data'),
                      OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',TOKENIZERS_PARALLELISM='false')
    shared_ffmpeg=ENGINE/'ffmpeg-shared'
    os.environ['PATH']=str(shared_ffmpeg)+os.pathsep+str(appdir)+os.pathsep+os.environ['PATH']
    dll_handles=[]
    if os.name=='nt' and shared_ffmpeg.is_dir():dll_handles.append(os.add_dll_directory(str(shared_ffmpeg)))
    cfg=settings();device=cfg['device'];idle_minutes=cfg['idle_minutes']
    profile=voice_profile(profile_id)
    infer={'custom':{'device':device,'is_half':device=='cuda','version':'v2Pro',
        'bert_base_path':'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large',
        'cnhuhbert_base_path':'GPT_SoVITS/pretrained_models/chinese-hubert-base',
        't2s_weights_path':profile['gpt'],'vits_weights_path':profile['sovits']}}
    import yaml
    config_path=ENGINE/(profile['id']+'-'+str(port)+'-infer.yaml');config_path.write_text(yaml.safe_dump(infer),encoding='utf-8')
    sys.argv=[str(appdir/'api_v2.py'),'-a','127.0.0.1','-p',str(port),'-c',str(config_path)]
    namespace=runpy.run_path(str(appdir/'api_v2.py'),run_name='gpt_sovits_api')
    # Preserve this wrapper (and its local UI) when the official control endpoint restarts.
    namespace['handle_control'].__globals__['argv']=[str(Path(__file__)),'serve','--port',str(port),'--voice',profile['id']]+(['--manager-pid',str(manager_pid)] if manager_pid else [])
    api=namespace['APP']
    # Idle watchdog: release the model memory instead of staying resident forever.
    last_seen=[time.monotonic()]
    exit_file=ENGINE/'manual-output'/'idle-exit.log'
    @api.middleware('http')
    async def _touch(request,call_next):
        last_seen[0]=time.monotonic()
        return await call_next(request)
    def _idle_guard():
        limit=idle_minutes*60
        while True:
            time.sleep(10)
            if time.monotonic()-last_seen[0]<limit:continue
            try:
                exit_file.parent.mkdir(exist_ok=True)
                exit_file.write_text(time.strftime('%Y-%m-%d %H:%M:%S')+' idle '+str(idle_minutes)+'m\n',encoding='utf-8')
            except OSError:pass
            os._exit(0)
    threading.Thread(target=_idle_guard,daemon=True).start()
    import torch
    torch.set_num_threads(4)
    output=ENGINE/'manual-output';output.mkdir(exist_ok=True)
    def synthesize(text):
        payload=json.dumps(tts_params(text,profile['id']),ensure_ascii=False).encode('utf-8')
        req=urllib.request.Request('http://127.0.0.1:'+str(port)+'/tts',data=payload,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=180) as response:data=response.read(32*1024*1024+1)
        if not data.startswith(b'RIFF') or len(data)>32*1024*1024:raise ValueError('没有取得有效WAV音频')
        path=output/(uuid.uuid4().hex+'.wav');path.write_bytes(data);return str(path)
    import gradio as gr
    ui=gr.Interface(synthesize,gr.Textbox(label='想说的话（中文）',value='你好呀，今天也想陪你聊聊天。',lines=4),
                    gr.Audio(label='点击播放试听',type='filepath',autoplay=False),
                    title=profile['name']+' · 本地语音',description='GPT-SoVITS v2Pro｜直接输入文字，无需聊天模型。音色来源与使用说明见项目 integrations/gpt_sovits。空闲 '+str(idle_minutes)+' 分钟自动退出。',
                    allow_flagging='never',analytics_enabled=False)
    api=gr.mount_gradio_app(api,ui,path='/voice',allowed_paths=[str(output)])
    import uvicorn
    server=uvicorn.Server(uvicorn.Config(api,host='127.0.0.1',port=port,workers=1,access_log=False))
    if manager_pid:
        def watch_manager():
            failures=0
            while not server.should_exit:
                time.sleep(5)
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8793/api/app/ping',timeout=2) as response:pid=json.load(response)['pid']
                    if pid!=manager_pid:server.should_exit=True;return
                    failures=0
                except (OSError,ValueError,KeyError):
                    failures+=1
                    if failures>=2:server.should_exit=True;return
        threading.Thread(target=watch_manager,name='voice-manager-lifetime',daemon=True).start()
    server.run()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['serve']);parser.add_argument('--manager-pid',type=int)
    parser.add_argument('--port',type=int,default=9880);parser.add_argument('--voice',choices=['chii','dafeiyu'])
    args=parser.parse_args()
    serve(args.manager_pid,args.port,args.voice)
