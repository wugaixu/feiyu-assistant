"""Browser-independent media and outbound workers for the SDK inbox daemon."""
import json,time,uuid,urllib.request,urllib.error
from pathlib import Path
from types import SimpleNamespace
from app.live_service import ROOT,LiveStore,dm_policy,retry_decision,DEFAULT_MIN_SEND_GAP_SECONDS

def enabled():
    try:return json.loads((ROOT/'inbound-policy.json').read_text(encoding='utf-8')).get('standalone') is True
    except (OSError,ValueError):return False

class LocalReadContext:
    """Ordinary HTTP using the Cookie already imported into DouK; no browser."""
    def __init__(self):
        config=json.loads((ROOT/'tools/douk-5.8/Volume/settings.json').read_text(encoding='utf-8-sig'))
        self.values=config.get('cookie') or {}
        if not isinstance(self.values,dict) or not self.values:raise ValueError('Local Cookie unavailable')
        version=(config.get('browser_info') or {}).get('browser_version') or '130.0.0.0'
        self.agent=f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36'
        self.pages=[SimpleNamespace(url='https://www.douyin.com/',is_closed=lambda:False,evaluate=lambda _:self.agent)]
        self.request=self
    def cookies(self,url):return [{'name':str(k),'value':str(v)} for k,v in self.values.items()]
    def get(self,url,headers,timeout):
        from urllib.parse import urlsplit
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc!='www.douyin.com' or parsed.path not in ('/aweme/v1/web/comment/list/','/aweme/v1/web/aweme/detail/'):
            raise ValueError('Read-only endpoint required')
        cookie='; '.join(str(k)+'='+str(v) for k,v in self.values.items())
        if '\n' in cookie or '\r' in cookie:raise ValueError('Invalid Cookie')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):raise ValueError('Redirect refused')
        opener=urllib.request.build_opener(NoRedirect())
        req=urllib.request.Request(url,headers={**headers,'Cookie':cookie})
        try:
            with opener.open(req,timeout=timeout/1000) as response:
                status=response.status;body=response.read(2_000_001)
        except urllib.error.HTTPError as exc:status=exc.code;body=b''
        return SimpleNamespace(status=status,body=lambda:body,dispose=lambda:None)

def media_step(db,owner):
    from app.media_pipeline import update_shared_analysis,dispatch_standalone_shared
    update_shared_analysis(db,owner);dispatch_standalone_shared(db,owner)
    row=db.execute("SELECT id,raw_json FROM live_tasks WHERE owner=? AND status='media_pending' ORDER BY CASE WHEN json_extract(raw_json,'$.is_group')=1 THEN 1 ELSE 0 END,received LIMIT 1",(owner,)).fetchone()
    if not row:return None
    message={'kind':'sdk_message'};record={}
    try:
        message=json.loads(row[1])
        if message.get('transport')=='whale' and message.get('upload'):
            from app.whale_upload import capture as capture_upload
            record=capture_upload(row[0],message)
        elif message.get('transport')=='qq':
            from app.qq_channel import capture
            record=capture(row[0],message)
        elif message.get('transport')=='sdk':
            from app.sdk_media import capture
            record=capture(None,row[0],message)
        else:raise ValueError('Unsupported media transport')
        if record.get('video'):
            from app.douyin_comments import fetch
            try:comments=fetch(LocalReadContext(),record['video']['video_id'])
            except Exception:comments={'status':'unavailable','reason':'Local comments credentials or request unavailable','automatic_retry':False}
            path=Path(record['manifest_path']).with_name('comments.json')
            path.write_text(json.dumps(comments,ensure_ascii=False,indent=2),encoding='utf-8')
            record['comments']={**comments,'path':str(path),'untrusted_content':True,'transport':'local_http'}
        if message['kind'] in ('video_card','video'):
            from app.video_audio import start as start_audio
            record['audio_analysis']=start_audio(record)
            if record.get('media_type')=='image_gallery':
                record['shared_video_analysis']={'status':'not_applicable','reason':'Image gallery: inspect saved images directly'}
            else:
                from app.shared_video import start_frames,poll_frames
                start_frames(record)
                record['shared_video_analysis']=poll_frames(row[0]) or {'status':'unavailable','reason':'No decodable video file'}
        Path(record['manifest_path']).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception as exc:
        record={**record,'task_id':row[0],'kind':message.get('kind','sdk_message'),
            'status':'unavailable','files':record.get('files',[]),
            'failure_reason':'media_write_failed' if isinstance(exc,OSError) else 'media_processing_failed',
            'error_class':type(exc).__name__,'automatic_retry':False,
            'manifest_path':record.get('manifest_path','')}
        message['media_failure']=record['failure_reason']
    with db:
        db.execute('INSERT OR IGNORE INTO media_records(id,owner,received,kind,record_json) VALUES(?,?,?,?,?)',
            (row[0],owner,time.time(),message['kind'],json.dumps(record,ensure_ascii=False)))
        if message['kind']=='audio':
            source_label={'whale':'鲸聊音频','qq':'QQ语音'}.get(message.get('transport'),'抖音语音')
            transcript=record.get('transcript') or {}
            text=str(transcript.get('text') or '').strip()
            recognized=transcript.get('status')=='transcribed' and bool(text)
            message.update(original_kind='audio',kind='voice_transcript' if recognized else 'voice_untranscribed',
                raw_text=('['+source_label+'自动转写，可能有误]\n'+text) if recognized else '['+source_label+'] 已收到语音，但未取得可用转写；请读取附件状态并如实反馈。')
            db.execute("UPDATE live_tasks SET raw_json=?,status='resource_saved' WHERE id=? AND status='media_pending'",(json.dumps(message,ensure_ascii=False),row[0]))
        else:
            db.execute("UPDATE live_tasks SET raw_json=?,status='resource_saved',result=? WHERE id=? AND status='media_pending'",(json.dumps(message,ensure_ascii=False),json.dumps({'media_status':record['status'],'manifest_path':record.get('manifest_path',''),'reason':record.get('failure_reason')}),row[0]))
    return {'task_id':row[0],'status':record['status']}

