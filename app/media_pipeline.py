"""Capture media once, attach saved resource paths to the next text event."""
import json
import time
import uuid
from pathlib import Path
from app.media_capture import capture, ROOT


def update_shared_analysis(db,owner):
    from app.shared_video import start_frames,poll_frames
    rows=db.execute("SELECT id,record_json FROM media_records WHERE owner=? AND kind IN ('video','video_card')",(owner,)).fetchall()
    for task,raw in rows:
        record=json.loads(raw)
        if record.get('status')=='cleaned' or record.get('shared_video_analysis',{}).get('status')=='cleaned':continue
        changed=False
        if record.get('shared_video_analysis',{}).get('status') not in ('done','unavailable','not_applicable','failed','partial') and any(f.get('decode_verified') for f in record.get('files',[])):
            start_frames(record)
            result=poll_frames(task)
            if result is not None:record['shared_video_analysis']=result;changed=True
        if record.get('audio_analysis',{}).get('status')=='processing':
            from app.video_audio import start as start_audio
            record['audio_analysis']=start_audio(record);changed=True
        if not changed:continue
        try:Path(record['manifest_path']).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:record['manifest_write_error']='media_write_failed'
        with db:db.execute('UPDATE media_records SET record_json=? WHERE id=?',(json.dumps(record,ensure_ascii=False),task))


def dispatch_standalone_shared(db,owner):
    # A following text gets the media first. A video without accompanying text
    # can still reach the chat after a short association grace period.
    rows=db.execute("SELECT m.id,m.record_json,t.received,t.raw_json FROM media_records m JOIN live_tasks t ON t.id=m.id WHERE m.owner=? AND m.attached_to IS NULL AND m.kind IN ('video','video_card','image','audio','file') AND t.status='resource_saved' AND t.received<?",(owner,time.time()-10)).fetchall()
    for task,raw,received,message_raw in rows:
        message=json.loads(message_raw)
        if message.get('sample_only'):continue
        if time.time()-message.get('association_received_at',received)<10:continue
        # Newly supported kinds do not turn old saved images/voice into new tasks.
        if message.get('association_policy')!='media_10s_v1' and json.loads(raw).get('kind') in ('image','audio','file'):continue
        record=json.loads(raw)
        if record.get('status')!='saved':continue
        if record.get('shared_video_analysis',{}).get('status')=='processing' or record.get('audio_analysis',{}).get('status')=='processing':continue
        from app.channels import same_source
        following=db.execute("SELECT raw_json FROM live_tasks WHERE owner=? AND received>=? AND status IN ('pending','processing') AND id<>?",(owner,received,task)).fetchall()
        if any(same_source(message_raw,r[0]) for r in following):continue
        original_kind=message.get('original_kind') or message.get('kind')
        is_video=record['kind'] in ('video','video_card') if 'kind' in record else original_kind in ('video','video_card')
        transcript=record.get('transcript') or {}
        voice_text=str(transcript.get('text') or '').strip()
        voice_label='鲸聊音频' if message.get('transport')=='whale' else '抖音语音'
        standalone_text=('['+voice_label+'自动转写，可能有误]\n'+voice_text) if record.get('kind')=='audio' and transcript.get('status')=='transcribed' and voice_text else '[用户分享的媒体]'
        message.update(original_kind=original_kind,kind='shared_video' if is_video else 'shared_media',raw_text=standalone_text)
        with db:db.execute("UPDATE live_tasks SET raw_json=?,status='pending' WHERE id=? AND status='resource_saved'",(json.dumps(message,ensure_ascii=False),task))
        break


