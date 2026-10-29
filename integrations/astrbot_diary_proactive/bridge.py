"""Installed as diary plugin's workflow_bridge.py; no AstrBot core patches."""
import asyncio,copy,json,sys,time
from pathlib import Path
PROJECT=Path(r'__PROJECT_DIR__')
if str(PROJECT) not in sys.path:sys.path.insert(0,str(PROJECT))
from app import diary_proactive as bridge
from app import diary_context
import importlib
if getattr(bridge,"__spec__",None) is not None:bridge=importlib.reload(bridge)
diary_context=importlib.reload(diary_context)
from app.live_service import LiveStore,ROOT
from .core.proactive import diagnostics

def mark_loaded(plugin):
    report={'adapter_version':1,'updated_at':time.time(),'checked_at':None,
            'enabled':plugin.settings.subsystem('proactive'),'reason':'接入就绪，等待插件定期巡检',
            **{k:plugin.settings.proactive.get(k) for k in ('patrol_minutes','daily_limit_private','min_interval_minutes')}}
    (ROOT/'diary-proactive-state.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')

async def sync(plugin):
    store=LiveStore(ROOT/'assistant.sqlite3');db=store.db
    try:
        bridge.schema(db)
        from app.outbox import SUCCESS
        for row in bridge.jobs(db,"ack_state='pending'"):
            states=[x[0] for x in db.execute('SELECT status FROM reply_outbox WHERE task_id=? ORDER BY seq',(row['id'],))]
            success=bool(states) and all(s in SUCCESS for s in states)
            terminal=row['status'] in ('cancelled','outcome_uncertain') or any(s not in SUCCESS and s not in ('pending','attempted') for s in states)
            if not success and not terminal:continue
            with db:
                if not db.execute("UPDATE diary_proactive_jobs SET ack_state='attempted' WHERE id=? AND ack_state='pending'",(row['id'],)).rowcount:continue
            if success:
                decision=json.loads(row['decision'])
                plugin.proactive._pending_log[row['umo']]={'slot':decision.get('slot',''),'fragment':decision.get('fragment','')}
                await plugin.proactive.confirm_sent(row['umo'],now=plugin._now())
            await diagnostics.delivery(plugin.proactive,row['umo'],success=success,now=plugin._now(),
                reason='平台已接受，未代表手机实际收到' if success else '工作流未确认发送，不自动重发')
            result='done' if success else ('cancelled' if row['status']=='cancelled' or 'expired' in states else 'outcome_uncertain')
            with db:db.execute('UPDATE diary_proactive_jobs SET ack_state=?,status=? WHERE id=?',('accepted' if success else 'not_confirmed',result,row['id']))
    finally:db.close()

async def patrol(plugin):
    """The diary still owns every trigger, gate and quota; workflow owns delivery."""
    await sync(plugin)
    enabled=plugin.settings.subsystem('proactive');reason='等待插件触发条件';decision=None
    store=LiveStore(ROOT/'assistant.sqlite3')
    try:
        bridge.schema(store.db)
        if not enabled:reason='插件主动消息已关闭'
        elif not bridge.target():reason='当前所选会话未启用情绪日记，主动联系暂停'
        elif not await asyncio.to_thread(bridge.manager_online):reason='管理台离线，不创建积压主动消息'
        elif bridge.busy(store.db) or store.db.execute("SELECT 1 FROM diary_proactive_jobs WHERE status IN ('pending','generating','queued') AND expires>?",(time.time(),)).fetchone():reason='会话或上一条主动消息处理中'
        else:
            umo=bridge.expected_umo()
            decision=await plugin.proactive.patrol(now=plugin._now(),allowed_umos={umo})
            await diagnostics.record_check(plugin.proactive,decision,now=plugin._now())
            if decision:
                bridge.enqueue(store.db,decision)
                reason='插件已触发，等待统一发送队列处理'
    except Exception as exc:reason='插件接入检查失败：'+type(exc).__name__
    finally:
        report={'adapter_version':1,'updated_at':time.time(),'checked_at':time.time(),'enabled':enabled,'reason':reason,
                'patrol_minutes':plugin.settings.proactive.get('patrol_minutes'),
                'daily_limit_private':plugin.settings.proactive.get('daily_limit_private'),
                'min_interval_minutes':plugin.settings.proactive.get('min_interval_minutes')}
        temp=ROOT/'diary-proactive-state.json.tmp'
        temp.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8');temp.replace(ROOT/'diary-proactive-state.json')
        store.db.close()

def prepare_request(event,req):
    if not bridge.is_active_prompt(event.get_message_str(),getattr(event.message_obj,'session_id',None)):return False
    if req.func_tool is not None:
        req.func_tool=copy.copy(req.func_tool)
        req.func_tool.remove_tool('send_message_to_user')
    return True  # No fake incoming interaction, greeting or affinity update.


def session_for_event(event,session_type):return diary_context.session(event,session_type)


def owner_text(event):return diary_context.text(event)
