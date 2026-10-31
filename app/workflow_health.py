"""Read-only evidence-based stage monitoring; never starts business work."""
import json,sqlite3,shutil,time
from pathlib import Path
from datetime import datetime,timezone,timedelta

LABELS={'ready':'就绪','idle':'待机','busy':'处理中','note':'已检查','warn':'需关注','error':'异常','off':'未启用','unknown':'待核验','blocked':'等待恢复'}
TERMINAL_COMMENTS={'visible_in_account_history','owner_confirmed_published'}


def snapshot(path):
    result={'ok':False,'counts':{},'undispatched':0,'dispatch_uncertain':0,'dispatch_rejected':0,'media':[],'comments':[],'reply_pending':0}
    try:db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=1)
    except sqlite3.Error:return result
    try:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'live_tasks' not in tables:return result
        result['counts']=dict(db.execute('SELECT status,count(*) FROM live_tasks GROUP BY status'))
        if 'chat_dispatch' in tables:
            result['undispatched']=db.execute("SELECT count(*) FROM live_tasks WHERE status='pending' AND id NOT IN (SELECT task_id FROM chat_dispatch)").fetchone()[0]
            result['dispatch_uncertain']=db.execute("SELECT count(*) FROM live_tasks t JOIN chat_dispatch d ON d.task_id=t.id WHERE t.status='pending' AND d.status IN ('attempted','outcome_uncertain')").fetchone()[0]
            result['dispatch_rejected']=db.execute("SELECT count(*) FROM live_tasks t JOIN chat_dispatch d ON d.task_id=t.id WHERE t.status='pending' AND d.status='rejected'").fetchone()[0]
        if 'media_records' in tables:
            for received,raw in db.execute('SELECT received,record_json FROM media_records ORDER BY received DESC LIMIT 40'):
                try:record=json.loads(raw)
                except ValueError:continue
                if not isinstance(record,dict):continue
                result['media'].append({'received':received,'record':record})
        if 'shared_comment_outbox' in tables:
            for status,created,video_id,raw in db.execute('SELECT status,created,video_id,detail FROM shared_comment_outbox ORDER BY created DESC LIMIT 30'):
                try:detail=json.loads(raw or '{}')
                except ValueError:detail={}
                if not isinstance(detail,dict):detail={}
                result['comments'].append({'status':status,'created':created,'video_id':video_id,'error_class':detail.get('error_class'),'diagnostic':detail.get('diagnostic',{})})
        if 'reply_outbox' in tables:
            result['reply_counts']=dict(db.execute('SELECT status,count(*) FROM reply_outbox GROUP BY status'))
            result['reply_pending']=result['reply_counts'].get('pending',0)
            result['reply_attempted']=result['reply_counts'].get('attempted',0)
            from app.queue_maintenance import resolutions,resolved
            handled=resolutions(db)
            result['reply_uncertain']=0 if not result['reply_counts'].get('outcome_uncertain') else sum(not resolved(status,handled.get(('send',key))) for key,status in db.execute("SELECT id,status FROM reply_outbox WHERE status='outcome_uncertain'"))
        # Route through the same immutable address resolver as the sender.
        from app.channels import address
        result['reply_routes']={}
        for task_id,count in db.execute("SELECT task_id,count(*) FROM reply_outbox WHERE status='pending' GROUP BY task_id") if 'reply_outbox' in tables else ():
            try:channel=address(db,task_id)['channel']
            except (ValueError,TypeError,sqlite3.Error):channel='unknown'
            result['reply_routes'][channel]=result['reply_routes'].get(channel,0)+count
        result['active_channel']='douyin'
        if 'channel_current' in tables:
            active=db.execute('SELECT channel FROM channel_current WHERE id=1').fetchone()
            if active:result['active_channel']=active[0]
        result['ok']=True
    except sqlite3.Error:result['ok']=False
    finally:db.close()
    return result


