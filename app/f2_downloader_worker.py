"""F2 official-release detail adapter plus pinned yt-dlp file downloader.

Uses an existing session only. No QR/SMS login, browser-cookie database access,
identity minting, captcha solving, external parsing service or profile launch.
"""
import asyncio,contextlib,io,json,logging,re,sys


async def detail(request):
    logging.disable(logging.CRITICAL)
    from f2.apps.douyin import utils
    tokens={c['name']:c['value'] for c in request['cookies'] if c['domain'].lstrip('.')=='douyin.com' or c['domain'].endswith('.douyin.com')}
    if not tokens.get('msToken'):raise ValueError('Existing session msToken required')
    # The package otherwise requests a new visitor token during model import.
    # Supply the actual browser session token instead; no new identity is made.
    utils.TokenManager.gen_real_msToken=classmethod(lambda cls:tokens['msToken'])
    from f2.apps.douyin.crawler import DouyinCrawler
    from f2.apps.douyin.model import PostDetail
    import httpx
    headers={'User-Agent':request['user_agent'],'Referer':'https://www.douyin.com/',
             'Cookie':'; '.join(k+'='+v for k,v in tokens.items())}
    async with DouyinCrawler({'cookie':headers['Cookie'],'headers':headers,'max_retries':1,'timeout':6,'max_connections':1,'max_tasks':1}) as crawler:
        crawler._aclient=httpx.AsyncClient(headers=headers,timeout=6,verify=True,transport=httpx.AsyncHTTPTransport(retries=0))
        value=await crawler.fetch_post_detail(PostDetail(aweme_id=request['video_id'],msToken=tokens['msToken']))
    work=value.get('aweme_detail') if isinstance(value,dict) else None
    if not isinstance(work,dict) or str(work.get('aweme_id'))!=request['video_id']:raise ValueError('F2 did not return the requested video detail')
    return work


if __name__=='__main__':
    try:
        request=json.load(sys.stdin)
        if not re.fullmatch(r'\d{15,25}',request.get('video_id','')):raise ValueError('Invalid video ID')
        # F2 may write informational console output. Never propagate it or URLs.
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            request['work']=asyncio.run(asyncio.wait_for(detail(request),timeout=10))
        sys.stdin=io.StringIO(json.dumps(request))
        from downloader_worker import main
        main()
    except Exception as exc:
        print(json.dumps({'status':'unavailable','backend':'f2','version':'0.0.1.7','reason':'detail_or_download_failed','error_class':type(exc).__name__,'automatic_retry':False}))
