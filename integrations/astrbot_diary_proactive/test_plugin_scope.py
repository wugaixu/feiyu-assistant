"""Run in AstrBot's environment against an isolated patched plugin copy."""
import os,sys,unittest
import asyncio,datetime,importlib.util,json,sqlite3,tempfile,time,types
from unittest.mock import AsyncMock,Mock,patch
from pathlib import Path
PLUGIN=Path(os.environ['DIARY_PLUGIN_TEST_ROOT'])
os.environ.setdefault('ASTRBOT_ROOT',os.environ.get('DIARY_TEST_TMP',str(PLUGIN.parent/'astrbot-test-runtime')))
sys.path[:0]=[str(PLUGIN),str(PLUGIN/'test')]
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from test_proactive import TriggerCase,run,SUN
from app import diary_proactive as transport
from app.live_service import LiveStore
from app.sdk_receiver import OWNER

package=types.ModuleType('_diary_workflow_test');package.__path__=[str(PLUGIN)]
sys.modules[package.__name__]=package
spec=importlib.util.spec_from_file_location(package.__name__+'.workflow_bridge',PLUGIN/'workflow_bridge.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)

class ReceiptAndAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=os.environ['DIARY_TEST_TMP']);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=LiveStore(self.root/'assistant.sqlite3');self.db=self.store.db;self.addCleanup(self.db.close)
        self.dest={'session_id':'selected','config_id':'default'};self.umo='webchat:FriendMessage:webchat!u!selected'
        for p in (patch.object(transport,'ROOT',self.root),patch.object(adapter,'ROOT',self.root),patch.object(transport,'target',return_value=self.dest),patch.object(transport,'expected_umo',return_value=self.umo),patch.object(adapter.diagnostics,'delivery',new_callable=AsyncMock)):
            p.start();self.addCleanup(p.stop)
        transport.schema(self.db)
        self.plugin=types.SimpleNamespace(proactive=types.SimpleNamespace(_pending_log={},confirm_sent=AsyncMock(),patrol=AsyncMock()),
            _now=lambda:SUN,settings=types.SimpleNamespace(subsystem=lambda name:True,proactive={'patrol_minutes':15,'daily_limit_private':3,'min_interval_minutes':120}))
    def queued(self,status='pending'):
        key=transport.enqueue(self.db,{'umo':self.umo,'kind':'direct','slot':'signal','char':'。'})
        with self.db:
            self.db.execute("UPDATE diary_proactive_jobs SET status='queued' WHERE id=?",(key,))
            self.db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,status) VALUES(?,?,?,?,?)',(key+':0',key,OWNER,'。',status))
        return key
    def test_only_accepted_transport_receipt_updates_diary_once(self):
        key=self.queued()
        asyncio.run(adapter.sync(self.plugin));self.plugin.proactive.confirm_sent.assert_not_called()
        with self.db:self.db.execute("UPDATE reply_outbox SET status='server_accepted'")
        asyncio.run(adapter.sync(self.plugin));asyncio.run(adapter.sync(self.plugin))
        self.plugin.proactive.confirm_sent.assert_awaited_once()
        self.assertEqual(self.db.execute('SELECT ack_state,status FROM diary_proactive_jobs').fetchone(),('accepted','done'))
    def test_uncertain_receipt_does_not_claim_success_or_resend(self):
        self.queued('outcome_uncertain');asyncio.run(adapter.sync(self.plugin))
        self.plugin.proactive.confirm_sent.assert_not_called()
        self.assertEqual(self.db.execute('SELECT ack_state,status FROM diary_proactive_jobs').fetchone(),('not_confirmed','outcome_uncertain'))
    def test_codex_selection_skips_plugin_decisions(self):
        with patch.object(transport,'target',return_value=None):asyncio.run(adapter.patrol(self.plugin))
        self.plugin.proactive.patrol.assert_not_called()
    def test_only_active_diary_request_removes_direct_send_tool(self):
        key=transport.enqueue(self.db,{'umo':self.umo,'kind':'wake','note':'日记'})
        with self.db:self.db.execute("UPDATE diary_proactive_jobs SET status='generating'")
        remove=Mock();req=types.SimpleNamespace(func_tool=types.SimpleNamespace(remove_tool=remove))
        event=types.SimpleNamespace(get_message_str=lambda:'[日记主动联系:'+key+']',message_obj=types.SimpleNamespace(session_id='webchat!u!selected'))
        self.assertTrue(adapter.prepare_request(event,req));remove.assert_called_once_with('send_message_to_user')
        event.get_message_str=lambda:'普通主人私聊'
        self.assertFalse(adapter.prepare_request(event,req))

class SelectedSessionTests(TriggerCase):
    def test_regular_trigger_cannot_consume_quota_for_other_session(self):
        stack=self.make()
        run(stack.notebook.note_add(self.peer(),kind='promise',text='周三考试',due_at='2026-10-04',now=SUN))
        self.assertIsNone(run(stack.proactive.patrol(now=SUN,allowed_umos={'webchat:FriendMessage:current'})))
        decision=run(stack.proactive.patrol(now=SUN,allowed_umos={'aiocqhttp:FriendMessage:10001'}))
        self.assertEqual(decision['slot'],'promise')
    def test_signal_cannot_escape_selected_session(self):
        stack=self.make({'love_peers':['10001']})
        run(stack.state.observe(self.peer(),valence=-2,now=SUN))
        self.assertIsNone(run(stack.proactive.patrol(now=SUN,allowed_umos=set())))
        decision=run(stack.proactive.patrol(now=SUN,allowed_umos={'aiocqhttp:FriendMessage:10001'}))
        self.assertEqual(decision['slot'],'signal')

if __name__=='__main__':unittest.main()