def import_requested_samples(db, owner, snapshot):
    request = ROOT / 'media-sample-request.json'
    if not request.exists():
        return
    options = json.loads(request.read_text(encoding='utf-8'))
    wanted = options.get('kinds', [])
    selected = []
    with db:
        for kind in wanted:
            message = next((m for m in snapshot.get('messages', []) if m.get('kind') == kind and m.get('direction') != 'outgoing' and (not options.get('audio_duration') or m.get('audio_duration') == options['audio_duration'])), None)
            if message:
                message=dict(message,sample_only=bool(options.get('sample_only')))
                local_id = uuid.uuid4().hex
                db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                           (local_id,owner,time.time(),json.dumps(message,ensure_ascii=False),'media_pending'))
                selected.append({'id':local_id,'kind':kind})
    request.replace(ROOT / ('media-sample-imported-' + str(time.time_ns()) + '.json'))
    (ROOT/'media-sample-import-result.json').write_text(json.dumps(selected),encoding='utf-8')


def process_one(db, page, owner):
    update_shared_analysis(db,owner)
    dispatch_standalone_shared(db,owner)
    reopen=ROOT/'image-history-check.request'
    if reopen.exists():
        reopen.replace(ROOT/'image-history-check.attempted')
        page.locator('.StackLayoutStackTitleBarbackBtn').click(timeout=5000)
        contact=page.locator('[data-e2e="conversation-item"]').filter(has_text='__OWNER_NICKNAME__')
        contact.wait_for(state='visible',timeout=5000)
        if contact.count()!=1:
            raise ValueError('Owner conversation missing or ambiguous during history check')
        contact.click(timeout=5000)
        page.wait_for_timeout(2000)
        (ROOT/'image-send-audit.request').write_text('history reentered',encoding='utf-8')
    audit=ROOT/'image-send-audit.request'
    if audit.exists():
        (ROOT/'image-chat-header.json').write_text(json.dumps(page.evaluate('()=>[...document.querySelectorAll("svg,[role=button]")].filter(e=>/back|close/i.test(e.getAttribute("class")||"")&&e.getBoundingClientRect().width>0).map(e=>e.outerHTML.slice(0,2500))')),encoding='utf-8')
        records=page.evaluate('''()=>[...document.querySelectorAll('[data-stack-layer="chat"] .messageMessageBoxisFromMe [data-e2e="msg-item-content"]')].filter(e=>e.querySelector('img')).map(e=>{
            const box=e.closest('.messageMessageBoxmessageBox'),clone=box.cloneNode(true);
            for(const img of clone.querySelectorAll('img'))img.setAttribute('src','[redacted image source]');
            return {text:box.innerText,html:clone.outerHTML.slice(0,18000),images:[...e.querySelectorAll('img')].map(i=>({source_type:i.src.split(':')[0],width:i.naturalWidth,height:i.naturalHeight})),titles:[...box.querySelectorAll('[title],[aria-label]')].map(n=>({title:n.title,aria:n.getAttribute('aria-label')}))};
        })''')
        (ROOT/'image-send-audit.json').write_text(json.dumps(records,ensure_ascii=False),encoding='utf-8')
        page.screenshot(path=str(ROOT/'image-send-audit.png'))
        audit.unlink()
    upload_probe=ROOT/'image-upload-probe.request'
    if upload_probe.exists():
        data=page.evaluate('''()=>({inputs:[...document.querySelectorAll('input[type="file"]')].map(e=>({html:e.outerHTML,parent:e.parentElement?.outerHTML.slice(0,6000)})),
            uploads:[...document.querySelectorAll('.semi-upload-add')].map(e=>e.outerHTML),
            composer:document.querySelector('[data-e2e="msg-input"]')?.parentElement?.outerHTML.slice(0,16000)})''')
        (ROOT/'image-upload-probe.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        upload_probe.unlink()
    refresh=ROOT/'media-refresh-request.json'
    if refresh.exists():
        for local_id in json.loads(refresh.read_text(encoding='utf-8')).get('ids',[]):
            row=db.execute('SELECT raw_json FROM live_tasks WHERE id=? AND owner=?',(local_id,owner)).fetchone()
            if not row:
                continue
            message=json.loads(row[0])
            message['kind']=message.get('original_kind',message['kind'])
            manifest=ROOT/'media'/local_id/'manifest.json'
            if manifest.exists():
                manifest.replace(manifest.with_name('manifest-before-refresh-'+str(time.time_ns())+'.json'))
            record=capture(page,local_id,message)
            with db:
                db.execute('UPDATE media_records SET record_json=? WHERE id=? AND owner=?',(json.dumps(record,ensure_ascii=False),local_id,owner))
                if message['kind']=='audio' and not message.get('sample_only') and (record.get('transcript') or {}).get('status')=='transcribed':
                    message['original_kind']='audio'
                    message['kind']='voice_transcript'
                    message['raw_text']='[抖音语音自动转写，可能有误]\n'+record['transcript']['text']
                    db.execute('UPDATE live_tasks SET raw_json=? WHERE id=? AND owner=?',(json.dumps(message,ensure_ascii=False),local_id,owner))
        refresh.replace(ROOT/('media-refresh-applied-'+str(time.time_ns())+'.json'))
    restore=ROOT/'restore-latest.request'
    if restore.exists():
        scroll=page.locator('[data-stack-layer="chat"] .messageMessageListlist')
        if scroll.count()==1:
            scroll.evaluate('(e)=>{e.scrollTop=0}')
            page.wait_for_timeout(1000)
        restore.unlink()
    transcribe_probe = ROOT/'voice-transcribe-probe.request'
    if transcribe_probe.exists():
        try:
            audio=page.locator('[data-stack-layer="chat"] .MessageItemAudioaudioBoxWrapper').filter(has_text='5"').first
            bubble=audio.locator('xpath=ancestor::*[@data-e2e="msg-item-content"][1]')
            audio.click(button='right',timeout=5000)
            control=page.get_by_text('转文字',exact=True)
            if control.count()!=1:
                raise ValueError('Normal transcribe menu missing or ambiguous')
            control.click(timeout=5000)
            page.wait_for_timeout(3500)
            data={'text':bubble.inner_text(),'dom':bubble.evaluate('(e)=>e.outerHTML'),'row':bubble.evaluate('(e)=>e.parentElement.parentElement.outerHTML')}
            (ROOT/'voice-transcribe-probe.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        except Exception as exc:
            (ROOT/'voice-transcribe-probe.json').write_text(json.dumps({'error':str(exc)}),encoding='utf-8')
        transcribe_probe.unlink()
    voice_probe = ROOT/'voice-menu-probe.request'
    if voice_probe.exists():
        try:
            audio = page.locator('[data-stack-layer="chat"] .MessageItemAudioaudioBoxWrapper').first
            audio.click(button='right',timeout=5000)
            page.wait_for_timeout(500)
            text=page.locator('body').inner_text(timeout=5000)
            (ROOT/'voice-menu-probe.json').write_text(json.dumps({'tail':text[-3000:],'transcribe_controls':page.get_by_text('转文字',exact=True).count()},ensure_ascii=False),encoding='utf-8')
            page.screenshot(path=str(ROOT/'voice-menu-probe.png'))
            page.keyboard.press('Escape')
        except Exception as exc:
            (ROOT/'voice-menu-probe.json').write_text(json.dumps({'error':str(exc)}),encoding='utf-8')
        voice_probe.unlink()
    retry = ROOT/'media-retry-request.json'
    if retry.exists():
        ids = json.loads(retry.read_text(encoding='utf-8')).get('ids', [])
        with db:
            for local_id in ids:
                row = db.execute('SELECT record_json FROM media_records WHERE id=? AND owner=?',(local_id,owner)).fetchone()
                if row and json.loads(row[0]).get('status') == 'unavailable':
                    manifest = ROOT/'media'/local_id/'manifest.json'
                    if manifest.exists():
                        manifest.replace(manifest.with_name('manifest-attempt-'+str(time.time_ns())+'.json'))
                    db.execute('DELETE FROM media_records WHERE id=?',(local_id,))
                    db.execute("UPDATE live_tasks SET status='media_pending' WHERE id=?",(local_id,))
        retry.replace(ROOT/('media-retry-applied-'+str(time.time_ns())+'.json'))
    probe = ROOT/'media-dom-probe.request'
    if probe.exists():
        data = page.evaluate('''() => ({media:[...document.querySelectorAll('audio,video')].map(m=>({tag:m.tagName,src:m.currentSrc||m.src,paused:m.paused,duration:m.duration,classes:m.className,parent:m.parentElement?.className,ancestors:[...(function*(n){for(let i=0;n&&i<9;i++,n=n.parentElement)yield n;})(m.parentElement)].map(n=>({cls:n.className,videos:n.querySelectorAll('video').length,text:n.innerText?.slice(-1500)}))})),
            buttons:[...document.querySelectorAll('button,[role="button"]')].filter(e=>e.offsetParent!==null).map(e=>({text:e.innerText,aria:e.getAttribute('aria-label'),cls:e.className})).slice(-40),
            dialogs:[...document.querySelectorAll('[role="dialog"]')].map(e=>({cls:e.className,text:e.innerText.slice(0,200)})),
            scrolls:[...(function*(n){for(let i=0;n&&i<12;i++,n=n.parentElement)yield n;})(document.querySelector('[data-stack-layer="chat"] [data-e2e="msg-item-content"]'))].map(n=>({cls:n.className,top:n.scrollTop,height:n.scrollHeight,client:n.clientHeight,overflow:getComputedStyle(n).overflowY,flex:getComputedStyle(n).flexDirection}))})''')
        (ROOT/'media-dom-probe.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        probe.unlink()
    rows = db.execute("SELECT id,raw_json,status FROM live_tasks WHERE owner=? AND status IN ('media_pending','pending') ORDER BY received", (owner,)).fetchall()
    row = next((r for r in rows if json.loads(r[1]).get('kind') in ('image','audio','video','video_card') and not db.execute('SELECT 1 FROM media_records WHERE id=?',(r[0],)).fetchone()), None)
    if not row:
        return None
    message = json.loads(row[1])
    if message.get('transport')=='sdk':
        from app.sdk_media import capture as sdk_capture
        record=sdk_capture(page,row[0],message)
    else:
        record = capture(page,row[0],message)
    if message['kind'] in ('video_card','video'):
        try:
            from app.shared_video import enrich,start_frames,poll_frames
            enrich(db,page,record,message)
            if record.get('media_type')=='image_gallery':
                record['shared_video_analysis']={'status':'not_applicable','reason':'Image gallery: inspect the saved images directly; frame extraction unnecessary'}
            else:
                start_frames(record)
                record['shared_video_analysis']=poll_frames(row[0]) or {'status':'unavailable','reason':'No decodable video file'}
            Path(record['manifest_path']).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as exc:
            record['shared_video_analysis']={'status':'unavailable','reason':type(exc).__name__}
    transcript = record.get('transcript') or {}
    with db:
        db.execute('INSERT OR IGNORE INTO media_records(id,owner,received,kind,record_json) VALUES(?,?,?,?,?)',
                   (row[0],owner,time.time(),message['kind'],json.dumps(record,ensure_ascii=False)))
        if message['kind'] == 'audio' and not message.get('sample_only'):
            message['original_kind'] = 'audio'
            message['kind'] = 'voice_transcript' if transcript.get('status') == 'transcribed' else 'voice_untranscribed'
            message['raw_text'] = ('[抖音语音自动转写，可能有误]\n' + transcript['text']) if transcript.get('status') == 'transcribed' else '[抖音语音] 已收到语音，但未能转写；请读取附件状态并如实反馈。'
            message['media_record_id'] = row[0]
            db.execute("UPDATE live_tasks SET raw_json=?,status='pending' WHERE id=?", (json.dumps(message,ensure_ascii=False),row[0]))
        else:
            db.execute("UPDATE live_tasks SET status='resource_saved',result=? WHERE id=? AND status IN ('pending','media_pending')",(json.dumps({'media_status':record['status'],'manifest_path':record['manifest_path']},ensure_ascii=False),row[0]))
    return {'id':row[0],'kind':record['kind'],'status':record['status'],'transcript_status':transcript.get('status')}
