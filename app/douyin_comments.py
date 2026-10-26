"""Bounded read-only comment context; audience opinion is not video evidence."""
import json
from app.douyin_content import transcript_path

# Owner-set 2026-10-23: read up to fifteen first-page comments instead of five.
# The platform still decides how many the first page actually returns.
COMMENT_LIMIT = 15


def normalize(payload,limit=COMMENT_LIMIT):
    rows=payload.get('comments') or []
    result=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('text'),str): continue
        result.append({'comment_id':str(row.get('cid','')),'text':row['text'][:300],
                       'likes':row.get('digg_count',0),'reply_count':row.get('reply_comment_total',0)})
        if len(result)>=limit: break
    return result


def fetch(context,video_id,limit=COMMENT_LIMIT,persist=True):
    from app.douyin_read_api import request
    from app.douyin_toolkit import verification_present
    response=None
    try:
        response=request(context,'/aweme/v1/web/comment/list/',{'aweme_id':video_id,'cursor':'0','count':str(min(limit,50)),'item_type':'0'})
        if response.status!=200: return {'status':'unavailable','reason':'Comments HTTP '+str(response.status)}
        body=response.body()
        if not body or len(body)>2_000_000: return {'status':'unavailable','reason':'Empty or oversized comments response'}
        value=json.loads(body)
        if verification_present(value): return {'status':'needs_verification','automatic_retry':False}
        if not isinstance(value,dict) or str(value.get('status_code'))!='0': return {'status':'unavailable','reason':'Comments business status unsuccessful'}
        result={'status':'done','video_id':video_id,'items':normalize(value,limit),'order':'platform_default_first_page',
                'untrusted_content':True,'evidence_limit':'Audience opinions; not a transcript or verified facts'}
        if persist:
            path=transcript_path(video_id).parent/'comments.json';path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    except Exception:
        return {'status':'unavailable','reason':'Comments request or decoding failed','automatic_retry':False}
    finally:
        if response is not None: response.dispose()


def verify(db,context,video_id):
    row=db.execute('SELECT status,detail FROM video_comments WHERE video_id=?',(video_id,)).fetchone()
    if not row: raise ValueError('No comment submission exists')
    prior=json.loads(row[1])
    receipts=prior.get('receipt',[])
    expected=next((r['comment_id'] for r in reversed(receipts) if r.get('status_code')==0 and r.get('comment_id')),None)
    if not expected: return {'status':'not_verifiable','reason':'No successful comment ID receipt','automatic_retry':False}
    current=fetch(context,video_id,50,persist=False)
    match=next((r for r in current.get('items',[]) if r['comment_id']==expected),None)
    result={'status':'visible_in_history' if match else 'not_observed','video_id':video_id,'comment_id':expected,
            'readback_transport':'session_http','readback':match,'owner_notification_confirmed':bool(prior.get('owner_notification_confirmed')),'automatic_retry':False}
    if match:
        prior.update(result)
        prior['public_visibility_confirmed']=False
        prior['visibility_scope']='Readable through this account API; other accounts and mention notifications not verified'
        db.execute('UPDATE video_comments SET status=?,detail=? WHERE video_id=?',('visible_in_history',json.dumps(prior,ensure_ascii=False),video_id));db.commit()
    return result
