"""Bounded local tools. HTTP executes in the existing owner-controlled session."""
import json
import re
import time
import uuid
from pathlib import Path

ROOT = Path(r'__RUNTIME_ROOT__')
ENDPOINT = 'https://www.douyin.com/aweme/v1/web/general/search/single/'


def schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS douyin_tool_operations(
      id TEXT PRIMARY KEY, action TEXT NOT NULL, args TEXT NOT NULL,
      status TEXT NOT NULL, created REAL NOT NULL, result TEXT);
    CREATE TABLE IF NOT EXISTS douyin_video_cache(
      id TEXT PRIMARY KEY, updated REAL NOT NULL, data TEXT NOT NULL);
    ''')
    db.commit()


def submit_search(db, keyword, limit=5):
    from app.video_rounds import config
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('limit must be 1..10')
    cfg = config()
    if not isinstance(keyword, str) or keyword not in cfg['keywords'] + cfg['tags']:
        raise ValueError('Search keyword must be explicitly owner-configured')
    schema(db)
    with db:
        # Serialize submissions across CLI/MCP clients and reserve before network IO.
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM douyin_tool_operations WHERE status IN ('pending','attempted')").fetchone():
            raise ValueError('A tool operation is already pending; inspect its result first')
        now = time.time()
        if db.execute("SELECT 1 FROM douyin_tool_operations WHERE action='search' AND created>?", (now-15,)).fetchone():
            raise ValueError('Search rate limited; no request sent')
        if db.execute("SELECT COUNT(*) FROM douyin_tool_operations WHERE action='search' AND created>?", (now-86400,)).fetchone()[0] >= 30:
            raise ValueError('Rolling daily search limit reached')
        key = uuid.uuid4().hex
        db.execute('INSERT INTO douyin_tool_operations VALUES(?,?,?,?,?,NULL)',
                   (key, 'search', json.dumps({'keyword': keyword, 'limit': limit}, ensure_ascii=False), 'pending', now))
    return {'operation_id': key, 'status': 'pending', 'automatic_retry': False}


def submit_transcript(db, video_id, action='transcript', retry_failed=False):
    from app.douyin_content import transcript_path
    transcript_path(video_id)
    schema(db)
    if not db.execute('SELECT 1 FROM douyin_video_cache WHERE id=?',(video_id,)).fetchone():
        raise ValueError('Search and cache this work first')
    with db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute("SELECT id,status FROM douyin_tool_operations WHERE action=? AND args=? ORDER BY created DESC LIMIT 1",(action,json.dumps({'video_id':video_id}))).fetchone()
        if type(retry_failed)is not bool: raise ValueError('Invalid retry flag')
        if old and not retry_failed: return {'operation_id':old[0],'automatic_retry':False,'reused':True}
        if old and retry_failed:
            if old[1] not in ('unavailable','failed'): raise ValueError('Only a known failed read can be retried explicitly')
            if db.execute('SELECT COUNT(*) FROM douyin_tool_operations WHERE action=? AND args=?',(action,json.dumps({'video_id':video_id}))).fetchone()[0]>=2: raise ValueError('Explicit read retry limit reached')
        if db.execute("SELECT 1 FROM douyin_tool_operations WHERE status IN ('pending','attempted')").fetchone():
            raise ValueError('Another tool operation is pending')
        key=uuid.uuid4().hex
        db.execute('INSERT INTO douyin_tool_operations VALUES(?,?,?,?,?,NULL)',(key,action,json.dumps({'video_id':video_id}),'pending',time.time()))
    return {'operation_id':key,'status':'pending','automatic_retry':False}


def submit_recommend(db,task_id,limit=5):
    from app.video_rounds import require_task,config,current_slot,TZ,manual_test,schema as round_schema
    from datetime import datetime
    round_schema(db);require_task(db,task_id)
    cfg=config()
    if cfg['video_source']!='recommendation' or not cfg['enabled'] or (current_slot(datetime.now(TZ),cfg['windows']) is None and not manual_test(db,task_id)):raise ValueError('Recommendation mode must be enabled within an authorized time window or explicit bounded manual test')
    if type(limit)is not int or not 1<=limit<=5:raise ValueError('limit must be 1..5')
    schema(db)
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM douyin_tool_operations WHERE status IN ('pending','attempted')").fetchone():raise ValueError('Another read operation is pending')
        if db.execute("SELECT 1 FROM douyin_tool_operations WHERE action='recommend' AND created>?",(time.time()-60,)).fetchone():raise ValueError('Recommendation read interval is 60 seconds')
        key=uuid.uuid4().hex
        db.execute('INSERT INTO douyin_tool_operations VALUES(?,?,?,?,?,NULL)',(key,'recommend',json.dumps({'limit':limit}),'pending',time.time()))
    return {'operation_id':key,'automatic_retry':False}


def result(db, key):
    if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{32}', key):
        raise ValueError('Invalid operation ID')
    schema(db)
    row = db.execute('SELECT status,result FROM douyin_tool_operations WHERE id=?', (key,)).fetchone()
    if not row:
        from app.video_rounds import schema as video_schema
        video_schema(db)
        row=db.execute('SELECT status,result_json FROM video_browser_commands WHERE id=?',(key,)).fetchone()
        if not row: raise ValueError('Unknown operation')
    return {'operation_id': key, 'status': row[0], 'result': json.loads(row[1]) if row[1] else None}


def normalize_items(payload, limit):
    """Only actual work objects; never mistake a comment/user ID for a work ID."""
    out = []
    seen = set()
    def walk(value):
        if len(out) >= limit:
            return
        if isinstance(value, dict):
            video_id = value.get('aweme_id')
            if isinstance(video_id, str) and re.fullmatch(r'\d{15,25}', video_id) and isinstance(value.get('desc'), str) and isinstance(value.get('video'), dict):
                if video_id not in seen:
                    seen.add(video_id)
                    author = value.get('author') or {}
                    stats = value.get('statistics') or {}
                    out.append({'video_id': video_id, 'url': 'https://www.douyin.com/video/'+video_id,
                                'description': value['desc'][:600], 'author': str(author.get('nickname', ''))[:80],
                                'duration_ms': value['video'].get('duration'),
                                'likes': stats.get('digg_count'), 'comments': stats.get('comment_count'),
                                'evidence': 'metadata_only', 'transcript_available': False})
                return
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(payload)
    return out


def verification_present(value):
    if isinstance(value, dict):
        return 'verify_check' in value or any(verification_present(v) for v in value.values())
    if isinstance(value, list):
        return any(verification_present(v) for v in value)
    return value == 'verify_check'


def search_http(context, keyword, limit, db=None):
    # The search route/parameters were reviewed in LIghtJUNction/douyin src/crawler.rs.
    # No custom signature generator or authentication-challenge workaround is used.
    params = {'device_platform': 'webapp', 'aid': '6383', 'channel': 'channel_pc_web',
              'search_channel': 'aweme_general', 'keyword': keyword, 'search_source': 'tab_search',
              'query_correct_type': '1', 'is_filter_search': '0', 'offset': '0',
              'count': str(limit), 'need_filter_settings': '0', 'list_type': 'multi'}
    response = None
    try:
        response = context.request.get(ENDPOINT, params=params, timeout=8000,
                                      headers={'Referer': 'https://www.douyin.com/', 'Accept': 'application/json'})
        if response.status != 200:
            return {'status': 'unavailable', 'reason': 'HTTP '+str(response.status), 'automatic_retry': False}
        body = response.body()
        if not body or len(body) > 8_000_000:
            return {'status': 'unavailable', 'reason': 'Empty or oversized API response', 'automatic_retry': False}
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            return {'status': 'unavailable', 'reason': 'API returned non-JSON content', 'automatic_retry': False}
        if not isinstance(payload, dict):
            return {'status': 'unavailable', 'reason': 'Unexpected API response shape', 'automatic_retry': False}
        if verification_present(payload):
            return {'status': 'needs_verification', 'reason': 'Platform requested verification', 'automatic_retry': False}
        if str(payload.get('status_code')) != '0':
            return {'status': 'unavailable', 'reason': 'Nonzero or missing business status', 'automatic_retry': False}
        items = normalize_items(payload, limit)
        if db is not None:
            from app.douyin_content import remember
            remember(db,payload)
        return {'status': 'done', 'transport': 'session_http', 'items': items,
                'count': len(items), 'evidence_limit': 'Metadata only; not watched or transcribed', 'automatic_retry': False}
    except Exception:
        # Never persist request URLs, cookies, response bodies, or auth details.
        return {'status': 'unavailable', 'reason': 'Session HTTP request failed or timed out', 'automatic_retry': False}
    finally:
        if response is not None:
            response.dispose()


def tick(db, context):
    schema(db)
    row = db.execute("SELECT id,args,action FROM douyin_tool_operations WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
    if not row:
        return
    with db:
        if not db.execute("UPDATE douyin_tool_operations SET status='attempted' WHERE id=? AND status='pending'", (row[0],)).rowcount:
            return
    try:
        value=execute_read(db,context,row)
    except Exception as exc:
        value={'status':'failed','reason':'Read operation failed ('+type(exc).__name__+')','automatic_retry':False}
    with db:
        for item in value.get('items', []) if row[2] in ('search','recommend') else []:
            db.execute('INSERT OR REPLACE INTO douyin_video_cache VALUES(?,?,?)',
                       (item['video_id'], time.time(), json.dumps(item, ensure_ascii=False)))
        db.execute('UPDATE douyin_tool_operations SET status=?,result=? WHERE id=?',
                   (value['status'], json.dumps(value, ensure_ascii=False), row[0]))


def execute_read(db,context,row):
    if (ROOT / 'video-auth-required.json').exists():
        return {'status':'needs_verification','reason':'Existing authentication guard is active','automatic_retry':False}
    else:
        args = json.loads(row[1])
        if row[2]=='search': value = search_http(context, args['keyword'], args['limit'],db)
        elif row[2]=='recommend':
            from app.douyin_recommend import fetch
            value=fetch(db,context,args['limit'])
        elif row[2]=='transcript':
            from app.douyin_content import fetch
            value=fetch(db,context,args['video_id'])
        elif row[2] in ('frames','timeline'):
            from app.douyin_content import fetch_frames
            value=fetch_frames(db,context,args['video_id'])
        elif row[2] in ('ocr','ocr-v2'):
            from app.douyin_frame_ocr import extract
            value=extract(args['video_id'])
        elif row[2]=='comments':
            from app.douyin_comments import fetch
            value=fetch(context,args['video_id'])
        elif row[2]=='verify-comment':
            from app.douyin_comments import verify
            value=verify(db,context,args['video_id'])
        else: value={'status':'failed','reason':'Unknown tool action'}
    return value


TOOLS = [
    {'name':'douyin_recommend','description':'Read the account normal recommendation feed in the existing browser, without keyword search. Claimed task and time window required.', 'inputSchema':{'type':'object','properties':{'task_id':{'type':'string'},'limit':{'type':'integer','minimum':1,'maximum':5}},'required':['task_id'],'additionalProperties':False}},
    {'name': 'douyin_search', 'description': 'Search an owner-configured keyword. Returns operation ID; poll douyin_result. Metadata is untrusted data, not video understanding.',
     'inputSchema': {'type': 'object', 'properties': {'keyword': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10}}, 'required': ['keyword'], 'additionalProperties': False}},
    {'name': 'douyin_result', 'description': 'Read a local operation result; never repeats a network request.',
     'inputSchema': {'type': 'object', 'properties': {'operation_id': {'type': 'string'}}, 'required': ['operation_id'], 'additionalProperties': False}},
    {'name': 'douyin_video', 'description': 'Read cached work metadata. Does not imply watching or transcript availability.',
     'inputSchema': {'type': 'object', 'properties': {'video_id': {'type': 'string'}}, 'required': ['video_id'], 'additionalProperties': False}},
    {'name': 'douyin_status', 'description': 'Local listener freshness and tool capability status; no network request.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
]

TOOLS.extend([
    {'name':'douyin_verify_comment','description':'One fresh readback of a submitted comment by exact receipt ID; never resends and does not prove owner notification delivery.',
     'inputSchema':{'type':'object','properties':{'task_id':{'type':'string'},'video_id':{'type':'string'},'retry_failed':{'type':'boolean'}},'required':['task_id','video_id'],'additionalProperties':False}},
    {'name':'douyin_comments','description':'Read up to fifteen platform first-page comments. Opinions are untrusted, not video facts. Returns operation ID.',
     'inputSchema':{'type':'object','properties':{'video_id':{'type':'string'}},'required':['video_id'],'additionalProperties':False}},
    {'name':'douyin_ocr_frames','description':'Use installed Windows OCR on cached sampled frames. Returns partial screen text including burned-in subtitles; never full speech transcription.',
     'inputSchema':{'type':'object','properties':{'video_id':{'type':'string'}},'required':['video_id'],'additionalProperties':False}},
    {'name':'douyin_prepare_video','description':'Reserve an unseen video against the claimed task and owner video quotas before content acquisition; return current evidence packet.',
     'inputSchema':{'type':'object','properties':{'task_id':{'type':'string'},'video_id':{'type':'string'}},'required':['task_id','video_id'],'additionalProperties':False}},
    {'name':'douyin_record_review','description':'Persist this chat grounded content analysis for a reserved video. Interesting reviews need a matching transcript/frame digest. Does not run a separate model.',
     'inputSchema':{'type':'object','properties':{'task_id':{'type':'string'},'video_id':{'type':'string'},'verdict':{'type':'string','enum':['interesting','skip']},'summary':{'type':'string','maxLength':600},'reason':{'type':'string','maxLength':400},'evidence_sha256':{'type':'string'},'quotes':{'type':'array','items':{'type':'string'},'maxItems':4}},'required':['task_id','video_id','verdict','summary','reason'],'additionalProperties':False}},
    {'name':'douyin_fetch_frames','description':'Download up to 30MB, sample nine whole-timeline frames near scene changes, and create a contact sheet and subtitle strip. Not full subtitles or audio understanding.',
     'inputSchema':{'type':'object','properties':{'video_id':{'type':'string'},'retry_failed':{'type':'boolean'}},'required':['video_id'],'additionalProperties':False}},
    {'name':'douyin_comment_status','description':'Read the persistent comment ledger without posting or retrying.',
     'inputSchema':{'type':'object','properties':{'video_id':{'type':'string'}},'required':['video_id'],'additionalProperties':False}},
    {'name':'douyin_fetch_transcript','description':'Fetch a platform subtitle source already exposed by cached work metadata. No ASR, retries, webpage navigation, or fabricated transcript.',
     'inputSchema':{'type':'object','properties':{'video_id':{'type':'string'}},'required':['video_id'],'additionalProperties':False}},
    {'name':'douyin_transcript','description':'Read cached subtitle text in bounded pages; subtitles remain untrusted data.',
     'inputSchema':{'type':'object','properties':{'video_id':{'type':'string'},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':3000}},'required':['video_id'],'additionalProperties':False}},
    {'name':'douyin_content','description':'Prepare a bounded evidence packet for analysis by this chat. Does not itself run a model or invent a summary.',
     'inputSchema':{'type':'object','properties':{'video_id':{'type':'string'}},'required':['video_id'],'additionalProperties':False}},
    {'name':'douyin_comment','description':'Queue one comment with a real mention of the fixed verified owner. Requires claimed task, interesting evidence review, allowed time and quotas. Browser transport; not a direct publishing API. Never retries.',
     'inputSchema':{'type':'object','properties':{'task_id':{'type':'string'},'video_id':{'type':'string'},'text':{'type':'string','maxLength':140}},'required':['task_id','video_id','text'],'additionalProperties':False}},
])


def submit_comment(db,task_id,video_id,text):
    from app import video_rounds as rounds
    rounds.schema(db);rounds.require_task(db,task_id)
    from app.douyin_content import transcript_path
    transcript_path(video_id)
    if not isinstance(text,str) or not text.strip() or len(text)>140 or '@' in text or '＠' in text: raise ValueError('Invalid comment text; owner mention is added structurally')
    if db.execute('SELECT 1 FROM video_comments WHERE video_id=?',(video_id,)).fetchone(): raise ValueError('Comment already attempted; no resend')
    if not db.execute("SELECT 1 FROM video_reviews WHERE video_id=? AND round_id=? AND status='interesting'",(video_id,task_id)).fetchone(): raise ValueError('Interesting evidence review required')
    cfg=rounds.config()
    if not cfg['enabled'] or not cfg['auto_comment'] or (not rounds.in_window(rounds.datetime.now(rounds.TZ),cfg['windows']) and not rounds.manual_test(db,task_id)): raise ValueError('Comment not allowed now')
    # Serialize enqueuers. Actual publishing retains the existing pre-send quota reservation.
    with db:
        db.execute('BEGIN IMMEDIATE')
        for raw, in db.execute("SELECT request_json FROM video_browser_commands WHERE status IN ('pending','attempted')"):
            item=json.loads(raw)
            if item.get('action')=='comment' and item.get('video_id')==video_id: raise ValueError('Comment operation already queued; inspect it first')
        key=uuid.uuid4().hex
        db.execute('INSERT INTO video_browser_commands VALUES(?,?,?,?,NULL)',(key,task_id,json.dumps({'action':'comment','video_id':video_id,'text':text},ensure_ascii=False),'pending'))
    return {'operation_id':key,'status':'pending','transport':'verified_browser_comment','automatic_retry':False,'owner_notification_confirmed':False}


next(t for t in TOOLS if t['name']=='douyin_record_review')['inputSchema']['properties'].update({'category':{'type':'string','enum':['entertainment','comedy','other']},'category_reason':{'type':'string','maxLength':400}})


def call(db, name, args):
    if name == 'douyin_recommend':return submit_recommend(db,**args)
    schema(db)
    spec = next((t for t in TOOLS if t['name'] == name), None)
    if not spec or not isinstance(args, dict) or set(args)-set(spec['inputSchema']['properties']) or set(spec['inputSchema'].get('required', []))-set(args):
        raise ValueError('Unknown tool or invalid arguments')
    if name == 'douyin_search':
        return submit_search(db, **args)
    if name == 'douyin_prepare_video':
        from app import video_rounds as rounds
        from app.douyin_content import packet
        rounds.schema(db);rounds.require_task(db,args['task_id'])
        if not db.execute('SELECT 1 FROM douyin_video_cache WHERE id=?',(args['video_id'],)).fetchone(): raise ValueError('Search and cache the video first')
        rounds.reserve_video(db,args['task_id'],args['video_id'],rounds.config())
        return packet(db,args['video_id'])
    if name == 'douyin_record_review': return record_review(db,**args)
    if name == 'douyin_fetch_transcript': return submit_transcript(db,**args)
    if name == 'douyin_fetch_frames': return submit_transcript(db,action='timeline',**args)
    if name == 'douyin_comments': return submit_transcript(db,action='comments',**args)
    if name == 'douyin_verify_comment':
        from app import video_rounds as rounds
        rounds.schema(db);rounds.require_task(db,args['task_id'])
        if not db.execute('SELECT 1 FROM video_comments WHERE video_id=? AND round_id=?',(args['video_id'],args['task_id'])).fetchone(): raise ValueError('This task has no submitted comment')
        return submit_transcript(db,args['video_id'],action='verify-comment',retry_failed=args.get('retry_failed',False))
    if name == 'douyin_ocr_frames': return submit_transcript(db,action='ocr-v2',**args)
    if name == 'douyin_comment_status':
        from app.video_rounds import schema as video_schema
        from app.douyin_content import transcript_path
        transcript_path(args['video_id']);video_schema(db)
        row=db.execute('SELECT status,detail FROM video_comments WHERE video_id=?',(args['video_id'],)).fetchone()
        return {'status':row[0],'detail':json.loads(row[1]) if row[1] else None} if row else {'status':'not_attempted'}
    if name == 'douyin_transcript':
        from app.douyin_content import read
        return read(**args)
    if name == 'douyin_content':
        from app.douyin_content import packet
        return packet(db,**args)
    if name == 'douyin_comment': return submit_comment(db,**args)
    if name == 'douyin_result':
        return result(db, args['operation_id'])
    if name == 'douyin_video':
        video_id = args['video_id']
        if not isinstance(video_id, str) or not re.fullmatch(r'\d{15,25}', video_id):
            raise ValueError('Invalid work ID')
        row = db.execute('SELECT updated,data FROM douyin_video_cache WHERE id=?', (video_id,)).fetchone()
        return {'status': 'cached', 'updated': row[0], 'data': json.loads(row[1])} if row else {'status': 'not_cached'}
    path = ROOT / 'live-service-status.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return {'listener_state': state.get('state', 'unknown'), 'age_seconds': round(time.time()-state.get('updated_at', 0), 1),
            'tools': [t['name'] for t in TOOLS], 'transport': 'existing_session_http',
            'search_browser_navigation': False, 'comment_transport': 'verified_browser_comment',
            'direct_api_posting_supported': False,'transcript_transport':'platform_caption_if_exposed',
            'analysis_provider':'current_chat','automatic_retry': False}


def record_review(db,task_id,video_id,verdict,summary,reason,evidence_sha256=None,quotes=None,category=None,category_reason=None):
    from app import video_rounds as rounds
    from app.douyin_content import transcript_path
    rounds.schema(db);rounds.require_task(db,task_id)
    if verdict not in ('interesting','skip') or not isinstance(summary,str) or len(summary)>600 or not isinstance(reason,str) or not reason.strip() or len(reason)>400:
        raise ValueError('Invalid structured review')
    if quotes is None: quotes=[]
    if not isinstance(quotes,list) or len(quotes)>4 or any(not isinstance(q,str) or not q.strip() or len(q)>200 for q in quotes): raise ValueError('Invalid evidence quotes')
    path=transcript_path(video_id)
    base=path.parent
    frames=base/'frames-v2.json' if (base/'frames-v2.json').exists() else base/'frames.json'
    evidence='metadata_only'
    if not path.exists(): path=base/'frame-ocr-v2.json' if (base/'frame-ocr-v2.json').exists() else base/'frame-ocr.json'
    if path.exists():
        record=json.loads(path.read_text(encoding='utf-8'))
        if evidence_sha256==record['sha256']:
            if not quotes or any(q not in record['text'] for q in quotes): raise ValueError('Transcript review requires exact supporting quotations')
            evidence=record['source']
    if evidence=='metadata_only' and frames.exists():
        record=json.loads(frames.read_text(encoding='utf-8'))
        if evidence_sha256==record['sha256'] and record.get('frames') and all(Path(p).is_file() for p in record['frames']): evidence='sampled_frames_no_audio'
    if verdict=='interesting' and evidence=='metadata_only': raise ValueError('Metadata alone cannot authorize a substantive comment')
    legacy={'platform_caption':'subtitles','sampled_frame_ocr':'video_frames','sampled_frames_no_audio':'video_frames','metadata_only':'visible_text'}
    return rounds.review(db,task_id,{'video_id':video_id,'verdict':verdict,'reason':reason,'summary':summary,
        'evidence':legacy[evidence],'evidence_source':evidence,'evidence_sha256':evidence_sha256,'quotes':quotes,'analysis_provider':'current_chat','category':category,'category_reason':category_reason})
