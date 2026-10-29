import tempfile,unittest,sqlite3
from pathlib import Path
from unittest.mock import patch
from app.workflow_health import assess,snapshot,collect

ROOT=Path(r'__RUNTIME_ROOT__')

class WorkflowHealthTests(unittest.TestCase):
    def setUp(self):
        self.wf={'sdk':{'online':True,'uid':'__ACCOUNT_UID__'},'receiver':{'state':'listening','age_seconds':2}}
        self.data={'ok':True,'counts':{},'media':[],'comments':[],'undispatched':0,'dispatch_uncertain':0,'reply_pending':0}
        self.deps={'douk':True,'ffmpeg':True,'sensevoice':True}
        self.policies={'dm':{'enabled':True,'min_send_gap_seconds':10},'shared_comment':{'enabled':True}}
        self.agent={'available':True,'title':'DSH测试会话','running':False}
        self.storage={'cleanup_enabled':True,'retention_days':7,'interval_hours':24,'next_run_at':2000}

    def stages(self):
        return {s['id']:s for s in assess(self.wf,{'provider':'dsh'},self.policies,self.data,self.deps,self.agent,self.storage,now=1000)}

    def test_idle_on_demand_modules_are_healthy_without_processes(self):
        stages=self.stages();self.assertEqual(len(stages),9)
        self.assertEqual(stages['sdk']['state'],'ready')
        for stage in ('download','visual','audio','agent','reply','comment','cleanup'):self.assertEqual(stages[stage]['state'],'idle')

    def test_stale_receiver_never_claims_pipeline_healthy(self):
        self.wf['receiver']['age_seconds']=30
        stages=self.stages();self.assertEqual(stages['inbox']['state'],'error')
        self.assertEqual(stages['audio']['state'],'blocked')

    def test_account_identity_mismatch_blocks_replies(self):
        self.wf['sdk']['uid']='wrong'
        self.assertEqual(self.stages()['sdk']['state'],'error');self.assertEqual(self.stages()['reply']['state'],'blocked')

    def test_processing_media_and_speech_are_separate(self):
        self.data['counts']['media_pending']=2
        self.data['media']=[{'record':{'shared_video_analysis':{'status':'done'},'audio_analysis':{'status':'processing'}}}]
        stages=self.stages();self.assertEqual(stages['download']['state'],'busy');self.assertEqual(stages['visual']['state'],'idle');self.assertEqual(stages['audio']['state'],'busy')

    def test_no_audio_is_a_result_not_a_service_fault(self):
        self.data['media']=[{'record':{'audio_analysis':{'status':'no_audio'}}}]
        self.assertEqual(self.stages()['audio']['state'],'idle')

    def test_missing_model_or_decoder_is_reported(self):
        self.deps['sensevoice']=False;self.assertEqual(self.stages()['audio']['state'],'error')
        self.deps['sensevoice']=True;self.deps['ffmpeg']=False
        self.assertEqual(self.stages()['audio']['state'],'error');self.assertEqual(self.stages()['visual']['state'],'error')

    def test_agent_offline_not_misreported_as_sdk_offline(self):
        self.agent['available']=False
        self.assertEqual(self.stages()['agent']['state'],'error');self.assertEqual(self.stages()['sdk']['state'],'ready')

    def test_active_session_is_not_claimed_to_be_processing_project_task(self):
        self.agent['running']=True
        stage=self.stages()['agent']
        self.assertEqual(stage['state'],'busy');self.assertIn('没有本项目已领取任务',stage['detail'])

    def test_old_comment_attempt_is_warning_not_perpetual_running(self):
        self.data['comments']=[{'status':'attempted','created':0}]
        self.assertEqual(self.stages()['comment']['state'],'warn')

    def test_disabled_switches_and_pending_receipts_are_distinct(self):
        self.policies['dm']['enabled']=False;self.policies['shared_comment']['enabled']=False
        stages=self.stages();self.assertEqual(stages['reply']['state'],'off');self.assertEqual(stages['comment']['state'],'off')
        self.policies['dm']['enabled']=True;self.wf['outbound_alerts']=3
        self.assertEqual(self.stages()['reply']['state'],'idle')

    def test_database_unreadable_is_not_all_green(self):
        self.data['ok']=False
        self.assertEqual(self.stages()['inbox']['state'],'warn')
        self.assertEqual(self.stages()['audio']['state'],'unknown')

    def test_comment_receipt_note_and_owner_observation_are_separate(self):
        comment={'status':'accepted_unverified','created':900,'review':{}}
        self.data['comments']=[comment]
        self.assertEqual(self.stages()['comment']['state'],'idle')
        comment['review']={'visibility':{'value':'confirmed'},'mention':{'value':'missing'}}
        self.assertEqual(self.stages()['comment']['state'],'idle')
        comment['review']['mention']['value']='confirmed'
        self.assertEqual(self.stages()['comment']['state'],'idle')
        self.assertEqual(comment['status'],'accepted_unverified')

    def test_definite_failure_is_not_muted_as_unverified(self):
        self.wf['reply_assessment']={'state':'warn','detail':'failed','metric':'error'}
        self.assertEqual(self.stages()['reply']['state'],'warn')
        self.data['comments']=[{'status':'platform_rejected','created':900}]
        self.assertEqual(self.stages()['comment']['state'],'warn')

    def test_cleanup_error_does_not_trigger_deletion(self):
        self.storage['last_cleanup']={'state':'error'}
        self.assertEqual(self.stages()['cleanup']['state'],'warn')

    def test_missing_database_is_read_only(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            p=Path(folder)/'missing.sqlite3';self.assertFalse(snapshot(p)['ok']);self.assertFalse(p.exists())

class ChannelMonitoringTests(unittest.TestCase):
    setUp=WorkflowHealthTests.setUp
    stages=WorkflowHealthTests.stages

    def test_qq_and_whale_do_not_depend_on_douyin(self):
        self.wf['sdk']['online']=False
        for channel in ('qq','whale'):
            self.data['reply_routes']={channel:2}
            self.data['reply_pending']=2
            self.wf['channel_ready']={channel:True}
            self.assertEqual(self.stages()['reply']['state'],'busy')
            self.assertEqual(self.stages()['sdk']['state'],'error')

    def test_partial_outage_and_unknown_address_are_not_all_green(self):
        self.data['reply_routes']={'douyin':1,'qq':2}
        self.wf['channel_ready']={'qq':False}
        stage=self.stages()['reply']
        self.assertEqual(stage['state'],'warn');self.assertIn('QQ',stage['detail'])
        self.data['reply_routes']={'unknown':1}
        self.assertEqual(self.stages()['reply']['state'],'blocked')

    def test_idle_uses_actual_default_channel(self):
        self.wf['sdk']['online']=False
        self.wf['channel_ready']={'whale':True}
        self.data['active_channel']='whale'
        self.assertEqual(self.stages()['reply']['state'],'idle')

    def test_snapshot_routes_and_malformed_comment_are_read_only(self):
        import json
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            path=Path(folder)/'state.sqlite3'
            db=sqlite3.connect(path)
            db.executescript("CREATE TABLE live_tasks(id TEXT,status TEXT,raw_json TEXT); CREATE TABLE reply_outbox(task_id TEXT,status TEXT); CREATE TABLE shared_comment_outbox(status TEXT,created REAL,video_id TEXT,detail TEXT); CREATE TABLE channel_current(id INTEGER,channel TEXT);")
            db.execute('INSERT INTO live_tasks VALUES(?,?,?)',('qq','done',json.dumps({'transport':'qq','reply_device':'qq:123456789:987654321'})))
            db.execute("INSERT INTO reply_outbox VALUES('qq','pending')")
            db.execute("INSERT INTO shared_comment_outbox VALUES('failed',0,'work','[]')")
            db.execute("INSERT INTO channel_current VALUES(1,'whale')")
            db.commit();db.close();before=path.read_bytes()
            state=snapshot(path)
            self.assertTrue(state['ok']);self.assertEqual(state['reply_routes'],{'qq':1})
            self.assertEqual(state['active_channel'],'whale')
            self.assertEqual(path.read_bytes(),before)

if __name__=='__main__':unittest.main()
