import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import local_runtime as lr
from app.live_service import LiveStore
from app.sdk_receiver import OWNER

class LocalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=lr.ROOT);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.patch=patch.object(lr,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
        self.store=LiveStore(':memory:');self.addCleanup(self.store.db.close);self.db=self.store.db
    def queue(self):
        self.db.execute("INSERT INTO reply_outbox(id,task_id,owner,text,status) VALUES('reply','task',?,'hello','pending')",(OWNER,));self.db.commit()
    def health(self):return patch('app.sdk_sender.health',return_value={'online':True,'uid':'__ACCOUNT_UID__'})
    def test_reply_sent_without_page_or_browser(self):
        self.queue()
        with self.health(),patch.object(lr,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}),patch('app.sdk_sender.send',return_value={'result':'server_accepted'}) as send:
            result=lr.outbound_step(self.db,OWNER)
        self.assertEqual(result['state'],'server_accepted');send.assert_called_once_with('hello')
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'server_accepted')
    def test_uncertain_send_never_retried(self):
        self.queue()
        with self.health(),patch.object(lr,'dm_policy',return_value={'enabled':True,'retry_rejected':True,'min_send_gap_seconds':0}),patch('app.sdk_sender.send',return_value={'result':'outcome_uncertain'}) as send:
            lr.outbound_step(self.db,OWNER);lr.outbound_step(self.db,OWNER)
        self.assertEqual(send.call_count,1)
        self.assertEqual(self.db.execute('SELECT status,characters FROM outbound_attempts').fetchone(),('outcome_uncertain',5))
    def test_offline_keeps_reply_pending(self):
        self.queue()
        with patch('app.sdk_sender.health',return_value=None):self.assertEqual(lr.outbound_step(self.db,OWNER)['state'],'sdk_offline')
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'pending')
    def test_explicit_rejection_delayed_once(self):
        self.queue()
        policy={'enabled':True,'retry_rejected':True,'retry_max':1,'retry_delay_seconds':180,'min_send_gap_seconds':0}
        with self.health(),patch.object(lr,'dm_policy',return_value=policy),patch('app.sdk_sender.send',return_value={'result':'platform_not_accepted'}) as send:
            lr.outbound_step(self.db,OWNER);lr.outbound_step(self.db,OWNER)
        self.assertEqual(send.call_count,1)
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'pending')
    def test_voice_text_enters_packet_once(self):
        message={'kind':'audio','transport':'sdk','association_policy':'media_10s_v1'}
        self.db.execute("INSERT INTO live_tasks VALUES('voice',?,0,?,'media_pending',NULL)",(OWNER,json.dumps(message)));self.db.commit()
        record={'task_id':'voice','kind':'audio','files':[],'status':'saved','transcript':{'status':'transcribed','text':'你好呀'},'manifest_path':str(self.root/'manifest.json')}
        with patch('app.sdk_media.capture',return_value=record) as capture:
            lr.media_step(self.db,OWNER)
        row=self.db.execute("SELECT raw_json,status FROM live_tasks WHERE id='voice'").fetchone()
        raw=json.loads(row[0]);self.assertEqual(row[1],'resource_saved')
        self.assertEqual(raw['original_kind'],'audio');self.assertEqual(raw['kind'],'voice_transcript');self.assertIn('你好呀',raw['raw_text'])
        from app.media_pipeline import dispatch_standalone_shared
        dispatch_standalone_shared(self.db,OWNER);dispatch_standalone_shared(self.db,OWNER)
        self.assertEqual(self.db.execute("SELECT status FROM live_tasks WHERE id='voice'").fetchone()[0],'pending')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM media_records').fetchone()[0],1)
        capture.assert_called_once()

    def test_gallery_processed_without_page(self):
        self.db.execute("INSERT INTO live_tasks VALUES('media',?,0,?,'media_pending',NULL)",(OWNER,json.dumps({'kind':'video_card','transport':'sdk'})));self.db.commit()
        record={'task_id':'media','kind':'video_card','files':[],'status':'saved','media_type':'image_gallery','video':{'video_id':'123'},'manifest_path':str(self.root/'manifest.json')}
        with patch('app.sdk_media.capture',return_value=record) as capture,patch.object(lr,'LocalReadContext',return_value=object()),patch('app.douyin_comments.fetch',return_value={'status':'done','items':[]}):
            lr.media_step(self.db,OWNER)
        self.assertIsNone(capture.call_args.args[0])
        result=json.loads(self.db.execute('SELECT record_json FROM media_records').fetchone()[0])
        self.assertEqual(result['shared_video_analysis']['status'],'not_applicable')

if __name__=='__main__':unittest.main()
