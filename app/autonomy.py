"""Local, durable random activity scheduling. No heartbeat or public comments."""
import hashlib,json,random,re,sqlite3,time,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from app.live_service import ROOT,LiveStore

TZ=timezone(timedelta(hours=8))
DEFAULT={'browse_enabled':False,'windows':[['10:30','12:30'],['13:30','14:00'],['17:00','21:00']],
 'window_limits':[6,3,8],'window_wakes':[2,1,2],'max_per_wake':5,'daily_shares':3,'categories':['entertainment','comedy']}


def validate(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise ValueError('未知自主活动设置')
    out={**DEFAULT,**value}
    for key in ('browse_enabled',):
        if type(out[key]) is not bool:raise ValueError('开关须为布尔值')
    def hhmm(s):return isinstance(s,str) and bool(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',s))
    if not 1<=len(out['windows'])<=8 or len(out['windows'])!=len(out['window_limits']) or len(out['windows'])!=len(out['window_wakes']):raise ValueError('请设置对应的时段、视频额度和唤醒次数')
    minutes=set()
    for window in out['windows']:
        if not isinstance(window,list) or len(window)!=2 or not all(hhmm(s) for s in window) or window[0]>=window[1]:raise ValueError('刷视频时段须为当天开始早于结束的HH:MM')
        a,b=[int(s[:2])*60+int(s[3:]) for s in window]
        if minutes.intersection(range(a,b)):raise ValueError('刷视频时段不能重叠')
        minutes.update(range(a,b))
    for key,lo,hi in [('max_per_wake',1,5),('daily_shares',1,10)]:
        if type(out[key]) is not int or not lo<=out[key]<=hi:raise ValueError('设置超出范围：'+key)
    if any(type(n)is not int or not 1<=n<=30 for n in out['window_limits']) or sum(out['window_limits'])>100:raise ValueError('视频额度超出范围')
    if any(type(n)is not int or not 1<=n<=6 for n in out['window_wakes']):raise ValueError('唤醒次数超出范围')
    if not isinstance(out['categories'],list) or not out['categories'] or any(c not in ('entertainment','comedy') for c in out['categories']):raise ValueError('目前只支持娱乐/搞笑筛选')
    return out

def config():
    try:return validate({k:v for k,v in json.loads((ROOT/'autonomy-policy.json').read_text(encoding='utf-8')).items() if k in DEFAULT})
    except FileNotFoundError:return validate({})

def save(value):
    from app.resource_storage import atomic
    cfg=validate(value);atomic(ROOT/'autonomy-policy.json',cfg);return cfg

def schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS autonomy_jobs(
      id TEXT PRIMARY KEY,kind TEXT NOT NULL,day TEXT NOT NULL,slot INTEGER NOT NULL,
      created REAL NOT NULL,expires REAL NOT NULL,status TEXT NOT NULL,result TEXT);
      CREATE TABLE IF NOT EXISTS autonomy_plans(kind TEXT PRIMARY KEY,state_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS autonomy_videos(video_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,
      day TEXT NOT NULL,slot INTEGER NOT NULL,created REAL NOT NULL,status TEXT NOT NULL,detail TEXT);
    ''')
    with db:
        db.execute("DELETE FROM autonomy_plans WHERE kind='proactive'")
        db.execute("UPDATE autonomy_jobs SET status='retired' WHERE kind='proactive' AND status IN ('attempted','deferred')")
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='reply_outbox'").fetchone():
            db.execute("UPDATE reply_outbox SET status='expired' WHERE status='pending' AND task_id IN (SELECT id FROM autonomy_jobs WHERE kind='proactive')")


def read_plan(db,kind):
    row=db.execute('SELECT state_json FROM autonomy_plans WHERE kind=?',(kind,)).fetchone()
    return json.loads(row[0]) if row else {}

def store_plan(db,kind,value):
    db.execute('INSERT OR REPLACE INTO autonomy_plans VALUES(?,?)',(kind,json.dumps(value)))

def tick(db,now=None):
    schema(db);cfg=config();now=now or datetime.now(TZ)
    from app.event_dispatch import target_config
    from app import astrbot_worker
    if target_config().get('provider')!='astrbot':return {'state':'requires_astrbot'}
    due=[]
    if cfg['browse_enabled']:
        from app.video_wake_plan import plan
        bcfg={'windows':cfg['windows'],'window_wakes':cfg['window_wakes'],'min_gap_minutes':10,'skip_chance':0.3}
        state=plan(bcfg,read_plan(db,'browse'),now);store_plan(db,'browse',state)
        if state.get('pending') and state['pending']['at']<=now.timestamp():due.append(('browse',state))
    db.commit()
    if not due:return {'state':'waiting'}
    if astrbot_worker.busy() or db.execute("SELECT 1 FROM live_tasks WHERE status='processing' OR (status='pending' AND id NOT IN (SELECT task_id FROM chat_dispatch))").fetchone():return {'state':'busy'}
    if not client_ready():return {'state':'astrbot_offline'}
    from app.sdk_sender import health
    sdk=health()
    from app import channels,whale_channel
    route=channels.current(db)
    if route['channel']=='whale':
        if not whale_channel.settings().get('enabled') or route['device'] not in whale_channel.settings().get('devices',[]):return {'state':'whale_disabled'}
        sdk={'online':True,'capabilities':{'share':True}}
    elif route['channel']=='qq':
        from app import qq_channel
        if not qq_channel.permitted(route['device']):return {'state':'qq_disabled'}
        status=qq_channel.health()
        if not status.get('online') or not status.get('bound'):return {'state':'qq_offline'}
        sdk={'online':True,'capabilities':{'share':True}}
    if not sdk or not sdk.get('online'):return {'state':'sdk_offline'}
    kind,state=due[0];pending=state.pop('pending');reason=None
    if kind=='browse' and not sdk.get('capabilities',{}).get('share'):return {'state':'sdk_update_required'}
    if kind=='browse':
        from app.video_wake_plan import complete
        state['pending']=pending;state=complete(state,now)
        reason='random_skip' if pending['skip'] else None
        slot=pending['slot']
    key=uuid.uuid4().hex
    with db:
        if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
        store_plan(db,kind,state)
        db.execute('INSERT INTO autonomy_jobs VALUES(?,?,?,?,?,?,?,?)',(key,kind,now.date().isoformat(),slot,now.timestamp(),pending['end'],'skipped' if reason else 'attempted',json.dumps({'reason':reason,'destination':target_config().get('astrbot',{})})))
    if reason:return {'state':'skipped','reason':reason}
    astrbot_worker.submit_background(run_job,key)
    return {'state':'started','kind':kind,'job_id':key}

def client_ready():
    from app.astrbot_client import state
    return state()['online']

def expire_outbound(db,now=None):
    """A queued scheduled message cannot escape its window or a disabled switch."""
    now=time.time() if now is None else now;cfg=config()
    tasks=db.execute("SELECT t.id,t.raw_json,j.kind,j.expires,j.created FROM live_tasks t JOIN autonomy_jobs j ON json_extract(t.raw_json,'$.job_id')=j.id WHERE json_extract(t.raw_json,'$.origin')='local_schedule'").fetchall()
    for task,raw,kind,end,created in tasks:
        cancelled=kind!='browse' or not cfg['browse_enabled'] or now>=end
        if cancelled:
            db.execute("UPDATE reply_outbox SET status='expired' WHERE task_id=? AND status='pending'",(task,))

def activity_state(db):
    schema(db)
    try:runtime=json.loads((ROOT/'sdk-receiver-status.json').read_text(encoding='utf-8')).get('activity') or {}
    except (OSError,ValueError):runtime={}
    from app.diary_proactive import status as diary_status
    return {'config':config(),'diary_proactive':diary_status(db),'plans':{k:json.loads(v) for k,v in db.execute('SELECT kind,state_json FROM autonomy_plans')},
        'runtime':runtime,
        'jobs':[dict(zip(('id','kind','day','created','expires','status','result'),r)) for r in db.execute("SELECT id,kind,day,created,expires,status,result FROM autonomy_jobs WHERE kind='browse' ORDER BY created DESC LIMIT 20")],
        'videos_today':db.execute('SELECT count(*) FROM autonomy_videos WHERE day=?',(datetime.now(TZ).date().isoformat(),)).fetchone()[0]}

def record_job(db,key,status,result):
    old=db.execute('SELECT result FROM autonomy_jobs WHERE id=?',(key,)).fetchone()
    original=json.loads(old[0] or '{}') if old else {}
    with db:db.execute('UPDATE autonomy_jobs SET status=?,result=? WHERE id=?',(status,json.dumps({**original,**result},ensure_ascii=False),key))

def emit(db,key,text_parts,kind,work=None,reply_id=None):
    from app.sdk_receiver import OWNER
    from app import reply_batch
    from app.event_dispatch import target_config
    now=time.time();cfg=config();row=db.execute('SELECT expires,status,result FROM autonomy_jobs WHERE id=?',(key,)).fetchone()
    if kind!='browse' or not row or row[1]!='attempted' or now>=row[0] or not cfg['browse_enabled']:raise ValueError('Activity expired or disabled')
    target=target_config();destination=json.loads(row[2] or '{}').get('destination',{})
    if target.get('provider')!='astrbot' or target.get('astrbot',{})!=destination:raise ValueError('Activity destination changed')
    current=datetime.fromtimestamp(now,TZ).strftime('%H:%M')
    windows=cfg['windows']
    if not any(a<=current<b for a,b in windows):raise ValueError('Outside current activity window')
    task_id=reply_id or key
    with db:
        existing=db.execute('SELECT owner,status,raw_json FROM live_tasks WHERE id=?',(task_id,)).fetchone()
        raw=json.dumps({'kind':'scheduled_'+kind,'origin':'local_schedule','raw_text':'主人已授权的自动活动，不是主人新发来的私信','job_id':key},ensure_ascii=False)
        if existing:
            if existing[0]!=OWNER or existing[1]!='resource_saved' or json.loads(existing[2]).get('job_id')!=key:raise ValueError('Task cannot be reused')
            db.execute("UPDATE live_tasks SET status='processing',raw_json=? WHERE id=?",(raw,task_id))
        else:db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',(task_id,OWNER,now,raw,'processing'))
        db.execute('INSERT INTO chat_dispatch VALUES(?,?,?,?,?)',(task_id,now,'processed','autonomy-'+key,json.dumps({'provider':'astrbot',**target_config().get('astrbot',{})})))
    parts=([{'type':'share','work':work}] if work else [])+text_parts
    try:reply_batch.finish(db,task_id,parts)
    except Exception:
        with db:db.execute("UPDATE live_tasks SET status='failed' WHERE id=? AND status='processing'",(task_id,))
        raise

def run_job(key):
    store=LiveStore(ROOT/'assistant.sqlite3');db=store.db
    try:
        row=db.execute('SELECT kind,day,slot,expires,status FROM autonomy_jobs WHERE id=?',(key,)).fetchone()
        if not row or row[4]!='attempted':return
        if time.time()>=row[3]:record_job(db,key,'skipped',{'reason':'expired'});return
        from app.event_dispatch import target_config
        target=target_config();settings=target.get('astrbot',{})
        original=db.execute('SELECT result FROM autonomy_jobs WHERE id=?',(key,)).fetchone()
        destination=json.loads(original[0] or '{}').get('destination',{})
        if target.get('provider')!='astrbot' or settings!=destination:record_job(db,key,'skipped',{'reason':'provider_changed'});return
        if row[0]!='browse':record_job(db,key,'retired',{'reason':'replaced_by_diary_plugin'});return
        from app.autonomy_video import browse
        result=browse(db,key,row,settings,config())
        record_job(db,key,'done' if result.get('state')=='done' else 'unavailable',result)
    except Exception as exc:record_job(db,key,'outcome_uncertain',{'error_class':type(exc).__name__,'automatic_retry':False})
    finally:db.close()
