"""Owner-shared work comments: model-authored review, durable single attempt.

Publication runs only through the real-browser comment module
``app.browser_publish``; there is no HTTP write path. A hidden Edge profile
is launched on demand, the owner mention is created from the platform's own
@-suggestion response, and the receipt is read from the page's network event.
"""
import json,re,sqlite3,time
from datetime import datetime,timezone,timedelta
from app.live_service import ROOT
from app.sdk_receiver import OWNER,OWNER_UID,ACCOUNT_UID

POLICY=ROOT/'shared-comment-policy.json'
TZ=timezone(timedelta(hours=8))
OWNER_SEC_UID=OWNER.rsplit('/',1)[-1]

def policy():
    try:cfg=json.loads(POLICY.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {'enabled':False}
    if not isinstance(cfg,dict):return {'enabled':False,'_warnings':['Invalid comment policy object']}
    warnings=[]
    value=cfg.get('daily_limit',10)
    if isinstance(value,bool) or not re.fullmatch(r'\d+',str(value)):
        value=10;warnings.append('Invalid daily_limit; using default 10')
    cfg['daily_limit']=int(value)
    if warnings:cfg['_warnings']=warnings
    return cfg

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS shared_comment_outbox(
        video_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,text TEXT NOT NULL,evidence TEXT NOT NULL,
        created REAL NOT NULL,day TEXT NOT NULL,status TEXT NOT NULL,detail TEXT)''')
    db.commit()


def manual_attempt_count(db,day):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_comment_attempts'").fetchone():return 0
    return db.execute('SELECT count(*) FROM manual_comment_attempts WHERE day=?',(day,)).fetchone()[0]

def eligible(message,record,cfg):
    return (cfg.get('enabled') is True and message.get('transport')=='sdk'
        and not message.get('sample_only') and record.get('source')=='sdk'
        and record.get('kind')=='video_card'
        and (record.get('video') or {}).get('source')=='sdk_shared_work'
        and float(record.get('received_at') or 0)>=float(cfg.get('enabled_since',float('inf')))
        and float(message.get('association_received_at') or 0)>=float(cfg.get('enabled_since',float('inf'))))

def enqueue(db,task_id,video_id,review,*,after_reply=False):
    schema(db);cfg=policy()
    if not re.fullmatch(r'\d{15,25}',video_id):raise ValueError('Invalid work ID')
    text=review.get('text');evidence=review.get('evidence')
    if not isinstance(text,str) or not 1<=len(text.strip())<=100 or any(c in text for c in ('@','＠','\n','\r')):
        raise ValueError('Comment requires 1..100 characters; mention is added structurally')
    if not isinstance(evidence,str) or not 5<=len(evidence.strip())<=1000:raise ValueError('Actual visual review evidence required')
    with db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT owner,raw_json,status FROM live_tasks WHERE id=?",(task_id,)).fetchone()
        task=row[:2] if row and row[2]=='processing' else None
        if not task or task[0]!=OWNER:raise ValueError('Claimed verified owner task required')
        candidates=db.execute('SELECT record_json FROM media_records WHERE owner=? AND (attached_to=? OR id=?)',(OWNER,task_id,task_id)).fetchall()
        record=next((json.loads(r[0]) for r in candidates if str((json.loads(r[0]).get('video') or {}).get('video_id'))==video_id),None)
        if not record or not eligible(json.loads(task[1]),record,cfg):raise ValueError('Work not eligible under current shared-work policy')
        if record.get('status')!='saved' or not record.get('files'):raise ValueError('Actual saved work required')
        if record.get('media_type')!='image_gallery' and (record.get('shared_video_analysis') or {}).get('status')!='done':
            raise ValueError('Video visual evidence not ready')
        # Also respect historical public attempts made by the former browser module.
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='video_comments'").fetchone() and db.execute('SELECT 1 FROM video_comments WHERE video_id=?',(video_id,)).fetchone():
            raise ValueError('This work already has a public comment attempt')
        if db.execute('SELECT 1 FROM shared_comment_outbox WHERE video_id=?',(video_id,)).fetchone():
            raise ValueError('This work already has a public comment attempt')
        day=datetime.now(TZ).date().isoformat()
        if db.execute('SELECT count(*) FROM shared_comment_outbox WHERE day=?',(day,)).fetchone()[0]+manual_attempt_count(db,day)>=int(cfg.get('daily_limit',10)):
            raise ValueError('Daily shared-work comment quota reached')
        db.execute('INSERT INTO shared_comment_outbox VALUES(?,?,?,?,?,?,?,NULL)',(video_id,task_id,text.strip(),evidence.strip(),time.time(),day,'waiting_for_reply'))
    return {'status':'waiting_for_reply','video_id':video_id,'automatic_retry':False}

def verify_self(me):
    if not isinstance(me,dict) or me.get('status_code')!=0 or str(me.get('user_uid'))!=ACCOUNT_UID:
        raise ValueError('Publishing browser is not the verified assistant account')
    return me

def verify_owner(owner):
    if not isinstance(owner,dict) or str(owner.get('uid'))!=OWNER_UID or owner.get('sec_uid')!=OWNER_SEC_UID:
        raise ValueError('Owner mention identity mismatch in the suggestion response')
    nickname=owner.get('nickname')
    if not isinstance(nickname,str) or not nickname.strip() or len(nickname)>50:raise ValueError('Owner nickname unavailable')
    return nickname

def check_receipt(value,text):
    comment=value.get('comment') or {};cid=str(comment.get('cid') or '')
    result={'status':'outcome_uncertain','automatic_retry':False,'owner_notification_confirmed':False}
    if value.get('status_code') not in (None,0):return {**result,'status':'platform_rejected','status_code':value.get('status_code')}
    if value.get('status_code')==0 and cid and cid.isdigit():
        result.update(status='accepted_unverified',comment_id=cid)
        extras=comment.get('text_extra') or []
        if isinstance(extras,str):
            try:extras=json.loads(extras)
            except ValueError:extras=[]
        result['mention_in_receipt']=any(isinstance(x,dict) and str(x.get('user_id'))==OWNER_UID for x in extras)
        result['text_matches']=isinstance(comment.get('text'),str) and comment['text'].strip()==text.strip()
    return result

def _norm(text):
    return ' '.join(str(text or '').split())

def _find_comment(comments,expected,cid):
    """Locate our comment by id, else by exact text plus our own author uid."""
    for comment in comments:
        if not isinstance(comment,dict):continue
        if cid and str(comment.get('cid'))==str(cid):return comment
        if not cid and _norm(comment.get('text'))==_norm(expected) and str((comment.get('user') or {}).get('uid'))==ACCOUNT_UID:
            return comment
    return None

def step(db,api=None):
    if api is not None:return _step(db,api)
    schema(db)
    if not policy().get('enabled') or not db.execute("SELECT 1 FROM shared_comment_outbox WHERE status='pending' LIMIT 1").fetchone():return None
    from app.comment_profile import ProfileLease,ProfileBusy
    try:
        with ProfileLease() as lease:return _step(db,lease=lease)
    except ProfileBusy:return {'status':'browser_busy','automatic_retry':False}


def _step(db,api=None,lease=None):
    schema(db);cfg=policy()
    if not cfg.get('enabled'):return None
    mention_owner=cfg.get('mention_owner',True) is not False
    row=db.execute("SELECT video_id,task_id,text,detail FROM shared_comment_outbox WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
    if not row:return None
    work_kind='video'
    try:
        for candidate in db.execute('SELECT record_json FROM media_records WHERE owner=? AND (attached_to=? OR id=?)',(OWNER,row[1],row[1])).fetchall():
            record=json.loads(candidate[0])
            if str((record.get('video') or {}).get('video_id'))==str(row[0]):
                work_kind=str(record.get('media_type') or record.get('kind') or 'video')
                break
    except (sqlite3.Error,ValueError):
        work_kind='video'
    # Reserve before ANY network operation; interruption cannot cause a second publish.
    with db:
        db.execute('BEGIN IMMEDIATE')
        day=datetime.now(TZ).date().isoformat()
        attempts=db.execute("SELECT count(*) FROM shared_comment_outbox WHERE day=? AND status!='pending'",(day,)).fetchone()[0]+manual_attempt_count(db,day)
        if attempts>=int(cfg.get('daily_limit',10)):
            db.execute("UPDATE shared_comment_outbox SET status='quota_blocked' WHERE video_id=? AND status='pending'",(row[0],))
            return {'status':'quota_blocked','video_id':row[0],'automatic_retry':False}
        if not db.execute("UPDATE shared_comment_outbox SET status='attempted' WHERE video_id=? AND status='pending'",(row[0],)).rowcount:return None
        db.execute('UPDATE shared_comment_outbox SET day=? WHERE video_id=?',(day,row[0]))
    owns=api is None;posted=False
    if owns:
        from app.browser_publish import HiddenCommentBrowser
        api=HiddenCommentBrowser()
        api._external_lease=lease
        try:api.__enter__()
        except Exception as exc:
            result={'status':'blocked_before_publish','error_class':type(exc).__name__,'automatic_retry':False}
            api=None
    try:
        if api is not None:
            verify_self(api.self_identity())
            # Once the publish flow is entered it may have submitted; treat any
            # later failure as uncertain so it is never retried automatically.
            posted=True
            receipt=api.publish(row[0],row[2],mention_owner=mention_owner,kind=work_kind)
            if mention_owner:verify_owner(receipt.get('owner_identity') or {})
            expected=receipt.get('editor_text') or row[2]
            result=check_receipt(receipt,expected)
            result['mention_owner']=mention_owner
            if result['status']!='platform_rejected':
                try:
                    current=api.comments(row[0],kind=work_kind)
                    found=_find_comment(current.get('comments') or [] if current.get('status_code')==0 else [],expected,result.get('comment_id'))
                    if found:
                        proof=check_receipt({'status_code':0,'comment':found},expected)
                        result.update(comment_id=str(found.get('cid')),readback_found=True,
                            mention_in_readback=proof.get('mention_in_receipt',False),text_matches=proof.get('text_matches',False))
                        if result['text_matches'] and (not mention_owner or result['mention_in_readback']) and str((found.get('user') or {}).get('uid'))==ACCOUNT_UID:
                            result['status']='visible_in_account_history'
                        elif result['status']=='outcome_uncertain':
                            result['status']='accepted_unverified'
                    else:
                        result['readback_found']=False
                        if result.get('comment_id'):result['readback_cid_missing']=True
                except Exception:result['readback_found']=False
    except Exception as exc:
        result={'status':'outcome_uncertain' if posted else 'blocked_before_publish','error_class':type(exc).__name__,'automatic_retry':False}
        diagnostic=getattr(api,'last_diagnostic',None) if api is not None else None
        if isinstance(diagnostic,dict) and diagnostic:
            result['diagnostic']=diagnostic
            if diagnostic.get('submitted') is False:result['status']='blocked_before_publish'
    finally:
        if owns and api is not None:
            try:api.close()
            except Exception:pass
    result.update(video_id=row[0],task_id=row[1],transport='real_browser',owner_notification_confirmed=False)
    result.setdefault('mention_owner',mention_owner)
    with db:db.execute('UPDATE shared_comment_outbox SET status=?,detail=? WHERE video_id=?',(result['status'],json.dumps(result,ensure_ascii=False),row[0]))
    return result

def status(db,task_id):
    schema(db)
    return [{'video_id':r[0],'status':r[1],'detail':json.loads(r[2]) if r[2] else None} for r in db.execute('SELECT video_id,status,detail FROM shared_comment_outbox WHERE task_id=?',(task_id,))]