def assess(workflow,target,policies,data,dependencies,agent,storage,now=None):
    """Pure classification, so missing/stale/idle states can be tested."""
    now=time.time() if now is None else now
    receiver=workflow.get('receiver') or {};sdk=workflow.get('sdk') or {}
    age=receiver.get('age_seconds');fresh=isinstance(age,(int,float)) and 0<=age<=12
    receiving=fresh and receiver.get('state')=='listening'
    modules=[]
    def add(key,title,state,detail,metric='',tab='tasks'):
        modules.append({'id':key,'title':title,'state':state,'label':LABELS[state],'detail':detail,'metric':metric,'tab':tab,'checked_at':now})
    from app.sdk_receiver import ACCOUNT_UID
    sdk_ok=sdk.get('online') is True and str(sdk.get('uid'))==ACCOUNT_UID
    add('sdk','抖音聊天连接','ready' if sdk_ok else 'error',
        '当前 SDK 账号在线，身份匹配' if sdk_ok else ('连接离线、需要登录或账号身份不匹配'),str(sdk.get('nickname') or '未取得账号状态'),'overview')
    if not fresh:add('inbox','私信接收与入库','error','接收器状态缺失或已过期，不能据旧记录认定在线',f'状态距今 {age if age is not None else "未知"} 秒')
    elif receiver.get('state')=='disabled':add('inbox','私信接收与入库','off','接收器未启用',f'状态距今 {age} 秒')
    elif not receiving:add('inbox','私信接收与入库','error','接收器报告异常，请查看日志',str(receiver.get('error_class') or receiver.get('state')),'logs')
    elif not data.get('ok'):add('inbox','私信接收与入库','warn','接收器在线，但管理台无法读取任务数据库','数据库状态待核验','logs')
    elif (receiver.get('ingest') or {}).get('rejected'):add('inbox','私信接收与入库','warn','坏消息已隔离，其余消息继续接收',f"本轮隔离 {receiver['ingest']['rejected']} 条")
    else:add('inbox','私信接收与入库','ready','接收器监听中，任务数据库可读',f'心跳 {age} 秒前')
    counts=data.get('counts',{});media=data.get('media',[])
    downloads=counts.get('media_pending',0)
    if not dependencies.get('douk'):add('download','媒体下载与保存','error','DouK组件未找到，不能判断为可用','需检查本地工具','logs')
    elif downloads:add('download','媒体下载与保存','busy' if receiving else 'blocked','等待媒体工作线程处理' if receiving else '接收器未就绪，下载队列暂不能处理',f'排队 {downloads} 条')
    elif media and media[0]['record'].get('status')=='unavailable':add('download','媒体下载与保存','warn','最近一条媒体未成功保存，查看任务资料中的具体原因','最近任务失败')
    else:add('download','媒体下载与保存','idle' if receiving else 'blocked','组件已准备，按消息需要下载；待机不代表已实际下载','当前无下载排队')
    for key,title,field,ready in [('visual','视频画面解析','shared_video_analysis',dependencies.get('ffmpeg')),('audio','语音与视频音频识别','audio_analysis',dependencies.get('sensevoice') and dependencies.get('ffmpeg'))]:
        recent=[x['record'][field] for x in media if isinstance(x['record'].get(field),dict)]
        busy=sum(r.get('status')=='processing' for r in recent)
        if not ready:add(key,title,'error','所需本地组件缺失，请检查工具目录','组件未就绪','logs')
        elif busy:add(key,title,'busy' if receiving else 'blocked','后台正在处理新媒体' if receiving else '处理状态尚未结束，但接收器心跳异常',f'处理 {busy} 条')
        elif recent and recent[0].get('status') in ('failed','unavailable','partial'):add(key,title,'warn','最近一次结果不完整或失败；其他已取得资料仍可使用',f'最近结果：{recent[0].get("status")}')
        else:
            detail='按需运行，不需要常驻识别进程' if key=='audio' else '按需解码抽帧，没有新视频时正常待机'
            result=recent[0].get('status') if recent else '尚无新任务'
            if result in ('no_audio','no_speech_recognized'):detail='最近任务无音轨或未识别出语音，属于处理结果，不是服务掉线'
            if not receiving:detail='组件存在，但接收器未就绪，等待工作流恢复'
            add(key,title,'idle' if receiving else 'blocked',detail,f'最近结果：{result}')
    provider=target.get('provider','codex');pending=data.get('undispatched',0);processing=counts.get('processing',0)
    provider_label={'codex':'Codex','dsh':'DSH','astrbot':'AstrBot'}.get(provider,provider)
    if not agent.get('available'):add('agent','会话投递与分析','error',agent.get('reason') or '目标会话连接不可用',provider_label,'sessions')
    elif data.get('dispatch_rejected'):add('agent','会话投递与分析','warn','有被会话明确拒绝的投递，不自动重发；后续新消息可继续处理',f'{data["dispatch_rejected"]} 条被拒绝','tasks')
    elif data.get('dispatch_uncertain'):add('agent','会话投递与分析','warn','有已尝试但未确认的投递，保留记录且不自动重发',f'{data["dispatch_uncertain"]} 条待核验','tasks')
    elif processing:add('agent','会话投递与分析','busy','目标会话可访问，等待已领取任务完成',f'{provider_label} · {processing} 条已领取','sessions')
    elif agent.get('running'):add('agent','会话投递与分析','busy','目标会话正在运行；当前没有本项目已领取任务',f'{provider_label} 会话活跃','sessions')
    elif pending:add('agent','会话投递与分析','busy' if receiving else 'blocked','等待媒体准备完成或下一轮投递',f'未投递 {pending} 条','tasks')
    else:add('agent','会话投递与分析','idle',f'已连接：{agent.get("title") or "指定会话"}',provider_label,'sessions')
    dm=policies.get('dm') or {}
    routes=data.get('reply_routes') or {data.get('active_channel','douyin'):0}
    ready={'douyin':sdk_ok,**workflow.get('channel_ready',{})}
    missing=[key for key in routes if ready.get(key) is not True]
    names={'douyin':'抖音','qq':'QQ','whale':'鲸聊','unknown':'未知来源'}
    unavailable='、'.join(names.get(key,key) for key in missing)
    route_metric=' · '.join(f'{names.get(key,key)} {count}' for key,count in routes.items())
    if dm.get('enabled',True) is not True:add('reply','私信回复发送','off','发送开关已关闭','可在设置中启用','settings')
    elif missing:add('reply','私信回复发送','blocked' if len(missing)==len(routes) else 'warn',f'{unavailable}通道未就绪；其他通道独立运行，待发送记录保留',route_metric)
    elif data.get('reply_attempted') or data.get('reply_uncertain'):
        add('reply','私信回复发送','warn','存在未确认发送，不自动重发；后续分段可在前段结束尝试后继续',f"处理中 {data.get('reply_attempted',0)} · 结果不明 {data.get('reply_uncertain',0)} · 排队 {data.get('reply_pending',0)}")
    elif data.get('reply_pending'):add('reply','私信回复发送','busy','按照现有间隔逐条发送',f'排队 {data["reply_pending"]} 条')
    elif workflow.get('reply_assessment'):
        result=workflow['reply_assessment'];add('reply','私信回复发送',result['state'],result['detail'],result['metric'])
    else:add('reply','私信回复发送','idle','发送通道就绪；平台回执不等于接收端已收到',f'最小间隔 {dm.get("min_send_gap_seconds",1)} 秒')
    comments=data.get('comments',[]);active=sum(x['status'] in ('waiting_for_reply','pending','attempted') for x in comments)
    overdue=sum(x['status'] in ('pending','attempted') and now-x.get('created',now)>600 for x in comments)
    if not (policies.get('shared_comment') or {}).get('enabled'):add('comment','作品评论与主人提及','off','公开评论开关已关闭','不影响私信和作品分享','settings')
    elif (policies.get('shared_comment') or {}).get('_warnings'):add('comment','作品评论与主人提及','warn','评论设置含无效额度，已使用默认值；请在设置中修正','配置待修正','settings')
    elif overdue:add('comment','作品评论与主人提及','warn','发布队列超过10分钟未结束，请查看记录；本页不重试发布',f'{overdue} 条较久未完成','comments')
    elif active:add('comment','作品评论与主人提及','busy' if receiving else 'blocked','按需启动发布流程，空闲时没有浏览器进程是正常的',f'排队或处理 {active} 条','comments')
    elif comments:
        from app.delivery_review import automatic
        result=automatic(comments,'comment');add('comment','作品评论与主人提及',result['state'],result['detail'],result['metric'],'comments')
    else:add('comment','作品评论与主人提及','idle','已启用，等待符合授权的新分享作品','浏览器仅在发布时运行','comments')
    if storage.get('cleanup_enabled') and storage.get('last_cleanup',{}).get('state')=='error':
        add('cleanup','资源保存与定期清理','warn','最近清理检查发生错误，请查看设置中的记录','资源不会为此强制删除','settings')
    elif storage.get('cleanup_enabled'):
        stamp=storage.get('next_run_at');when=datetime.fromtimestamp(stamp,timezone(timedelta(hours=8))).strftime('%m-%d %H:%M') if isinstance(stamp,(int,float)) else '等待调度'
        add('cleanup','资源保存与定期清理','idle',f'保留 {storage.get("retention_days",30)} 天，每 {storage.get("interval_hours",24)} 小时检查',f'下次检查：{when}（北京时间）','settings')
    else:add('cleanup','资源保存与定期清理','off','自动清理未启用，资源继续保留','可在设置中查看路径与清理预览','settings')
    if not data.get('ok'):
        for module in modules:
            if module['id'] not in ('sdk','inbox','cleanup') and module['state'] in ('ready','idle','busy','note'):
                module.update(state='unknown',label=LABELS['unknown'],detail='任务数据库不可读，暂不能核验业务队列；组件检查不能替代任务状态',metric='任务状态待核验')
    return modules


