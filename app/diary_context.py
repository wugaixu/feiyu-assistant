"""Verify workflow facts before diary scope, greetings and interaction bookkeeping."""
import json,re,sqlite3,time
from contextlib import closing
from pathlib import Path
ROOT=Path(r'__RUNTIME_ROOT__')


def event_text(event):
    getter=getattr(event,'get_message_str',None)
    return str(getter() if callable(getter) else getattr(event,'message_str','') or '')


def message(event):
    text=event_text(event)
    tokens=re.findall(r'\[本轮看图许可:([0-9a-f]{48})\]',text)
    envelope='"instructions_file"' in text and '"local_task_id"' in text
    if not tokens:return {'invalid':True} if envelope else None
    try:
        from app.sdk_receiver import OWNER
        with closing(sqlite3.connect('file:'+(ROOT/'assistant.sqlite3').as_posix()+'?mode=ro',uri=True,timeout=2)) as db:
            row=db.execute('''SELECT t.raw_json,p.session_id FROM local_vision_permits p
                JOIN live_tasks t ON t.id=p.task_id JOIN chat_dispatch d ON d.task_id=t.id
                WHERE p.token=? AND p.expires>? AND t.owner=? AND t.status='processing'
                AND d.status IN ('queued','attempted') AND json_extract(d.detail,'$.provider')='astrbot'
                AND json_extract(d.detail,'$.session_id')=p.session_id''',(tokens[-1],time.time(),OWNER)).fetchone()
        if not row:return {'invalid':True}
        sid=str(getattr(getattr(event,'message_obj',None),'session_id',''))
        if sid!=row[1] and not sid.endswith('!'+row[1]):return {'invalid':True}
        raw=json.loads(row[0])
        if raw.get('direction')!='incoming':return {'invalid':True}
        return raw
    except (OSError,sqlite3.Error,ValueError,TypeError):return {'invalid':True}


def session(event,session_type):
    raw=message(event)
    if raw and raw.get('invalid'):
        # An invalid workflow envelope must not inherit WebChat's owner identity.
        return session_type(platform='workflow',message_type='GroupMessage',session_id='unverified')
    if raw and raw.get('is_group'):
        gid=str(raw.get('group_id') or '')
        return session_type(umo='workflow:GroupMessage:'+gid,platform='workflow',message_type='GroupMessage',
            session_id=gid,group_id=gid,sender_id=str(raw.get('sender_uid') or ''),
            sender_name=str(raw.get('sender_name') or ''),group_name=str(raw.get('group_name') or ''))
    return session_type.from_event(event)


def text(event):
    raw=message(event)
    if raw is None:return str(getattr(event,'message_str','') or event_text(event))
    if raw.get('invalid'):return ''
    from app.task_context import summary
    value=summary(raw)
    return re.sub(r'^\[(?:鲸聊音频|抖音语音|QQ语音)自动转写[^\]]*\]\s*','',value).strip()
