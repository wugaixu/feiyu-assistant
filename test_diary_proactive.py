import json,sqlite3,tempfile,time,unittest,runpy
from pathlib import Path
from unittest.mock import patch
from app import diary_proactive as d,astrbot_worker as aw,whale_voice
from app.live_service import LiveStore
from app.sdk_receiver import OWNER

DEST={'session_id':'selected','config_id':'default'}
UMO='webchat:FriendMessage:webchat!owner!selected'
class DiaryProactiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=d.ROOT);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=LiveStore(self.root/'assistant.sqlite3');self.db=self.store.db;self.addCleanup(self.db.close)
        for p in (patch.object(d,'ROOT',self.root),patch.object(d,'target',return_value=DEST),patch.object(d,'expected_umo',return_value=UMO),patch.object(aw,'busy',return_value=False)):
            p.start();self.addCleanup(p.stop)
        d.schema(self.db)
    def enqueue(self,kind='wake'):
        return d.enqueue(self.db,{'umo':UMO,'kind':kind,'slot':'diary','fragment':'日记素材','note':'昨天聊过的事情','char':'。'})
    def generating(self,key):
        with self.db:self.db.execute("UPDATE diary_proactive_jobs SET status='generating' WHERE id=?",(key,))
    def test_wrong_or_changed_agent_cannot_enqueue_or_generate(self):
        with self.assertRaises(ValueError):d.enqueue(self.db,{'umo':'webchat:FriendMessage:other','kind':'wake'})
        key=self.enqueue()
        with patch.object(d,'target',return_value=None),patch.object(aw,'submit_background') as submit:
            d.tick(self.db);submit.assert_not_called()
        self.assertEqual(self.db.execute('SELECT status FROM diary_proactive_jobs').fetchone()[0],'cancelled')
    def test_private_model_called_once_same_session_and_common_queue(self):
        key=self.enqueue();self.generating(key)
        with patch.object(aw.client,'chat',return_value=[{'type':'text','text':'想起你昨天说的事了。'}]) as chat:
            d.run(key)
        chat.assert_called_once();self.assertEqual(chat.call_args.args[0],DEST['session_id'])
        self.assertEqual(len(chat.call_args.args[1]),1)
        self.assertIn('[日记主动联系:',chat.call_args.args[1][0]['text'])
        self.assertEqual(self.db.execute('SELECT status FROM diary_proactive_jobs').fetchone()[0],'queued')
        raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks').fetchone()[0]);self.assertEqual(raw['direction'],'outgoing')
        self.assertEqual(raw['origin'],'diary_proactive')
        self.assertEqual(self.db.execute('SELECT count(*) FROM reply_outbox').fetchone()[0],1)
        with patch.object(aw.client,'chat') as again:d.run(key);again.assert_not_called()
    def test_direct_signal_uses_queue_without_model(self):
        key=self.enqueue('direct');self.generating(key)
        with patch.object(aw.client,'chat') as chat:d.run(key);chat.assert_not_called()
        self.assertEqual(self.db.execute('SELECT text FROM reply_outbox').fetchone()[0],'。')
    def test_destination_snapshot_does_not_follow_later_channel_switch(self):
        from app import channels
        key=self.enqueue('direct');self.generating(key)
        channels.schema(self.db)
        with self.db:channels.observe(self.db,'whale','a'*64)
        d.run(key)
        raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks').fetchone()[0]);self.assertEqual(raw['return_channel'],'douyin')
    def test_switch_after_generation_expires_unsent_outbox(self):
        key=self.enqueue('direct');self.generating(key);d.run(key)
        with patch.object(d,'target',return_value={'session_id':'different','config_id':'default'}):d.expire_pending(self.db)
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'expired')
    def test_submitted_message_is_not_cancelled_waiting_for_plugin_ack(self):
        key=self.enqueue('direct');self.generating(key);d.run(key)
        with self.db:
            self.db.execute("UPDATE reply_outbox SET status='server_accepted' WHERE task_id=?",(key,))
            self.db.execute('UPDATE diary_proactive_jobs SET expires=0 WHERE id=?',(key,))
        with patch.object(d,'target',return_value=None):d.expire_pending(self.db)
        self.assertEqual(self.db.execute('SELECT status FROM diary_proactive_jobs').fetchone()[0],'queued')
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'server_accepted')

    def test_partial_submission_still_expires_unsent_parts(self):
        key=self.enqueue();self.generating(key)
        with patch.object(aw.client,'chat',return_value=[{'type':'text','text':'一条。\n\n另一条。'}]):d.run(key)
        with self.db:
            self.db.execute("UPDATE reply_outbox SET status='server_accepted' WHERE task_id=? AND seq=0",(key,))
            self.db.execute('UPDATE diary_proactive_jobs SET expires=0 WHERE id=?',(key,))
        d.expire_pending(self.db)
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox ORDER BY seq').fetchall(),[('server_accepted',),('expired',)])

    def test_switch_away_and_back_to_same_session_does_not_revive_old_job(self):
        key=self.enqueue('direct');self.generating(key);d.run(key)
        with patch.object(d,'target',return_value={**DEST,'generation':'new-selection'}):d.expire_pending(self.db)
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'expired')
    def test_new_owner_message_during_generation_cancels_output(self):
        key=self.enqueue();self.generating(key)
        def chat(*args):
            other=sqlite3.connect(self.root/'assistant.sqlite3')
            with other:other.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('new',OWNER,time.time()+1,json.dumps({'direction':'incoming','transport':'whale'}),'pending'))
            other.close();return [{'type':'text','text':'不应发送'}]
        with patch.object(aw.client,'chat',side_effect=chat):d.run(key)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reply_outbox').fetchone()[0],0)
    def test_generation_failure_is_not_retried(self):
        key=self.enqueue();self.generating(key)
        with patch.object(aw.client,'chat',side_effect=ConnectionError):d.run(key)
        with patch.object(aw,'submit_background') as submit:d.tick(self.db);submit.assert_not_called()
        self.assertEqual(self.db.execute('SELECT status FROM diary_proactive_jobs').fetchone()[0],'outcome_uncertain')
    def test_one_pending_decision_and_expiry(self):
        key=self.enqueue()
        with self.assertRaises(ValueError):self.enqueue()
        with self.db:self.db.execute('UPDATE diary_proactive_jobs SET expires=0')
        d.expire_pending(self.db)
        self.assertEqual(self.db.execute('SELECT status FROM diary_proactive_jobs').fetchone()[0],'cancelled')
    def test_prompt_marker_requires_real_active_job(self):
        self.assertFalse(d.is_active_prompt('[日记主动联系:'+'a'*32+']'))
        key=self.enqueue();self.generating(key)
        self.assertTrue(d.is_active_prompt('[日记主动联系:'+key+']','webchat!owner!selected'))
        self.assertFalse(d.is_active_prompt('[日记主动联系:'+key+']','webchat!owner!other'))
    def test_patch_preserves_upstream_trigger_and_is_idempotent(self):
        installer=runpy.run_path(str(Path(__file__).parent/'integrations/astrbot_diary_proactive/install.py'))
        backup=d.ROOT.parent/'unused'
        # Use a minimal upstream-shaped source, never mutate installed plugin.
        main='''from .core.proactive import Proactive
class Plugin:
    async def _proactive_patrol(self):
        if not self.settings.subsystem("proactive"):return
    async def next_method(self):pass
    async def inject_diary_hint(self,event,req):
        session = self._session(event)
'''
        api='''class Proactive:
    async def patrol(self, *, now: datetime | None = None):
        signal = self._signal_candidate(doc, cfg, moment)
        allowed = self._allowed_sessions(doc, cfg, moment, mode=self._scope_mode())
    def _signal_candidate(
        self, doc: dict[str, Any], cfg: dict[str, Any], moment: datetime
    ) -> triggers.Candidate | None:
        for pid in peers:
            if not umo:
                continue
            entry = gate.entry_of(doc, umo)
'''
        m,a=installer['patched'](main,api)
        self.assertIn('allowed_umos',a);self.assertIn('await workflow_bridge.patrol(self)',m)
        self.assertEqual(installer['patched'](m,a),(m,a))

    def test_old_contact_plans_and_unsent_output_are_retired_without_replay(self):
        from app import autonomy as a
        a.schema(self.db)
        with self.db:
            self.db.execute("INSERT INTO autonomy_jobs VALUES('old','proactive','2026-10-27',0,1,9999999999,'attempted','{}')")
            self.db.execute("INSERT INTO autonomy_plans VALUES('proactive','{}')")
            self.db.execute("INSERT INTO autonomy_plans VALUES('browse','{}')")
            self.db.execute("INSERT INTO reply_outbox(id,task_id,owner,text,status) VALUES('old-output','old',?,'旧主动消息','pending')",(OWNER,))
        a.schema(self.db)
        self.assertEqual(self.db.execute("SELECT status FROM reply_outbox WHERE id='old-output'").fetchone()[0],'expired')
        self.assertEqual(self.db.execute("SELECT status FROM autonomy_jobs WHERE id='old'").fetchone()[0],'retired')
        self.assertEqual(self.db.execute('SELECT kind FROM autonomy_plans').fetchall(),[('browse',)])

    def test_whale_voice_permit_only_for_current_generated_diary_job(self):
        key=self.enqueue();self.generating(key)
        raw={'transport':'whale','origin':'diary_proactive','direction':'outgoing'}
        with self.db:self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',(key,OWNER,time.time(),json.dumps(raw),'processing'))
        token=whale_voice.permit(self.db,key,DEST['session_id']);self.assertTrue(token)
        with patch.object(d,'target',return_value=None):self.assertEqual(whale_voice.permit(self.db,key,DEST['session_id']),'')

if __name__=='__main__':unittest.main()