def media_job(owner):
    store=LiveStore(ROOT/'assistant.sqlite3')
    try:
        # Receiving media has priority over on-demand public browser activity.
        media=media_step(store.db,owner)
        if media:return media
        from app.shared_comments import step
        try:comment=step(store.db)
        except Exception as exc:comment={'status':'comment_step_failed','error_class':type(exc).__name__,'automatic_retry':False}
        return {'shared_comment':comment} if comment else None
    finally:store.db.close()

def outbound_step(db,owner):
    from app import sdk_sender
    from app import reply_batch
    reply_batch.schema(db)
    from app.diary_proactive import expire_pending
    expire_pending(db)
    from app import channels
    channels.schema(db)
    policy=dm_policy()
    if not policy.get('enabled',True):return {'state':'disabled'}
    db.execute('CREATE TABLE IF NOT EXISTS outbound_retries(outbox_id TEXT PRIMARY KEY,attempts INTEGER NOT NULL,not_before REAL NOT NULL,last_status TEXT)');db.commit()
    db.execute('''CREATE TABLE IF NOT EXISTS outbound_attempts(
        id TEXT PRIMARY KEY,outbox_id TEXT NOT NULL,started REAL NOT NULL,finished REAL,
        characters INTEGER NOT NULL,status TEXT NOT NULL,detail TEXT)''');db.commit()
    from app.outbox import recover_interrupted
    recover_interrupted(db)
    stamp=ROOT/'last-send-at.json'
    with db:
        if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='autonomy_jobs'").fetchone():
            from app.autonomy import expire_outbound
            expire_outbound(db)
        last=db.execute('SELECT MAX(COALESCE(finished,started)) FROM outbound_attempts').fetchone()[0] or 0
        if time.time()-last<policy.get('min_send_gap_seconds',DEFAULT_MIN_SEND_GAP_SECONDS):return {'state':'cooldown'}
        from app import whale_channel
        row=None;blocked='idle';health_checked=False;health=None
        candidates=db.execute("SELECT o.id,o.kind,o.text,o.path,o.sha256,o.work_json,o.task_id FROM reply_outbox o LEFT JOIN outbound_retries r ON r.outbox_id=o.id WHERE o.owner=? AND o.status='pending' AND COALESCE(r.not_before,0)<=? ORDER BY o.rowid",(owner,time.time())).fetchall()
        for candidate in candidates:
            if not reply_batch.allowed(db,candidate[0]):continue
            route=channels.address(db,candidate[6])
            if route.get('group_id'):
                from app import group_chats
                raw=json.loads(db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(candidate[6],)).fetchone()[0])
                if not group_chats.task_allowed(db,raw):
                    db.execute("UPDATE reply_outbox SET status='cancelled' WHERE id=? AND status='pending'",(candidate[0],));continue
            if route['channel']=='douyin':
                if not health_checked:health=sdk_sender.health();health_checked=True
                if not health or not health.get('online') or str(health.get('uid'))!='__ACCOUNT_UID__':blocked='sdk_offline';continue
                if route.get('group_id') and health.get('capabilities',{}).get('group_chat')!=1:blocked='sdk_group_unsupported';continue
            elif route['channel']=='qq':
                from app import qq_channel
                if not qq_channel.permitted(route['device']):blocked='qq_disabled';continue
                qq_health=qq_channel.health()
                if not qq_health.get('online') or not qq_health.get('bound'):blocked='qq_offline';continue
            elif not whale_channel.settings().get('enabled') or route['device'] not in whale_channel.settings().get('devices',[]):blocked='whale_disabled';continue
            row=candidate;break
        if not row:return {'state':blocked}
        kind=row[1] or 'text'
        changed=db.execute("UPDATE reply_outbox SET status='attempted' WHERE id=? AND status='pending'",(row[0],)).rowcount
        if not changed:return {'state':'reserved_elsewhere'}
        attempt_id=uuid.uuid4().hex
        db.execute('INSERT INTO outbound_attempts VALUES(?,?,?,NULL,?,?,NULL)',(attempt_id,row[0],time.time(),0 if kind!='text' else len(row[2] or ''),'attempted'))
    try:
        if route.get('group_id'):
            if kind=='image':
                from app.image_sender import validate_image,stage_for_sdk
                path,digest=validate_image(row[3])
                if digest!=row[4]:raise ValueError('Queued image changed')
                path=stage_for_sdk(path,digest)
                with db:db.execute("UPDATE reply_outbox SET path=? WHERE id=? AND status='attempted'",(str(path),row[0]))
            outcome=sdk_sender.post('/group-send',{'outbox_id':row[0]})
        elif route['channel']=='qq':
            from app.qq_channel import deliver
            outcome=deliver(route['device'],kind,row[2],row[3],row[4],row[5])
        elif route['channel']=='whale':
            outcome=whale_channel.deliver(db,route['device'],row[0],kind,row[2],row[3],row[4],row[5])
        elif kind=='image':
            from app.image_sender import validate_image,stage_for_sdk
            path,digest=validate_image(row[3])
            if digest!=row[4]:raise ValueError('Queued image changed')
            path=stage_for_sdk(path,digest)
            outcome=sdk_sender.post('/send-image',{'path':str(path),'sha256':digest})
        elif kind=='share':outcome=sdk_sender.post('/send-share',{'work':json.loads(row[5])})
        elif kind=='text':outcome=sdk_sender.send(row[2])
        else:outcome={'result':'unsupported_kind','reason':'This transport does not support this attachment'}

    except Exception as exc:outcome={'result':'outcome_uncertain','error_class':type(exc).__name__}
    result=outcome.get('result','outcome_uncertain')
    prior=db.execute('SELECT attempts FROM outbound_retries WHERE outbox_id=?',(row[0],)).fetchone()
    decision={'retry':False,'attempts':0} if route.get('group_id') or route['channel']=='qq' or kind in ('image','share','audio') else retry_decision(prior[0] if prior else 0,result,policy)
    finished=time.time()
    with db:
        db.execute('UPDATE outbound_attempts SET finished=?,status=?,detail=? WHERE id=?',(finished,result,json.dumps(outcome,ensure_ascii=False),attempt_id))
        db.execute("UPDATE reply_outbox SET status=?,detail=? WHERE id=?",('pending' if decision['retry'] else result,json.dumps(outcome,ensure_ascii=False),row[0]))
        db.execute('INSERT OR REPLACE INTO outbound_retries VALUES(?,?,?,?)',(row[0],decision['attempts'],decision.get('not_before',0),result))
    try:stamp.write_text(json.dumps({'time':finished,'outbox_id':row[0]}),encoding='utf-8')
    except OSError:pass  # SQLite completion is the authoritative cooldown.
    return {'state':result,'id':row[0],'channel':route['channel'],'image':kind=='image','share':kind=='share'}
