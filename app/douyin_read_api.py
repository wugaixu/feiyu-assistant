"""Ordinary read-only request compatibility using reviewed upstream signer."""
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode


def detail(context,video_id):
    return request(context,'/aweme/v1/web/aweme/detail/',{'aweme_id':video_id})


def request(context,path,extra):
    if path not in ('/aweme/v1/web/aweme/detail/','/aweme/v1/web/comment/list/'):
        raise ValueError('Read endpoint not allowed')
    pages=[p for p in context.pages if p.url.startswith('https://www.douyin.com/') and not p.is_closed()]
    if not pages: raise ValueError('Existing Douyin session unavailable')
    agent=pages[0].evaluate('navigator.userAgent')
    params={'device_platform':'webapp','aid':'6383','channel':'channel_pc_web',
            'update_version_code':'170400','pc_client_type':'1','version_code':'290100','version_name':'29.1.0',
            'cookie_enabled':'true','browser_language':'zh-CN','browser_platform':'Win32',
            'browser_name':'Chrome','browser_online':'true','engine_name':'Blink','os_name':'Windows',
            'os_version':'10','platform':'PC'}
    params.update(extra)
    # Domain-specific session value stays in memory and is never logged or saved.
    tokens=[c['value'] for c in context.cookies('https://www.douyin.com/') if c['name']=='msToken']
    if tokens: params['msToken']=tokens[0]
    query=urlencode(params)
    node=shutil.which('node')
    if not node: raise ValueError('Existing Node runtime unavailable')
    script=Path(__file__).resolve().parents[1]/'vendor/douyin-cli/sign.cjs'
    proc=subprocess.run([node,str(script)],input=json.dumps({'query':query,'userAgent':agent}),
                        capture_output=True,text=True,encoding='utf-8',timeout=3,check=True)
    signature=json.loads(proc.stdout)['signature']
    return context.request.get('https://www.douyin.com'+path+'?'+query+'&'+urlencode({'a_bogus':signature}),
                               headers={'User-Agent':agent,'Referer':'https://www.douyin.com/'},timeout=8000)