def collect(workflow,target,policies,outbound,root,db_path):
    root=Path(root)
    data=snapshot(db_path)
    from app.delivery_review import latest
    reviews=latest(root/'delivery-reviews.sqlite3')
    for comment in data.get('comments',[]):comment['review']=reviews.get(('comment',comment.get('video_id')), {})
    dependencies={'douk':(root/'tools/douk-5.8/DouK-Downloader.exe').is_file(),
        'ffmpeg':False,'sensevoice':all((root/'tools/sensevoice'/p).is_file() for p in ('models/model.int8.onnx','models/tokens.txt','node_modules/sherpa-onnx-node/sherpa-onnx.js','node_modules/sherpa-onnx-win-x64/sherpa-onnx.node')) and bool(shutil.which('node'))}
    try:
        import imageio_ffmpeg
        dependencies['ffmpeg']=Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file()
    except Exception:pass
    if target.get('provider')=='dsh':
        try:
            from app.dsh_client import sessions,capabilities
            from app.workflow_protocol import VERSION
            selected=next((s for s in sessions(timeout=2) if s['id']==target.get('effective')),None)
            agent={'available':bool(selected),**(selected or {}),'reason':'所选DSH会话已不可用' if not selected else ''}
            if capabilities(timeout=2).get('protocol_version')!=VERSION:agent={'available':False,'reason':'DSH连接器统一协议未加载，不投递新任务'}
        except Exception:agent={'available':False,'reason':'DSH连接不可用；待投递任务保留，不改投其他主体'}
    elif target.get('provider')=='astrbot':
        try:
            from app import astrbot_client
            state=astrbot_client.state(timeout=2)
            selected=next((s for s in state.get('sessions',[]) if s.get('id')==target.get('effective')),None)
            available=bool(state.get('online') and selected)
            from app.workflow_protocol import astrbot_reader
            reader=astrbot_reader()
            available=available and reader['ready']
            reason=''
            if not state.get('online'):reason='AstrBot连接不可用；待投递任务保留，不改投其他主体'
            elif not selected:reason='所选AstrBot会话已不可用，请在会话投递页重新选择'
            elif not reader['ready']:reason=reader['reason']
            agent={'available':available,'title':(selected or {}).get('title'),'reason':reason}
        except Exception:agent={'available':False,'reason':'AstrBot连接不可用；待投递任务保留，不改投其他主体'}
    else:
        from app.event_dispatch import recent_activity,codex_executable
        activity=recent_activity([target.get('effective')])
        available=bool(codex_executable()) and target.get('effective') in activity and not activity[target['effective']][1]
        agent={'available':available,'title':'当前Codex会话','reason':'Codex程序或所选会话不可用'}
    try:storage=json.loads((root/'resource-storage.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):storage={}
    try:storage['last_cleanup']=json.loads((root/'resource-cleanup-state.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):pass
    from app.delivery_review import automatic
    # Probe each required transport once, never send a diagnostic message.
    needed=set(data.get('reply_routes') or {data.get('active_channel','douyin'):0})
    channel_ready={}
    if 'qq' in needed:
        from app import qq_channel
        config=qq_channel.settings()
        health=qq_channel.health() if config.get('enabled') else {}
        channel_ready['qq']=bool(config.get('enabled') and health.get('online') and health.get('bound'))
    if 'whale' in needed:
        from app import whale_channel
        config=whale_channel.settings()
        channel_ready['whale']=bool(config.get('enabled') and config.get('devices'))
        if config.get('standalone'):
            channel_ready['whale']=channel_ready['whale'] and whale_channel.gateway_health().get('ok') is True
    workflow={**workflow,'channel_ready':channel_ready,'reply_assessment':automatic(outbound.get('items',[]))}
    modules=assess(workflow,target,policies,data,dependencies,agent,storage)
    return {'modules':modules,'checked_at':time.time(),'database_readable':data['ok'],
        'note':'就绪表示连接或组件检查通过；待机是正常按需状态，不代表已验证每条任务内容或消息送达。'}
