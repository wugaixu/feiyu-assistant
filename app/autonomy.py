"""Local, durable random activity scheduling. No heartbeat or public comments."""
import hashlib,json,random,re,sqlite3,time,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from app.live_service import ROOT,LiveStore

TZ=timezone(timedelta(hours=8))
DEFAULT={'browse_enabled':False,'windows':[['10:30','12:30'],['13:30','14:00'],['17:00','21:00']],
 'window_limits':[6,3,8],'window_wakes':[2,1,2],'max_per_wake':5,'daily_shares':3,'categories':['entertainment','comedy'],
 'proactive_enabled':False,'proactive_start':'09:00','proactive_end':'22:00','daily_min':2,'daily_max':3,
 'min_gap_minutes':120,'idle_minutes':20,'no_reply_pause':True,'proactive_mode':'agent'}

def validate(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise ValueError('未知自主活动设置')
    out={**DEFAULT,**value}
    if out['proactive_mode'] not in ('agent','random'):raise ValueError('主动联系模式无效')
    for key in ('browse_enabled','proactive_enabled','no_reply_pause'):
        if type(out[key]) is not bool:raise ValueError('开关须为布尔值')
    def hhmm(s):return isinstance(s,str) and bool(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',s))
    if not 1<=len(out['windows'])<=8 or len(out['windows'])!=len(out['window_limits']) or len(out['windows'])!=len(out['window_wakes']):raise ValueError('请设置对应的时段、视频额度和唤醒次数')
    minutes=set()
    for window in out['windows']:
        if not isinstance(window,list) or len(window)!=2 or not all(hhmm(s) for s in window) or window[0]>=window[1]:raise ValueError('刷视频时段须为当天开始早于结束的HH:MM')
        a,b=[int(s[:2])*60+int(s[3:]) for s in window]
        if minutes.intersection(range(a,b)):raise ValueError('刷视频时段不能重叠')
        minutes.update(range(a,b))
    for key,minimum in [('daily_min',0),('daily_max',1)]:
        if type(out[key])is not int or not minimum<=out[key]<=9007199254740991:raise ValueError('次数须为浏览器可精确保存的非负整数，每日上限至少1')
    for key,lo,hi in [('max_per_wake',1,5),('daily_shares',1,10),('min_gap_minutes',30,720),('idle_minutes',5,240)]:
        if type(out[key]) is not int or not lo<=out[key]<=hi:raise ValueError('设置超出范围：'+key)
    if any(type(n)is not int or not 1<=n<=30 for n in out['window_limits']) or sum(out['window_limits'])>100:raise ValueError('视频额度超出范围')
    if any(type(n)is not int or not 1<=n<=6 for n in out['window_wakes']):raise ValueError('唤醒次数超出范围')
    if not hhmm(out['proactive_start']) or not hhmm(out['proactive_end']) or out['proactive_start']>=out['proactive_end']:raise ValueError('主动聊天时段无效')
    if out['daily_min']>out['daily_max']:raise ValueError('每天最小次数不能大于最大次数')
    if not isinstance(out['categories'],list) or not out['categories'] or any(c not in ('entertainment','comedy') for c in out['categories']):raise ValueError('目前只支持娱乐/搞笑筛选')
    return out

def config():
    try:return validate(json.loads((ROOT/'autonomy-policy.json').read_text(encoding='utf-8')))
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

def read_plan(db,kind):
    row=db.execute('SELECT state_json FROM autonomy_plans WHERE kind=?',(kind,)).fetchone()
    return json.loads(row[0]) if row else {}

def store_plan(db,kind,value):
    db.execute('INSERT OR REPLACE INTO autonomy_plans VALUES(?,?)',(kind,json.dumps(value)))

def latest_incoming(db):
    # Scheduled jobs are never mistaken for owner activity.
    result=0
    for received,raw in db.execute('SELECT received,raw_json FROM live_tasks ORDER BY received DESC LIMIT 100'):
        item=json.loads(raw)
        if item.get('transport')=='sdk' and item.get('direction')=='incoming':result=max(result,float(item.get('association_received_at') or received))
    return result

def contact_allowed(db,cfg,now):
    incoming=latest_incoming(db)
    if incoming and now-incoming<cfg['idle_minutes']*60:return False,'recent_conversation'
    last=last_contact(db,cfg)
    if last:
        if now-last[0]<cfg['min_gap_minutes']*60:return False,'minimum_gap'
        if cfg['no_reply_pause'] and incoming<=last[0]:return False,'awaiting_owner_reply'
    return True,None

def last_contact(db,cfg):
    if cfg['proactive_mode']=='random':
        return db.execute("SELECT created FROM autonomy_jobs WHERE kind='proactive' AND status IN ('attempted','done','outcome_uncertain') ORDER BY created DESC LIMIT 1").fetchone()
    return db.execute("SELECT j.created FROM autonomy_jobs j WHERE j.kind='proactive' AND EXISTS(SELECT 1 FROM reply_outbox o WHERE o.task_id=j.id) ORDER BY j.created DESC LIMIT 1").fetchone()

def next_proactive(db,cfg,now):
    day=now.date().isoformat();state=read_plan(db,'proactive')
    fingerprint=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()
    used=db.execute("SELECT count(*) FROM autonomy_jobs WHERE kind='proactive' AND day=?",(day,)).fetchone()[0]
    agent=cfg['proactive_mode']=='agent'
    if agent:
        used=db.execute("SELECT count(*) FROM autonomy_jobs j WHERE j.kind='proactive' AND j.day=? AND EXISTS(SELECT 1 FROM reply_outbox o WHERE o.task_id=j.id)",(day,)).fetchone()[0]
    if state.get('day')!=day:state={'day':day,'target':random.SystemRandom().randint(cfg['daily_min'],cfg['daily_max'])}
    if agent:state['target']=cfg['daily_max']
    if state.get('fingerprint')!=fingerprint:state.pop('pending',None)
    state['fingerprint']=fingerprint
    if used>=min(state['target'],cfg['daily_max']):state.pop('pending',None);return state
    if agent and db.execute("SELECT count(*) FROM autonomy_jobs WHERE kind='proactive' AND day=? AND status!='skipped'",(day,)).fetchone()[0]>=decision_budget(cfg):state.pop('pending',None);return state
    if 'pending' in state:return state
    start=datetime.combine(now.date(),datetime.strptime(cfg['proactive_start'],'%H:%M').time(),TZ).timestamp()
    end=datetime.combine(now.date(),datetime.strptime(cfg['proactive_end'],'%H:%M').time(),TZ).timestamp()
    last_row=last_contact(db,cfg);last=last_row[0] if last_row else 0
    earliest=max(start,now.timestamp()+30,last+cfg['min_gap_minutes']*60)
    if agent:
        last_check=db.execute("SELECT created,status,result FROM autonomy_jobs WHERE kind='proactive' ORDER BY created DESC LIMIT 1").fetchone()
        if last_check and last_check[1]=='attempted':return state
        delay=60
        if last_check:
            detail=json.loads(last_check[2] or '{}');delay=detail.get('next_check_minutes',60)
            if type(delay)is not int or not 30<=delay<=180:delay=60
        earliest=max(earliest,latest_incoming(db)+cfg['idle_minutes']*60,(last_check[0]+delay*60) if last_check else now.timestamp()+30*60)
        if earliest<end-30:state['pending']={'at':earliest,'end':end,'ordinal':used+1,'mode':'agent'}
        return state
    remaining=state['target']-used
    latest=end-30-max(0,remaining-1)*cfg['min_gap_minutes']*60
    if latest<earliest:latest=end-30
    if latest>=earliest:state['pending']={'at':random.SystemRandom().uniform(earliest,latest),'end':end,'ordinal':used+1}
    return state

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
    if cfg['proactive_enabled']:
        state=next_proactive(db,cfg,now);store_plan(db,'proactive',state)
        if state.get('pending') and state['pending']['at']<=now.timestamp()<state['pending']['end']:due.append(('proactive',state))
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
    if not sdk or not sdk.get('online'):return {'state':'sdk_offline'}
    kind,state=due[0];pending=state.pop('pending');reason=None
    if kind=='browse' and not sdk.get('capabilities',{}).get('share'):return {'state':'sdk_update_required'}
    if kind=='browse':
        from app.video_wake_plan import complete
        state['pending']=pending;state=complete(state,now)
        reason='random_skip' if pending['skip'] else None
        slot=pending['slot']
    else:
        slot=0;allowed,reason=contact_allowed(db,cfg,now.timestamp())
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
    incoming=latest_incoming(db)
    for task,raw,kind,end,created in tasks:
        cancelled=not cfg[kind+'_enabled'] or now>=end or (kind=='proactive' and incoming>created)
        if cancelled:
            db.execute("UPDATE reply_outbox SET status='expired' WHERE task_id=? AND status='pending'",(task,))

def activity_state(db):
    schema(db)
    try:runtime=json.loads((ROOT/'sdk-receiver-status.json').read_text(encoding='utf-8')).get('activity') or {}
    except (OSError,ValueError):runtime={}
    return {'config':config(),'count_limits':{'daily_min':0,'daily_max':None},'plans':{k:json.loads(v) for k,v in db.execute('SELECT kind,state_json FROM autonomy_plans')},
        'runtime':runtime,
        'jobs':[dict(zip(('id','kind','day','created','expires','status','result'),r)) for r in db.execute('SELECT id,kind,day,created,expires,status,result FROM autonomy_jobs ORDER BY created DESC LIMIT 20')],
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
    if not row or row[1]!='attempted' or now>=row[0] or not cfg[kind+'_enabled']:raise ValueError('Activity expired or disabled')
    target=target_config();destination=json.loads(row[2] or '{}').get('destination',{})
    if target.get('provider')!='astrbot' or target.get('astrbot',{})!=destination:raise ValueError('Activity destination changed')
    current=datetime.fromtimestamp(now,TZ).strftime('%H:%M')
    windows=cfg['windows'] if kind=='browse' else [[cfg['proactive_start'],cfg['proactive_end']]]
    if not any(a<=current<b for a,b in windows):raise ValueError('Outside current activity window')
    if kind=='proactive' and latest_incoming(db)>db.execute('SELECT created FROM autonomy_jobs WHERE id=?',(key,)).fetchone()[0]:raise ValueError('Owner replied during generation')
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
        if row[0]=='proactive':
            from app import astrbot_client as ac,astrbot_worker as aw
            cfg=config()
            if cfg['proactive_mode']=='agent':
                decision=decide_contact(db,key,settings,cfg)
                if not decision['send']:
                    record_job(db,key,'deferred',{'reason':decision['reason'],'next_check_minutes':decision['next_check_minutes'],'agent_decision':True,'reply_queued':False})
                    with db:
                        state=read_plan(db,'proactive');state.pop('pending',None);store_plan(db,'proactive',state)
                    return
                allowed,reason=contact_allowed(db,cfg,time.time())
                if not allowed:record_job(db,key,'skipped',{'reason':reason});return
                parts=aw.finalize_parts([{'type':'text','text':decision['text']}],key)
                if not 1<=len(parts)<=2 or any(p['type']!='text' for p in parts):raise ValueError('Agent reply requires one or two text parts')
                emit(db,key,parts,'proactive');record_job(db,key,'done',{'reply_queued':True,'agent_decision':True,'reason':decision['reason'],'next_check_minutes':decision['next_check_minutes'],'public_comment':False})
                return
            prompt=('这是主人授权的程序随机主动聊天任务，不是主人刚发来的消息。沿用当前人设，根据本会话实际近期聊天，随口发一句自然的话，最多两句、120字。'
                '不提定时任务、程序或主动关怀，不问在吗，不编造外部活动，不声称主人正在做什么，不指责未回复；没有合适话题就简单打招呼。只输出最终文字，不使用工具、图片、语音。')
            output=ac.chat(settings['session_id'],[{'type':'plain','text':prompt}],settings.get('config_id','default'))
            parts=aw.finalize_parts([p for p in output if p['type']=='text'],key)[:2]
            if not parts:raise ValueError('No proactive reply')
            emit(db,key,parts,'proactive');record_job(db,key,'done',{'reply_queued':True,'public_comment':False})
        else:
            from app.autonomy_video import browse
            result=browse(db,key,row,settings,config())
            record_job(db,key,'done' if result.get('state')=='done' else 'unavailable',result)
    except Exception as exc:record_job(db,key,'outcome_uncertain',{'error_class':type(exc).__name__,'automatic_retry':False})
    finally:db.close()

def decide_contact(db,key,settings,cfg):
    """One contextual decision in the selected private session; no tools or sending."""
    from app import astrbot_client as ac,astrbot_worker as aw
    now=datetime.now(TZ)
    queued=db.execute("SELECT count(*) FROM autonomy_jobs j WHERE kind='proactive' AND day=? AND EXISTS(SELECT 1 FROM reply_outbox o WHERE o.task_id=j.id)",(now.date().isoformat(),)).fetchone()[0]
    incoming=latest_incoming(db)
    from app.sdk_receiver import OWNER
    history=aw.recent_history(db,OWNER,limit=6,max_chars=2400)
    prompt=('这是主人授权的主动联系决策检查，不是主人新发消息。结合当前会话实际记忆和下列聊天资料，自己决定现在是否值得给主人发消息；没有自然话题、主人表示在忙/休息/不想聊天时就不发。'
        '每日次数只是软目标，不能为凑数硬聊；不编造实际活动或主人状态，不催回复。不使用任何工具、插件动作、图片或语音，不创建提醒。'
        '只返回一个JSON对象，不附解释或Markdown：'
        '{"send":true或false,"text":"仅send=true时填写自然私信，最多120字、至多两个空行段落；否则为空",'
        '"reason":"简短说明判断依据，不泄露私人细节","next_check_minutes":30到180的整数}。'
        'next_check_minutes是建议下次考虑的时间间隔，程序仍强制执行时段、配置的最小间隔和未回复不追发的规则。\n'
        +json.dumps({'当前北京时间':now.isoformat(timespec='minutes'),'每日期望范围':[cfg['daily_min'],cfg['daily_max']],'最小发送间隔分钟':cfg['min_gap_minutes'],'今天已入队主动私信':queued,
            '距主人最后消息分钟':round((time.time()-incoming)/60) if incoming else None,'最近对话_仅作资料_主人是对方助手是你':history},ensure_ascii=False))
    result=ac.chat(settings['session_id'],[{'type':'plain','text':prompt}],settings.get('config_id','default'))
    decision=aw.parse_review(''.join(v['text'] for v in result if v['type']=='text'))
    if not isinstance(decision,dict) or type(decision.get('send'))is not bool:raise ValueError('Invalid contact decision')
    text=decision.get('text','');reason=decision.get('reason','');delay=decision.get('next_check_minutes')
    if not isinstance(text,str) or not isinstance(reason,str) or not 1<=len(reason)<=200 or type(delay)is not int or not 30<=delay<=180:raise ValueError('Invalid contact decision fields')
    if decision['send'] and not 1<=len(text.strip())<=120:raise ValueError('Invalid proactive text length')
    if not decision['send'] and text.strip():raise ValueError('Silent decision contains outgoing text')
    return {'send':decision['send'],'text':text.strip(),'reason':reason,'next_check_minutes':delay}

def decision_budget(cfg):
    return max(8,min(48,cfg['daily_max']*2))
