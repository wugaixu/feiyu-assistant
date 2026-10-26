"""Import the SDK's persistent inbox with transactionally deduplicated tasks."""
import json,time,uuid,urllib.request
from app.live_service import LiveStore,ROOT

OWNER='__OWNER_PROFILE__'
OWNER_UID='__OWNER_UID__'
ACCOUNT_UID='__ACCOUNT_UID__'

def enabled():
    try:return json.loads((ROOT/'inbound-policy.json').read_text(encoding='utf-8')).get('transport')=='sdk'
    except (OSError,ValueError):return False

def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS sdk_inbound_ids(
        message_id TEXT PRIMARY KEY,task_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sdk_inbound_cursor(id INTEGER PRIMARY KEY CHECK(id=1),seq INTEGER NOT NULL);''')

def ingest(db,events):
    """Each event commits independently; bad data cannot poison a whole page."""
    import math
    from app import channels
    channels.schema(db)
    schema(db);accepted=0;rejected={}
    db.execute('CREATE TABLE IF NOT EXISTS sdk_inbound_rejections(seq INTEGER PRIMARY KEY,reason TEXT NOT NULL,observed REAL NOT NULL)');db.commit()
    for event in events:
        seq=None
        try:
            if not isinstance(event,dict):raise ValueError('invalid_event')
            if isinstance(event.get('seq'),bool) or not isinstance(event.get('seq'),(int,str)):raise ValueError('invalid_sequence')
            seq=int(event['seq'])
            if seq<1 or seq>9223372036854775807:raise ValueError('invalid_sequence')
            if event.get('sender_uid')!=OWNER_UID or event.get('account_uid')!=ACCOUNT_UID:
                raise ValueError('identity_mismatch')
            mid=event.get('message_id')
            if not isinstance(mid,str) or not mid.strip() or len(mid)>512:raise ValueError('invalid_message_id')
            original_content=event.get('content') or {}
            if not isinstance(original_content,dict):raise ValueError('invalid_content')
            from app.sdk_share import normalize
            content=normalize(original_content)
            task=uuid.uuid4().hex;kind=content.get('kind','unknown')
            text=str(event.get('text') or '')
            if original_content.get('kind')=='unknown' and content.get('kind')=='share':
                text='[分享抖音作品] '+content['share']['title']
            received=float(event.get('timestamp') or time.time())
            if not math.isfinite(received):raise ValueError('invalid_timestamp')
            if text.strip().startswith(('测试001','测试002','测试003','测试004')):task='ignored_test'
            mapped={'share':'video_card','emoji':'image'}.get(kind,kind)
            if mapped not in ('text','image','video','audio','video_card'):mapped='sdk_message'
            message={'kind':mapped,'raw_text':text or '[抖音消息：'+kind+']','links':[],
                'direction':'incoming','transport':'sdk','sdk_message_id':mid,'sdk_content':content,
                'association_policy':'media_10s_v1','association_received_at':time.time()}
            raw=json.dumps(message,ensure_ascii=False)
        except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
            reason='identity_mismatch' if isinstance(event,dict) and (event.get('sender_uid')!=OWNER_UID or event.get('account_uid')!=ACCOUNT_UID) else 'invalid_event'
            rejected[reason]=rejected.get(reason,0)+1
            if seq is not None and 0<seq<=9223372036854775807:
                with db:
                    db.execute('INSERT OR REPLACE INTO sdk_inbound_rejections VALUES(?,?,?)',(seq,reason,time.time()))
                    db.execute('INSERT INTO sdk_inbound_cursor VALUES(1,?) ON CONFLICT(id) DO UPDATE SET seq=MAX(seq,excluded.seq)',(seq,))
            continue
        # Database failures remain fatal, retaining the last committed cursor.
        with db:
            inserted=db.execute('INSERT OR IGNORE INTO sdk_inbound_ids VALUES(?,?)',(mid,task)).rowcount
            if inserted and task!='ignored_test':
                state='media_pending' if mapped in ('image','video','audio','video_card') else 'pending'
                db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',(task,OWNER,received,raw,state))
                channels.observe(db,'douyin')
                accepted+=1
            db.execute('INSERT INTO sdk_inbound_cursor VALUES(1,?) ON CONFLICT(id) DO UPDATE SET seq=MAX(seq,excluded.seq)',(seq,))
    ingest.last_diagnostics={'rejected':sum(rejected.values()),'reasons':rejected}
    return accepted


def tick(db):
    schema(db)
    row=db.execute('SELECT seq FROM sdk_inbound_cursor WHERE id=1').fetchone()
    sdk_error=None
    try:
        with urllib.request.urlopen('http://127.0.0.1:8791/inbox?after='+str(row[0] if row else 0),timeout=5) as response:
            payload=json.load(response)
        accepted=ingest(db,payload['events'])
    except (OSError,ValueError,KeyError) as exc:
        # Record the real SDK failure, but allow an independent Whale turn to run.
        accepted=0;sdk_error=type(exc).__name__
    from app.event_dispatch import dispatch_one
    try:
        dispatch=dispatch_one(db) or {'state':'idle'}
    except Exception as exc:
        # One delivery failure must not stall receiving, media, or outbound.
        dispatch={'state':'error','error_class':type(exc).__name__,'error':str(exc)[:200],'at':time.time()}
    activity=None
    try:
        from app import autonomy,local_runtime
        if local_runtime.enabled():activity=autonomy.tick(db)
    except Exception as exc:activity={'state':'error','error_class':type(exc).__name__}
    return {'state':'degraded' if sdk_error else 'listening','sdk_inbox_error':sdk_error,'accepted':accepted,'ingest':getattr(ingest,'last_diagnostics',{}),'dispatch':dispatch,'activity':activity,'updated_at':time.time()}

def main():
    import msvcrt
    lock=(ROOT/'sdk-receiver.lock').open('a+b');lock.write(b'0');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    store=LiveStore(ROOT/'assistant.sqlite3')
    from concurrent.futures import ThreadPoolExecutor
    pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='local-media');job=None
    while True:
        try:
            state=tick(store.db) if enabled() else {'state':'disabled','updated_at':time.time()}
            from app import local_runtime
            if enabled() and local_runtime.enabled():
                if job is None or job.done():
                    if job is not None:
                        try:state['last_media']=job.result()
                        except Exception as exc:state['media_error']=type(exc).__name__
                    job=pool.submit(local_runtime.media_job,OWNER)
                state['outbound']=local_runtime.outbound_step(store.db,OWNER)
                state['runtime']='standalone';state['browser_required']=False
        except Exception as exc:state={'state':'unavailable','error_class':type(exc).__name__,'error':str(exc)[:240],'updated_at':time.time()}
        try:
            target=ROOT/'sdk-receiver-status.json';temp=target.with_suffix('.json.tmp')
            temp.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');temp.replace(target)
        except OSError:pass  # Status-file failure must not kill the receive loop.
        time.sleep(2)

if __name__=='__main__':main()
