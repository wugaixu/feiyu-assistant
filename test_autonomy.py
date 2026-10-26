"""Deterministic isolated scheduling and ordered private-share regressions."""
import copy,json,time,unittest
from datetime import datetime
from unittest.mock import patch
from app import autonomy as a,reply_batch as batch,astrbot_worker as aw
from app.live_service import LiveStore
from app.sdk_receiver import OWNER

DEST={'session_id':'test-session','config_id':'default'}
class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.store=LiveStore(':memory:');self.db=self.store.db;a.schema(self.db)
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT PRIMARY KEY,attempted REAL,status TEXT,queue_id TEXT,detail TEXT)');self.db.commit()
        self.cfg=copy.deepcopy(a.DEFAULT);self.cfg.update(proactive_enabled=True,proactive_mode='random')
        self.now=datetime(2026,10,24,10,30,tzinfo=a.TZ)
        self.patches=[patch.object(a,'config',side_effect=lambda:copy.deepcopy(self.cfg)),
            patch('app.event_dispatch.target_config',return_value={'provider':'astrbot','astrbot':DEST}),
            patch.object(a,'client_ready',return_value=True),patch('app.sdk_sender.health',return_value={'online':True,'capabilities':{'share':True}}),
            patch.object(aw,'busy',return_value=False),patch.object(aw,'submit_background')]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.db.close()
    def due(self,kind='proactive'):
        a.tick(self.db,self.now)
        state=a.read_plan(self.db,kind);state['pending']['at']=self.now.timestamp()-1
        state['pending']['skip']=False
        a.store_plan(self.db,kind,state);self.db.commit()
    def test_invalid_settings(self):
        for updates in [{'daily_min':4,'daily_max':3},{'windows':[['12:00','11:00']]},{'min_gap_minutes':1},{'categories':['news']},{'windows':[['10:00','12:00'],['11:00','13:00']],'window_limits':[1,1],'window_wakes':[1,1]}]:
            with self.assertRaises(ValueError):a.validate({**copy.deepcopy(a.DEFAULT),**updates})
    def test_ten_daily_contacts_are_allowed_without_changing_gap(self):
        cfg=a.validate({**self.cfg,'daily_min':10,'daily_max':10})
        self.assertEqual(cfg['daily_max'],10);self.assertEqual(cfg['min_gap_minutes'],120)
        self.assertEqual(a.validate({**self.cfg,'daily_max':300})['daily_max'],300)
        for count in (-1,True,1.5,9007199254740992):
            with self.assertRaises(ValueError):a.validate({**self.cfg,'daily_max':count})
        self.assertEqual(a.decision_budget(cfg),20)
    def test_agent_checks_are_deterministic_not_random_send_times(self):
        self.cfg['proactive_mode']='agent'
        first=a.next_proactive(self.db,self.cfg,self.now)
        a.store_plan(self.db,'proactive',first);self.db.commit()
        self.assertEqual(first,a.next_proactive(self.db,self.cfg,self.now))
        self.assertEqual(first['pending']['at'],self.now.timestamp()+1800)
        self.assertEqual(first['target'],self.cfg['daily_max'])
    def test_agent_decline_does_not_count_as_unanswered_contact(self):
        self.cfg['proactive_mode']='agent'
        stamp=self.now.timestamp()
        self.db.execute('INSERT INTO autonomy_jobs VALUES(?,?,?,?,?,?,?,?)',('decline','proactive','2026-10-24',0,stamp-600,stamp+3600,'deferred',json.dumps({'next_check_minutes':90})));self.db.commit()
        self.assertEqual(a.contact_allowed(self.db,self.cfg,stamp),(True,None))
        plan=a.next_proactive(self.db,self.cfg,self.now)
        self.assertEqual(plan['pending']['at'],stamp-600+90*60)
    def test_agent_counts_actual_queued_contacts_and_preserves_no_reply_pause(self):
        self.cfg['proactive_mode']='agent';key=self.job()
        with patch.object(a.time,'time',return_value=self.now.timestamp()):a.emit(self.db,key,[{'type':'text','text':'今天那个梗越想越好笑。'}],'proactive')
        a.record_job(self.db,key,'done',{'reply_queued':True})
        self.assertEqual(a.contact_allowed(self.db,self.cfg,self.now.timestamp()+3*3600)[1],'awaiting_owner_reply')
    def test_agent_decision_validates_send_silence_and_delay(self):
        self.cfg['proactive_mode']='agent'
        from app import astrbot_client as ac
        good={'send':False,'text':'','reason':'暂时没有自然话题','next_check_minutes':90}
        with patch.object(ac,'chat',return_value=[{'type':'text','text':json.dumps(good)}]) as chat:
            self.assertEqual(a.decide_contact(self.db,'id',DEST,self.cfg),good)
            self.assertEqual(chat.call_args.args[0],DEST['session_id'])
        for bad in [{**good,'send':'false'},{**good,'next_check_minutes':0},{**good,'text':'不发却有文字'},{**good,'send':True,'text':'a'*121}]:
            with patch.object(ac,'chat',return_value=[{'type':'text','text':json.dumps(bad)}]):
                with self.assertRaises(ValueError):a.decide_contact(self.db,'id',DEST,self.cfg)
    def test_agent_check_budget_stops_without_forcing_a_message(self):
        self.cfg['proactive_mode']='agent';stamp=self.now.timestamp()
        for i in range(8):self.db.execute('INSERT INTO autonomy_jobs VALUES(?,?,?,?,?,?,?,?)',(str(i),'proactive','2026-10-24',0,stamp-3600-i,stamp+100,'deferred','{}'))
        self.db.commit()
        self.assertNotIn('pending',a.next_proactive(self.db,self.cfg,self.now))
    def test_random_target_and_pending_time_survive_another_tick(self):
        a.tick(self.db,self.now);first=a.read_plan(self.db,'proactive');a.tick(self.db,self.now)
        self.assertEqual(a.read_plan(self.db,'proactive'),first)
        self.assertIn(first['target'],[2,3]);self.assertGreater(first['pending']['at'],self.now.timestamp())
    def test_due_job_reserved_once_before_background_call(self):
        self.due();result=a.tick(self.db,self.now)
        self.assertEqual(result['state'],'started')
        aw.submit_background.assert_called_once()
        a.tick(self.db,self.now)
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_jobs').fetchone()[0],1)
        self.assertEqual(json.loads(self.db.execute('SELECT result FROM autonomy_jobs').fetchone()[0])['destination'],DEST)
    def test_unanswered_previous_contact_does_not_chase(self):
        self.db.execute('INSERT INTO autonomy_jobs VALUES(?,?,?,?,?,?,?,?)',('old','proactive','2026-10-23',0,self.now.timestamp()-4*3600,self.now.timestamp()+3600,'done','{}'));self.db.commit()
        self.assertEqual(a.contact_allowed(self.db,self.cfg,self.now.timestamp()),(False,'awaiting_owner_reply'))
    def test_incoming_owner_reply_releases_pause_but_recent_chat_is_respected(self):
        stamp=self.now.timestamp()
        self.db.execute('INSERT INTO autonomy_jobs VALUES(?,?,?,?,?,?,?,?)',('old','proactive','2026-10-23',0,stamp-4*3600,stamp+3600,'done','{}'))
        self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',('reply',OWNER,stamp-1800,json.dumps({'transport':'sdk','direction':'incoming','association_received_at':stamp-1800})));self.db.commit()
        self.assertTrue(a.contact_allowed(self.db,self.cfg,stamp)[0])
        self.assertEqual(a.contact_allowed(self.db,self.cfg,stamp-1200)[1],'recent_conversation')
    def test_outside_contact_window_does_not_schedule_catchup(self):
        result=a.tick(self.db,datetime(2026,10,24,23,0,tzinfo=a.TZ))
        self.assertEqual(result['state'],'waiting');aw.submit_background.assert_not_called()
    def test_owner_task_has_priority_and_does_not_consume_due_job(self):
        self.due();self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',('incoming',OWNER,1,'{}'));self.db.commit()
        self.assertEqual(a.tick(self.db,self.now)['state'],'busy')
        self.assertIsNotNone(a.read_plan(self.db,'proactive').get('pending'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_jobs').fetchone()[0],0)
    def test_legacy_sdk_cannot_consume_or_send_a_work_card_job(self):
        self.cfg.update(browse_enabled=True,proactive_enabled=False);self.due('browse')
        with patch('app.sdk_sender.health',return_value={'online':True}):
            self.assertEqual(a.tick(self.db,self.now)['state'],'sdk_update_required')
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_jobs').fetchone()[0],0)
    def test_provider_change_keeps_job_unexecuted(self):
        self.due()
        with patch('app.event_dispatch.target_config',return_value={'provider':'codex'}):
            self.assertEqual(a.tick(self.db,self.now)['state'],'requires_astrbot')
        aw.submit_background.assert_not_called()
    def job(self,kind='proactive'):
        stamp=self.now.timestamp();key='f'*32
        self.db.execute('INSERT INTO autonomy_jobs VALUES(?,?,?,?,?,?,?,?)',(key,kind,'2026-10-24',0,stamp-10,stamp+3600,'attempted',json.dumps({'destination':DEST})));self.db.commit();return key
    def test_emit_is_atomic_ordered_and_not_a_fake_owner_message(self):
        self.cfg['browse_enabled']=True;key=self.job('browse')
        with patch.object(a.time,'time',return_value=self.now.timestamp()):
            a.emit(self.db,key,[{'type':'text','text':'最后那个回头真没绷住。'}],'browse',{'itemId':'7698772565188654321','title':'真实作品'})
        self.assertEqual(self.db.execute('SELECT kind FROM reply_outbox ORDER BY seq').fetchall(),[('share',),('text',)])
        self.assertFalse(batch.allowed(self.db,key+':batch:1'))
        raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(key,)).fetchone()[0]);self.assertEqual(raw['origin'],'local_schedule');self.assertNotIn('transport',raw)
        with self.db:self.db.execute("UPDATE reply_outbox SET status='server_accepted' WHERE kind='share'")
        self.assertTrue(batch.allowed(self.db,key+':batch:1'))
        with self.assertRaises(ValueError),patch.object(a.time,'time',return_value=self.now.timestamp()):
            a.emit(self.db,key,[{'type':'text','text':'不应重复'}],'browse',{'itemId':'7698772565188654321'})
    def test_disabled_switch_expires_only_unsent_scheduled_parts(self):
        key=self.job()
        with patch.object(a.time,'time',return_value=self.now.timestamp()):a.emit(self.db,key,[{'type':'text','text':'你好'}],'proactive')
        self.cfg['proactive_enabled']=False
        with self.db:a.expire_outbound(self.db,self.now.timestamp())
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'expired')
    def test_changed_destination_cannot_emit_old_model_output(self):
        key=self.job()
        with patch('app.event_dispatch.target_config',return_value={'provider':'astrbot','astrbot':{'session_id':'other'}}),patch.object(a.time,'time',return_value=self.now.timestamp()):
            with self.assertRaises(ValueError):a.emit(self.db,key,[{'type':'text','text':'不应发出'}],'proactive')
        self.assertEqual(self.db.execute('SELECT count(*) FROM reply_outbox').fetchone()[0],0)
    def test_activity_never_calls_public_comment(self):
        import inspect
        from app import autonomy_video
        self.assertNotIn('shared_comments.enqueue',inspect.getsource(autonomy_video))
    def test_new_owner_message_cancels_proactive_generation(self):
        key=self.job()
        self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',('new-owner',OWNER,self.now.timestamp(),json.dumps({'transport':'sdk','direction':'incoming'})));self.db.commit()
        with patch.object(a.time,'time',return_value=self.now.timestamp()):
            with self.assertRaises(ValueError):a.emit(self.db,key,[{'type':'text','text':'不应打断'}],'proactive')
        self.assertEqual(self.db.execute('SELECT count(*) FROM reply_outbox').fetchone()[0],0)
    def test_real_outbound_order_and_uncertain_share_is_not_replayed(self):
        import tempfile
        from pathlib import Path
        from app import local_runtime as lr,sdk_sender
        self.cfg['browse_enabled']=True;key=self.job('browse')
        with patch.object(a.time,'time',return_value=self.now.timestamp()):
            a.emit(self.db,key,[{'type':'text','text':'这个结尾真没绷住。'}],'browse',{'itemId':'7698772565188654321'})
        with tempfile.TemporaryDirectory(dir=a.ROOT) as directory,patch.object(lr,'ROOT',Path(directory)),patch.object(lr,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}),patch.object(a.time,'time',return_value=self.now.timestamp()),patch.object(sdk_sender,'health',return_value={'online':True,'uid':'__ACCOUNT_UID__'}),patch.object(sdk_sender,'post',return_value={'result':'outcome_uncertain'}) as post,patch.object(sdk_sender,'send',return_value={'result':'server_accepted'}) as send:
            self.assertTrue(lr.outbound_step(self.db,OWNER)['share']);post.assert_called_once()
            self.assertEqual(lr.outbound_step(self.db,OWNER)['state'],'server_accepted');send.assert_called_once()
            self.assertEqual(lr.outbound_step(self.db,OWNER)['state'],'idle');post.assert_called_once()

if __name__=='__main__':unittest.main()
