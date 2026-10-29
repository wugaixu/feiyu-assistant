import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from concurrent.futures import Future
from app import shared_video as shared
from app.live_service import LiveStore
from app.event_dispatch import dispatch_one
from app.media_pipeline import dispatch_standalone_shared


class SharedVideoTests(unittest.TestCase):
    def test_old_image_not_replayed_by_new_policy(self):
        store=LiveStore(':memory:');db=store.db
        with db:
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('old','owner',1,json.dumps({'kind':'image'}),'resource_saved'))
            db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('old','owner',1,'image',json.dumps({'kind':'image'})))
        dispatch_standalone_shared(db,'owner')
        self.assertEqual(db.execute('SELECT status FROM live_tasks').fetchone()[0],'resource_saved')
        db.close()

    def test_delayed_platform_message_still_gets_local_grace_period(self):
        store=LiveStore(':memory:');db=store.db
        with db:
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('new','owner',1,json.dumps({'kind':'image','association_policy':'media_10s_v1','association_received_at':98}),'resource_saved'))
            db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('new','owner',1,'image',json.dumps({'kind':'image'})))
        with patch('app.media_pipeline.time.time',return_value=100):dispatch_standalone_shared(db,'owner')
        self.assertEqual(db.execute('SELECT status FROM live_tasks').fetchone()[0],'resource_saved')
        db.close()

    def test_all_media_wait_ten_seconds_then_dispatch_once(self):
        for kind in ('image','audio','video','video_card'):
            with self.subTest(kind=kind):
                store=LiveStore(':memory:');db=store.db
                with db:
                    db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('media','owner',95,json.dumps({'kind':kind,'association_policy':'media_10s_v1'}),'resource_saved'))
                    db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('media','owner',95,kind,json.dumps({'status':'saved','kind':kind,'shared_video_analysis':{'status':'done'}})))
                with patch('app.media_pipeline.time.time',return_value=100):dispatch_standalone_shared(db,'owner')
                self.assertEqual(db.execute('SELECT status FROM live_tasks').fetchone()[0],'resource_saved')
                with patch('app.media_pipeline.time.time',return_value=106):
                    dispatch_standalone_shared(db,'owner');dispatch_standalone_shared(db,'owner')
                raw,status=db.execute('SELECT raw_json,status FROM live_tasks').fetchone()
                self.assertEqual(status,'pending');self.assertEqual(json.loads(raw)['original_kind'],kind)
                db.close()

    def test_following_text_keeps_all_media_as_attachments(self):
        for kind in ('image','audio','video','video_card'):
            store=LiveStore(':memory:');db=store.db
            with db:
                db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('media','owner',1,json.dumps({'kind':kind,'association_policy':'media_10s_v1'}),'resource_saved'))
                db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('text','owner',2,json.dumps({'kind':'text'}),'pending'))
                db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('media','owner',1,kind,'{}'))
            dispatch_standalone_shared(db,'owner')
            self.assertEqual(db.execute("SELECT status FROM live_tasks WHERE id='media'").fetchone()[0],'resource_saved')
            db.close()

    def test_text_withdraws_unqueued_standalone_video(self):
        store=LiveStore(self.root/'race.sqlite3');self.addCleanup(store.db.close);db=store.db
        with db:
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('text','owner',2,json.dumps({'kind':'text','raw_text':'看看'}),'pending'))
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('video','owner',1,json.dumps({'kind':'shared_video','raw_text':'视频'}),'pending'))
            db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('video','owner',1,'video_card',json.dumps({'status':'saved','shared_video_analysis':{'status':'done'}})))
        runner=MagicMock();runner.return_value.returncode=0
        thread='11111111-1111-1111-1111-111111111111'
        runner.return_value.stdout=f'Queued message {thread} for thread {thread}.\n'
        with patch('app.event_dispatch.thread_id',return_value=thread):
            dispatch_one(db,runner=runner,executable='codex.exe')
            self.assertIsNone(dispatch_one(db,runner=runner,executable='codex.exe'))
        self.assertEqual(runner.call_count,1)
        # The burst keeps the earliest task as the delivery and folds the text in.
        self.assertEqual(db.execute('SELECT attached_to FROM media_records').fetchone()[0],'video')
        self.assertEqual(db.execute("SELECT status FROM live_tasks WHERE id='text'").fetchone()[0],'merged')
        self.assertEqual(db.execute("SELECT status FROM live_tasks WHERE id='video'").fetchone()[0],'pending')

    def test_stale_standalone_selection_cannot_send_attached_video(self):
        store=LiveStore(self.root/'stale.sqlite3');self.addCleanup(store.db.close);db=store.db
        with db:
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('video','owner',1,json.dumps({'kind':'shared_video'}),'pending'))
            db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,?)',('video','owner',1,'video_card','{}','text'))
        runner=MagicMock()
        self.assertIsNone(dispatch_one(db,runner=runner,executable='codex.exe'))
        runner.assert_not_called()

    def setUp(self):
        target=patch('app.event_dispatch.target_config',return_value={'provider':'codex','mode':'explicit','thread_id':'11111111-1111-1111-1111-111111111111'})
        target.start();self.addCleanup(target.stop)
        self.temp=tempfile.TemporaryDirectory(dir=shared.ROOT);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def test_identity_needs_visible_author_and_work_id(self):
        page=MagicMock();page.evaluate.return_value=False
        with patch('app.upstream_observation.observe',return_value={'video':{'id':'7699453766428715642','author':'林的科技'}}):
            self.assertIsNone(shared.identify(page,{'card_author':'林的科技'}))
            page.evaluate.return_value=True
            self.assertEqual(shared.identify(page,{'card_author':'林的科技'})['video_id'],'7699453766428715642')
    def test_comments_unavailable_without_attribution(self):
        record={}
        with patch.object(shared,'identify',return_value=None):
            shared.enrich(None,None,record,{})
        self.assertEqual(record['comments']['status'],'unavailable')
    def test_recorded_webm_duration_is_passed_to_frame_job(self):
        source=self.root/'video.webm';source.write_bytes(b'test')
        pool=MagicMock();future=Future();pool.submit.return_value=future
        with patch.object(shared,'_pool',pool),patch.object(shared,'_jobs',{}):
            shared.start_frames({'task_id':'a','files':[{'path':str(source),'status':'saved','decode_verified':True}],'playback_recording':{'duration':20.4}})
            self.assertEqual(pool.submit.call_args.args[-2],20.4)
            self.assertEqual(shared.poll_frames('a')['status'],'processing')
    def test_dispatch_waits_for_shared_video_frames(self):
        store=LiveStore(self.root/'test.sqlite3');self.addCleanup(store.db.close);db=store.db
        with db:
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('a'*32,'owner',1,json.dumps({'kind':'text','raw_text':'看看这个视频'}),'pending'))
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('b'*32,'owner',0,json.dumps({'kind':'shared_video'}),'resource_saved'))
            db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('b'*32,'owner',0,'video_card',json.dumps({'status':'saved','shared_video_analysis':{'status':'processing'}})))
        runner=MagicMock()
        self.assertIsNone(dispatch_one(db,runner=runner,executable='codex.exe'))
        runner.assert_not_called()
    def test_standalone_video_uses_same_task_once(self):
        store=LiveStore(self.root/'test.sqlite3');self.addCleanup(store.db.close);db=store.db;task='c'*32
        with db:
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',(task,'owner',1,json.dumps({'kind':'video_card','raw_text':'作者'}),'resource_saved'))
            db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',(task,'owner',1,'video_card',json.dumps({'status':'saved','shared_video_analysis':{'status':'done'}})))
        dispatch_standalone_shared(db,'owner');dispatch_standalone_shared(db,'owner')
        raw,status=db.execute('SELECT raw_json,status FROM live_tasks WHERE id=?',(task,)).fetchone()
        self.assertEqual(status,'pending');self.assertEqual(json.loads(raw)['kind'],'shared_video')
        self.assertEqual(db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],1)
