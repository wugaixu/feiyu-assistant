"""Single-attempt image outbox using the verified chat's normal file input."""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit
from pathlib import Path
from PIL import Image

ROOT=Path(r'__RUNTIME_ROOT__')
PROJECT=Path(__file__).resolve().parent.parent


def schema(db):
    from app.outbox import schema as ensure_schema
    ensure_schema(db)


def validate_image(path):
    path=Path(path).resolve(strict=True)
    from app import media_formats as formats
    from app.resource_storage import resource_dirs
    roots=resource_dirs('media',ROOT)+[ROOT/'outgoing-images',PROJECT/'账号形象']
    if not any(path.is_relative_to(r.resolve()) for r in roots):
        raise ValueError('Image must be in an approved media/output directory')
    if not path.is_file() or path.suffix.lower() not in formats.OUTBOUND_EXTENSIONS:
        raise ValueError('PNG/JPEG/GIF/WEBP file required')
    if not 1 <= path.stat().st_size <= formats.MAX_BYTES:
        raise ValueError('Image must be at most 10 MiB')
    with Image.open(path) as img:
        if img.format not in formats.OUTBOUND_IMAGE or img.width*img.height>formats.MAX_PIXELS:
            raise ValueError('Unsupported image format or dimensions')
        img.verify()
    with path.open('rb') as f:
        digest=hashlib.file_digest(f,'sha256').hexdigest()
    return path,digest


def stage_for_sdk(path,digest):
    """Keep the SDK's fixed send-image allowlist when media storage changes."""
    path=Path(path)
    if any(path.is_relative_to(r.resolve()) for r in (ROOT/'media',ROOT/'outgoing-images',PROJECT/'账号形象')):return path
    dest=ROOT/'outgoing-images'/('resource-'+digest+path.suffix.lower());dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():
        with path.open('rb') as source,dest.open('xb') as target:
            import shutil
            shutil.copyfileobj(source,target)
    with dest.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:raise ValueError('Staged image changed')
    return dest


def enqueue(db,task_id,path):
    if not re.fullmatch('[0-9a-f]{32}',task_id):
        raise ValueError('Local task UUID required')
    schema(db)
    row=db.execute("SELECT owner FROM live_tasks WHERE id=? AND status IN ('processing','done')",(task_id,)).fetchone()
    if not row:
        raise ValueError('Claim the authorized task before queuing an image')
    path,digest=validate_image(path)
    key=task_id+':image:'+digest
    with db:
        db.execute('INSERT OR IGNORE INTO reply_outbox(id,task_id,owner,text,kind,path,sha256) VALUES(?,?,?,?,?,?,?)',
                   (key,task_id,row[0],'','image',str(path),digest))
    return key


def outgoing_images(page):
    return page.locator('[data-stack-layer="chat"] .messageMessageBoxisFromMe [data-e2e="msg-item-content"] img')


def transport_confirmed(events):
    """Normal browser traffic evidence, not recipient delivery confirmation."""
    upload=any(e.get('status')==200 and e.get('path','').startswith('/upload/v1/')
               and (e.get('host') or '').endswith('.douyin.com') for e in events)
    send=any(e.get('status')==200 and e.get('host')=='imapi.douyin.com'
             and e.get('path')=='/v1/message/send' for e in events)
    return upload and send


def business_result(body):
    """Extract the JSON status embedded in the observed browser response only."""
    text=body.decode('utf-8',errors='replace')
    decoder=json.JSONDecoder()
    for match in re.finditer(r'\{"status_code"\s*:',text):
        try:
            data,_=decoder.raw_decode(text[match.start():])
            return {k:data[k] for k in ('status_code','raw_check_code','tips') if k in data}
        except (ValueError,TypeError):
            continue
    return None


def confirm_preview(page,path,digest,verify):
    modal=page.locator('.MsgInputSendFileModalbox')
    if modal.count()!=1 or modal.locator('.MsgInputSendFileModalitemBox').count()!=1:
        raise ValueError('Owned single-image preview missing or ambiguous')
    if path.name not in modal.locator('.MsgInputSendFileModalfileList').inner_text():
        raise ValueError('Preview filename does not match queued image')
    actual=modal.locator('img.MsgInputSendFileModalitemIconInner').evaluate('''async img=>{
        if(!img.src.startsWith('data:image/')) throw Error('Expected local image preview');
        const raw=atob(img.src.slice(img.src.indexOf(';base64,')+8));
        const bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));
        return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');
    }''')
    if actual!=digest:
        raise ValueError('Preview bytes differ from the authorized file')
    verify()
    button=modal.locator('.MsgInputSendFileModalbtnSure')
    if button.count()!=1 or button.inner_text().strip()!='发送':
        raise ValueError('Verified send button missing or ambiguous')
    button.click(timeout=5000)


