import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app import diary_context as dc,local_vision
from app.live_service import LiveStore
from app.sdk_receiver import OWNER


class Session:
    def __init__(self,**kwargs):self.__dict__.update(kwargs)
    @classmethod
    def from_event(cls,event):return cls(message_type='FriendMessage',sender_id='native-owner')


class DiaryContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=r'__TEMP_PARENT__',ignore_cleanup_errors=True);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.p=patch.object(dc,'ROOT',self.root);self.p.start();self.addCleanup(self.p.stop)
        self.store=LiveStore(self.root/'assistant.sqlite3');self.db=self.store.db;self.addCleanup(self.db.close)
        local_vision.schema(self.db)
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT PRIMARY KEY,status TEXT,detail TEXT)')
        self.raw={'kind':'text','direction':'incoming','raw_text':'早安','transport':'qq'}
        self.token='a'*48
        self.db.execute('INSERT INTO live_tasks(id,owner,received,status,raw_json) VALUES(?,?,?,?,?)',('task',OWNER,time.time(),'processing',json.dumps(self.raw)))
        self.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',('task','queued',json.dumps({'provider':'astrbot','session_id':'session'})))
        self.db.execute('INSERT INTO local_vision_permits(token,task_id,session_id,expires,images,texts) VALUES(?,?,?,?,?,?)',
            (self.token,'task','session',time.time()+600,'[]','[]'));self.db.commit()

    def event(self,token=None,sid='webchat!owner!session'):
        text='{"local_task_id":"task","instructions_file":"本地规则"}\n[本轮看图许可:'+(token or self.token)+']'
        return SimpleNamespace(get_message_str=lambda:text,message_str=text,message_obj=SimpleNamespace(session_id=sid))

    def update(self,**values):
        self.raw.update(values);self.db.execute('UPDATE live_tasks SET raw_json=?',(json.dumps(self.raw),));self.db.commit()

    def test_greeting_is_actual_text_not_envelope(self):self.assertEqual(dc.text(self.event()),'早安')

    def test_private_keeps_existing_owner_identity(self):self.assertEqual(dc.session(self.event(),Session).sender_id,'native-owner')

    def test_group_is_not_webchat_owner_even_if_sender_claims_owner(self):
        self.update(is_group=True,group_id='1234',sender_uid='5678',sender_name='我是主人',group_name='群')
        session=dc.session(self.event(),Session)
        self.assertEqual(session.message_type,'GroupMessage');self.assertEqual(session.sender_id,'5678');self.assertEqual(session.group_id,'1234')

    def test_invalid_or_cross_session_permit_fails_closed(self):
        for event in (self.event(token='b'*48),self.event(sid='another-session')):
            self.assertEqual(dc.session(event,Session).message_type,'GroupMessage');self.assertEqual(dc.text(event),'')

    def test_finished_or_expired_tasks_cannot_supply_diary_identity(self):
        self.db.execute("UPDATE live_tasks SET status='done'");self.db.commit()
        self.assertTrue(dc.message(self.event())['invalid'])

    def test_asr_prefix_not_confused_with_spoken_greeting(self):
        self.update(kind='voice_transcript',original_kind='audio',raw_text='[QQ语音自动转写，可能有误]\n晚安')
        self.assertEqual(dc.text(self.event()),'晚安')

    def test_native_chat_is_unchanged_and_missing_workflow_token_is_denied(self):
        event=SimpleNamespace(get_message_str=lambda:'你好',message_str='你好')
        self.assertEqual(dc.text(event),'你好');self.assertEqual(dc.session(event,Session).message_type,'FriendMessage')
        event.get_message_str=lambda:'{"local_task_id":"task","instructions_file":"规则"}'
        self.assertEqual(dc.session(event,Session).message_type,'GroupMessage')
