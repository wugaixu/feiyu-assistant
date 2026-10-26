"""Bounded isolated yt-dlp adapter. Input secrets are stdin-only; no cookie files."""
import hashlib,json,re,sys
from pathlib import Path
from http.cookiejar import Cookie

ROOT=Path(r'__RUNTIME_ROOT__')


class Quiet:
    def debug(self,*args):pass
    def warning(self,*args):pass
    def error(self,*args):pass


def main():
    request=json.load(sys.stdin)
    video_id=request['video_id']
    if not re.fullmatch(r'\d{15,25}',video_id):raise ValueError('Invalid video identity')
    directory=Path(request['directory']).resolve()
    if not directory.is_relative_to(ROOT.resolve()):raise ValueError('Output outside runtime directory')
    directory.mkdir(parents=True,exist_ok=True)
    wheels=list((ROOT/'tools'/'packages').glob('yt_dlp-2026.10.21-*.whl'))
    if len(wheels)!=1:raise ValueError('Pinned yt-dlp wheel unavailable')
    sys.path.insert(0,str(wheels[0]))
    from yt_dlp import YoutubeDL
    from yt_dlp.version import __version__
    def progress(value):
        if value.get('downloaded_bytes',0)>64*1024*1024:raise ValueError('Download byte limit exceeded')
    opts={'quiet':True,'no_warnings':True,'logger':Quiet(),'noplaylist':True,
          'socket_timeout':6,'retries':0,'fragment_retries':0,'extractor_retries':0,
          'max_filesize':64*1024*1024,'format':'best[ext=mp4]/best',
          'outtmpl':str(directory/'download.%(ext)s'),'continuedl':False,'overwrites':False,
          'progress_hooks':[progress],'http_headers':{'User-Agent':request['user_agent'],'Referer':'https://www.douyin.com/'}}
    with YoutubeDL(opts) as ydl:
        for item in request.get('cookies',[]):
            domain=item['domain']
            if domain.lstrip('.')!='douyin.com' and not domain.endswith('.douyin.com'):continue
            expiry=item.get('expires',-1)
            ydl.cookiejar.set_cookie(Cookie(0,item['name'],item['value'],None,False,domain,True,domain.startswith('.'),item.get('path','/'),True,item.get('secure',True),int(expiry) if expiry>0 else None,expiry<=0,None,None,{},False))
        work=request.get('work')
        if work and str(work.get('aweme_id'))==video_id:
            from yt_dlp.extractor.tiktok import DouyinIE
            extractor=DouyinIE(ydl)
            info=ydl.process_ie_result(extractor._parse_aweme_video_app(work),download=True)
        elif request.get('playback_url'):
            from urllib.parse import urlsplit
            url=request['playback_url'];host=urlsplit(url).hostname or ''
            if not host.endswith(('.douyinvod.com','.douyincdn.com','.bytecdn.cn','.byteimg.com','.pstatp.com')):raise ValueError('Unknown playback host')
            info=ydl.process_ie_result({'id':video_id,'title':video_id,'formats':[{'url':url,'ext':'mp4','protocol':'https'}]},download=True)
        else:info=ydl.extract_info('https://www.douyin.com/video/'+video_id,download=True)
        path=Path(ydl.prepare_filename(info))
        if not path.is_file() or not 0<path.stat().st_size<=64*1024*1024:raise ValueError('No bounded download output')
        print(json.dumps({'status':'saved','path':str(path),'bytes':path.stat().st_size,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'source':'yt_dlp','version':__version__,
            'video_id':video_id,'duration':info.get('duration')}))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Exception messages may contain signed URLs; retain only class and category.
        text=str(exc).lower()
        reason='fresh_cookies_or_detail_required' if 'fresh cookies' in text else 'http_403' if '403' in text else 'extract_or_download_failed'
        print(json.dumps({'status':'unavailable','reason':reason,'error_class':type(exc).__name__,'automatic_retry':False}))