def process_one(db,page,owner,verify):
    schema(db)
    row=db.execute("SELECT id,path,sha256,status,detail FROM reply_outbox WHERE kind='image' AND owner=? AND status IN ('pending','preview_ready') ORDER BY rowid LIMIT 1",(owner,)).fetchone()
    if not row:
        return None
    verify()
    editor=page.locator('[data-e2e="msg-input"] [contenteditable="true"]')
    if editor.count()!=1 or editor.inner_text().strip('\u200b \n'):
        return {'id':row[0],'status':'waiting_user_draft'}
    file_input=page.locator('[data-stack-layer="chat"] .MsgInputFileUploadim-saas-fileUploader input.semi-upload-hidden-input[type="file"]')
    if file_input.count()!=1:
        return {'id':row[0],'status':'upload_input_missing'}
    path,digest=validate_image(row[1])
    if digest!=row[2]:
        with db:db.execute("UPDATE reply_outbox SET status='failed',detail='Image changed after enqueue' WHERE id=?",(row[0],))
        return {'id':row[0],'status':'failed'}
    if row[3]=='pending' and page.locator('.MsgInputSendFileModalbox').count():
        return {'id':row[0],'status':'waiting_user_preview'}
    # Legacy browser sender only; never resume an unowned/missing preview.
    if row[3]=='preview_ready':
        try:before=json.loads(row[4] or '{}')['outgoing_count_before']
        except (ValueError,TypeError,KeyError):return {'id':row[0],'status':'legacy_preview_metadata_missing'}
    else:before=outgoing_images(page).count()
    with db:
        claimed=db.execute("UPDATE reply_outbox SET status='attempted',detail=? WHERE id=? AND status=?",(json.dumps({'outgoing_count_before':before}),row[0],row[3])).rowcount
    if not claimed:
        return None
    try:
        verify()
        marker=str(time.time_ns())
        outgoing_images(page).evaluate_all('(imgs,marker)=>imgs.forEach(i=>i.dataset.localAssistantBefore=marker)',marker)
        network=[]
        def response_seen(response):
            url=urlsplit(response.url)
            if any(s in url.path.lower() for s in ('upload','image','resource','send')):
                network.append({'host':url.hostname,'path':url.path,'status':response.status})
            if url.hostname=='imapi.douyin.com' and url.path=='/v1/message/send':
                try:
                    body=response.body()
                    (ROOT/'image-send-response.bin').write_bytes(body)
                    (ROOT/'image-send-response-meta.json').write_text(json.dumps({'status':response.status,'content_type':response.headers.get('content-type'),'bytes':len(body)}),encoding='utf-8')
                    result=business_result(body)
                    if result is not None:
                        network.append({'business_result':result})
                except Exception as exc:
                    network.append({'response_read_error':str(exc)})
        def request_failed(request):
            url=urlsplit(request.url)
            network.append({'host':url.hostname,'path':url.path,'failure':request.failure})
        page.on('response',response_seen)
        page.on('requestfailed',request_failed)
        if row[3]=='pending':
            # Follow the webpage's normal add-file action and its file chooser.
            with page.expect_file_chooser(timeout=5000) as chooser:
                page.locator('[data-stack-layer="chat"] .MsgInputFileUploadim-saas-fileUploader .semi-upload-add').click(timeout=5000)
            chooser.value.set_files(str(path),timeout=10000)
        deadline=time.monotonic()+60
        confirmed=False
        while time.monotonic()<deadline:
            # Douyin can retain a data URL even after uploading. Require this
            # attempt's upload + send responses as well as a new loaded bubble.
            images=outgoing_images(page)
            new_ready=images.evaluate_all(
                '(imgs,marker)=>imgs.some(i=>i.dataset.localAssistantBefore!==marker&&i.complete&&i.naturalWidth>0)',marker)
            business=next((e['business_result'] for e in network if 'business_result' in e),None)
            if business is not None and business.get('status_code')!=0:
                detail={'reason':'Nonzero platform business status; recipient delivery not confirmed','business_result':business,'automatic_retry':False}
                with db:db.execute("UPDATE reply_outbox SET status='failed',detail=? WHERE id=?",(json.dumps(detail),row[0]))
                return {'id':row[0],'status':'failed','business_result':business}
            if confirmed and new_ready and transport_confirmed(network) and business is not None and business.get('status_code')==0 and business.get('raw_check_code',0)==0:
                with db:db.execute("UPDATE reply_outbox SET status='visible_in_history',detail=? WHERE id=?",(json.dumps({'upload_and_send_http_confirmed':True,'recipient_delivery_confirmed':False}),row[0]))
                return {'id':row[0],'status':'visible_in_history'}
            if not confirmed and page.locator('.MsgInputSendFileModalbox').count():
                confirmed=True
                confirm_preview(page,path,digest,verify)
            page.wait_for_timeout(500)
        data=page.evaluate('''()=>({dialogs:[...document.querySelectorAll('[role="dialog"],.semi-modal')].map(e=>({text:e.innerText})),
            buttons:[...document.querySelectorAll('button,[role="button"]')].filter(e=>e.offsetParent!==null).map(e=>({text:e.innerText,aria:e.getAttribute('aria-label'),cls:e.className})).slice(-20),
            input:document.querySelector('[data-e2e="msg-input"]')?.outerHTML})''')
        (ROOT/'image-send-preview.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        page.screenshot(path=str(ROOT/'image-send-preview.png'))
        with db:db.execute("UPDATE reply_outbox SET status='outcome_uncertain',detail=? WHERE id=?",(json.dumps({'reason':'No new outgoing image; inspect local preview before retrying','outgoing_count_before':before}),row[0]))
        return {'id':row[0],'status':'outcome_uncertain'}
    except Exception as exc:
        with db:db.execute("UPDATE reply_outbox SET status='outcome_uncertain',detail=? WHERE id=?",(json.dumps({'reason':str(exc),'outgoing_count_before':before}),row[0]))
        return {'id':row[0],'status':'outcome_uncertain'}
    finally:
        if 'response_seen' in locals():
            page.remove_listener('response',response_seen)
            page.remove_listener('requestfailed',request_failed)
            (ROOT/'image-upload-network.json').write_text(json.dumps(network,ensure_ascii=False),encoding='utf-8')
