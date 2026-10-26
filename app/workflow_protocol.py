"""Shared protocol version and read-only host readiness; never probes by sending."""
import json,time
from app.live_service import ROOT

VERSION=4


def astrbot_reader():
    try:
        value=json.loads((ROOT/'workflow-reader-state.json').read_text(encoding='utf-8'))
        age=time.time()-value['updated_at']
        ready=value.get('protocol_version')==VERSION and 0<=age<=30
        return {'ready':ready,'protocol_version':value.get('protocol_version'),'required_version':VERSION,
                'reason':None if ready else 'AstrBot素材插件版本过旧或心跳过期，请重载插件'}
    except (OSError,ValueError,TypeError,KeyError):
        return {'ready':False,'required_version':VERSION,'reason':'AstrBot统一素材插件尚未加载'}
