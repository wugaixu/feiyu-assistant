import json,sqlite3,time,unittest
from pathlib import Path
from unittest.mock import patch
import test_visual_delivery as fixtures
from app import task_context,task_materials,local_vision,astrbot_worker,reply_batch,shared_comments
from app.live_service import LiveStore
from app.sdk_receiver import OWNER


class UnifiedMaterialTests(unittest.TestCase):
    image=fixtures.VisualDeliveryTests.image
    def setUp(self):
        fixtures.VisualDeliveryTests.setUp(self)
        self.db=LiveStore(':memory:').db;self.addCleanup(self.db.close)
        self.task='a'*32
        self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',(self.task,OWNER,time.time(),json.dumps({'kind':'text','raw_text':'看图'}),'processing'))
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT,status TEXT,detail TEXT)')
        self.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',(self.task,'queued',json.dumps({'provider':'astrbot','session_id':'session'})))
        self.db.commit()

    def packet(self,record):
        return task_context.write_packet(self.task,OWNER,{'kind':'text','raw_text':'看图'},[record])

    def test_identical_request_block_and_untruncated_materials(self):
        record=self.image();record['document_analysis']={'status':'done','text':'甲'*19000,'truncated':False}
        context=self.packet(record)
        request=task_context.request(self.task,OWNER,{'kind':'text','raw_text':'看图'},[record],write=False)
        astro=astrbot_worker.dm_prompt({'kind':'text','raw_text':'看图'},[record],task_id=self.task)
        self.assertTrue(astro.endswith(request))
        token=local_vision.permit(self.db,self.task,'session',[record])
        # CLI and plugin share exactly the same frozen material snapshot and pagination.
        catalog=task_materials.for_task(self.db,self.task)
        text='';offset=0
        while True:
            page=task_materials.read_text(catalog['texts'],context,offset)
            self.assertEqual(page,local_vision.read_text(self.db,token,context,offset))
            text+=page['text']
            if page['next_offset'] is None:break
            offset=page['next_offset']
        self.assertIn('甲'*19000,text)
        with self.assertRaises(ValueError):local_vision.read_text(self.db,token,context.with_name('data.json'))
        context.write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):local_vision.read_text(self.db,token,context)
        with self.assertRaises(ValueError):task_materials.read_text(catalog['texts'],context)

    def test_all_three_completion_states_and_batches_match(self):
        for provider in ('codex','dsh','astrbot'):
            with self.subTest(provider=provider):
                self.db.execute("UPDATE live_tasks SET status='processing'")
                self.db.execute('UPDATE chat_dispatch SET status=?,detail=?',('queued',json.dumps({'provider':provider})))
                self.db.execute('DELETE FROM reply_outbox');self.db.commit()
                reply_batch.finish(self.db,self.task,[{'type':'text','text':'回复'}])
                self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'done')
                self.assertEqual(self.db.execute('SELECT status FROM chat_dispatch').fetchone()[0],'processed')

    def test_shared_rules_are_readable_frozen_and_not_repeated_in_envelope(self):
        record=self.image();context=self.packet(record)
        envelope=task_context.request(self.task,OWNER,{'kind':'text','raw_text':'看图'},[record],write=False)
        payload=json.loads(envelope.splitlines()[1])
        rules=Path(payload['instructions_file'])
        self.assertEqual(rules.read_text(encoding='utf-8'),task_context.RULES_FILE.read_text(encoding='utf-8'))
        self.assertNotIn('公开评论',envelope)
        self.assertNotIn('visual_files',payload)  # Paths are listed once, in context.md.
        self.assertLess(len(envelope),1000)
        token=local_vision.permit(self.db,self.task,'session',[record])
        catalog=task_materials.for_task(self.db,self.task)
        page=local_vision.read_text(self.db,token,rules)
        self.assertEqual(page,task_materials.read_text(catalog['texts'],rules))
        self.assertIsNone(page['next_offset'])
        self.assertIn('先登记草稿',page['text'])
        # A caller cannot replace trusted instructions after the permit freezes them.
        rules.write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):local_vision.read_text(self.db,token,rules)

    def test_reconcile_only_repairs_completed_metadata_without_creating_replies(self):
        from app.task_lifecycle import reconcile_completed
        reply_batch.finish(self.db,self.task,[{'type':'text','text':'完成'}])
        self.db.execute("UPDATE chat_dispatch SET status='queued'");self.db.commit()
        before=self.db.execute('SELECT count(*) FROM reply_outbox').fetchone()[0]
        self.assertEqual(reconcile_completed(self.db),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM reply_outbox').fetchone()[0],before)
        self.db.execute("UPDATE live_tasks SET status='pending'");self.db.execute("UPDATE chat_dispatch SET status='queued'");self.db.commit()
        self.assertEqual(reconcile_completed(self.db),0)


class CommonCommentTests(unittest.TestCase):
    def setUp(self):
        self.db=LiveStore(':memory:').db;self.addCleanup(self.db.close)
        self.video='7697598283775355313';self.task='task'
        self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',(self.task,OWNER,time.time(),json.dumps({'transport':'sdk','association_received_at':time.time()}),'processing'))
        record={'kind':'video_card','source':'sdk','received_at':time.time(),'status':'saved','files':[{'path':'video.mp4'}],
                'video':{'video_id':self.video,'source':'sdk_shared_work','description':'滑稽摔倒'},'shared_video_analysis':{'status':'done'}}
        self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,?)',('media',OWNER,time.time(),'video_card',json.dumps(record),self.task));self.db.commit()
        p=patch.object(shared_comments,'policy',return_value={'enabled':True,'enabled_since':1,'daily_limit':10});p.start();self.addCleanup(p.stop)

    def stage(self,**extra):
        return shared_comments.enqueue(self.db,self.task,self.video,{'text':'这个反差挺有意思。','evidence':'本轮画面中确实有反差。',**extra})

    def test_comment_cannot_publish_before_reply_and_failure_cancels_it(self):
        self.assertEqual(self.stage()['status'],'waiting_for_reply')
        self.assertIsNone(shared_comments._step(self.db,object()))
        reply_batch.finish(self.db,self.task,[{'type':'text','text':'暂未完成'}],status='failed')
        self.assertEqual(self.db.execute('SELECT status FROM shared_comment_outbox').fetchone()[0],'cancelled')

    def test_success_releases_draft_and_no_post_completion_draft_can_be_added(self):
        self.stage();reply_batch.finish(self.db,self.task,[{'type':'text','text':'私信照常回复'}])
        self.assertEqual(self.db.execute('SELECT status FROM shared_comment_outbox').fetchone()[0],'pending')
        with self.assertRaises(ValueError):self.stage()


if __name__=='__main__':unittest.main()
