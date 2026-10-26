"""Read-only readiness for installed AstrBot speech/music plugins.

Only loopback TCP is probed; no synthesis, music download, provider request or
plugin config changes. A listening port is not a successful voice inference.
"""
import json
import socket
import sqlite3
import threading
import time
import urllib.parse
from contextlib import closing
from app.audio_sender import ROOT

_cache = None
_stamp = 0
_lock = threading.Lock()


def voice_name():
    """Configured voice label for display only; never used for routing."""
    try:
        value = json.loads((ROOT / 'gpt-sovits.json').read_text(encoding='utf-8-sig'))
        name = value.get('voice')
        return name.strip() if isinstance(name, str) and name.strip() else ''
    except (OSError, ValueError):
        return ''


def inactivated_plugins():
    """Read AstrBot's own disabled-plugin list; the database is opened read-only."""
    database = ROOT / 'astrbot/runtime/data/data_v4.db'
    try:
        if not database.is_file():
            return set()
        with closing(sqlite3.connect('file:' + database.as_posix() + '?mode=ro', uri=True, timeout=1)) as con:
            row = con.execute("select value from preferences where scope='global' and key='inactivated_plugins'").fetchone()
        if not row:
            return set()
        value = json.loads(row[0])
        items = value.get('val') if isinstance(value, dict) else value
        return set(items) if isinstance(items, list) else set()
    except (sqlite3.Error, OSError, ValueError):
        return set()


def text_only_workflow(message):
    """Known local workflow requests without a Whale audio route stay text-only."""
    return (isinstance(message,str) and message.startswith(
        ('这是已核验主人的私聊。','公开作品简评任务。','这是主人授权的'))
        and '[鲸聊语音许可:' not in message)


def local_service(url):
    try:
        parsed=urllib.parse.urlsplit(str(url))
        if parsed.scheme not in ('http','https') or parsed.username or parsed.password:
            return 'invalid'
        if parsed.hostname not in ('127.0.0.1','localhost','::1'):
            return 'remote_configured' if parsed.hostname else 'invalid'
        with socket.create_connection((parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80)),timeout=.3):
            return 'reachable'
    except (OSError,ValueError):return 'offline'


def state(cached=True):
    global _cache,_stamp
    with _lock:
        if cached and _cache is not None and time.monotonic()-_stamp<30:
            return _cache.copy()
        data=ROOT/'astrbot/runtime/data'
        def config(name):
            try:
                value=json.loads((data/'config'/f'{name}_config.json').read_text(encoding='utf-8-sig'))
                return value if isinstance(value,dict) else {}
            except (OSError,ValueError):return {}
        gsv=config('astrbot_plugin_GPT_SoVITS')
        cover=config('astrbot_plugin_matsuko_cover')
        gsv_installed=(data/'plugins/astrbot_plugin_GPT_SoVITS/main.py').is_file()
        cover_installed=(data/'plugins/astrbot_plugin_matsuko_cover/main.py').is_file()
        disabled=inactivated_plugins()
        gsv_inactive=gsv_installed and 'data.plugins.astrbot_plugin_GPT_SoVITS.main' in disabled
        cover_inactive=cover_installed and 'data.plugins.astrbot_plugin_matsuko_cover.main' in disabled
        enabled=gsv_installed and gsv.get('enabled') is True and not gsv_inactive
        service=local_service((gsv.get('client') or {}).get('base_url','')) if enabled else 'disabled'
        reference=bool((gsv.get('default_params') or {}).get('ref_audio_path'))
        # Remote configuration is not declared offline merely because local
        # health probes intentionally avoid contacting third-party services.
        usable=enabled and service in ('reachable','remote_configured') and reference
        engines={}
        if cover_installed and not cover_inactive:
            for name in ('rvc','svc','svcvc'):
                if cover.get('enable_'+name):engines[name]=local_service(cover.get(name+'_base_url',''))
        result={'gsv_installed':gsv_installed,'gsv_enabled':enabled,'gsv_service':service,
                'gsv_available':bool(usable),'gsv_reference_configured':reference,
                'gsv_voice':voice_name(),
                'gsv_plugin_disabled':bool(gsv_inactive),'music_plugin_disabled':bool(cover_inactive),
                'music_installed':cover_installed and not cover_inactive,'cover_services':engines,
                'note':'插件安装不等于模型服务已部署；端口可连不代表合成/翻唱成功。',
                'max_audio_seconds':600,'max_audio_mb':32}
        _cache=result;_stamp=time.monotonic()
        return result.copy()


def speech_available(status):
    """True when gsv_tts can work, including a configured on-demand start."""
    return bool(status.get('gsv_available') or (
        status.get('gsv_enabled') and status.get('gsv_reference_configured')))


def instructions(status):
    if status.get('gsv_available'):
        speech='语音优先使用已安装的 GPT-SoVITS 工具 gsv_tts，按插件配置音色合成；当前没有其他语音工具，不得声称有系统女声备用。'
    elif speech_available(status):
        speech='GPT-SoVITS 已配置为按需启动：可以照常调用 gsv_tts，首次合成可能等待二十秒左右；服务待机不等于没部署，不要因此说没有语音。当前没有其他语音工具。'
    elif status.get('gsv_plugin_disabled'):
        speech='GPT-SoVITS 插件当前在 AstrBot 中被停用，不能声称使用了它的音色；需要语音时先说明插件未启用。当前没有其他语音工具，回文字即可。'
    else:
        speech='GPT-SoVITS 插件已安装但服务尚未就绪，不能声称使用了它的音色。如果用户指定 GPT-SoVITS 或特定音色，应说明模型服务尚未部署，不偷偷替换。当前没有其他语音工具。'
    music='已有音乐/翻唱插件时可按其实际工具搜索歌曲或翻唱；搜到歌曲不等于已经生成可播放音频。' if status.get('music_installed') else ''
    if status.get('music_installed') and not any(v in ('reachable','remote_configured') for v in status.get('cover_services',{}).values()):
        music+='当前本地翻唱服务未在线，不要盲目发起翻唱或声称会唱歌。'
    return speech+music+'插件返回的真实 Record 音频附件可以发送到鲸聊，最长10分钟、32MiB；没有音频不冒充发送成功。不输出内部许可或文件路径。'
