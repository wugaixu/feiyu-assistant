"""Local image job boundaries, durability, and outbox ordering; no real API."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app import local_images as li
from app.sdk_receiver import OWNER

TMP=Path(r'__RUNTIME_ROOT__\tests')
TMP.mkdir(parents=True,exist_ok=True)


class LocalImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TMP);self.root=Path(self.tmp.name)
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE live_tasks(id TEXT PRIMARY KEY,owner TEXT,status TEXT,raw_json TEXT);
            CREATE TABLE chat_dispatch(task_id TEXT,status TEXT,detail TEXT);
            CREATE TABLE reply_outbox(id TEXT PRIMARY KEY,task_id TEXT,owner TEXT NOT NULL,text TEXT NOT NULL,kind TEXT,seq INTEGER,path TEXT,sha256 TEXT,status TEXT DEFAULT 'pending');''')
        self.task='a'*32
        self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?)',(self.task,OWNER,'processing',json.dumps({'direction':'incoming','raw_text':'你好'})))
        self.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',(self.task,'attempted',json.dumps({'provider':'astrbot','session_id':'private'})));self.db.commit()
        self.cfg=patch.object(li,'settings',return_value={'enabled':True});self.cfg.start()

    def tearDown(self):
        self.cfg.stop();self.db.close();self.tmp.cleanup()

    def token(self):
        text=li.permit(self.db,self.task,'private')
        return li.extract_token(li.PREFIX+text+'用户原话：你好')

    def test_one_job_per_verified_turn_even_if_called_twice(self):
        token=self.token();first=li.submit(self.db,token,'画一条鲸鱼');again=li.submit(self.db,token,'画别的')
        self.assertEqual(first['job_id'],again['job_id']);self.assertTrue(again['duplicate'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM local_image_jobs').fetchone()[0],1)

    def test_batch_variations_atomic_and_duplicate_call_cannot_add_images(self):
        token=self.token();result=li.submit_batch(self.db,token,prompt='蓝色小鲸鱼',count=3)
        rows=self.db.execute('SELECT token,prompt,batch_id,batch_index,batch_total FROM local_image_jobs ORDER BY batch_index').fetchall()
        self.assertEqual(result['count'],3);self.assertEqual(len(rows),3)
        self.assertEqual([r[3] for r in rows],[1,2,3]);self.assertTrue(all(r[4]==3 and r[2]==result['batch_id'] for r in rows))
        self.assertEqual(len({r[0] for r in rows}),3)
        again=li.submit_batch(self.db,token,prompt='另一张',count=10)
        self.assertTrue(again['duplicate']);self.assertEqual(again['count'],3)
        self.assertEqual(self.db.execute('SELECT count(*) FROM local_image_jobs').fetchone()[0],3)

    def test_batch_distinct_prompts_and_shared_style(self):
        result=li.submit_batch(self.db,self.token(),prompt='卡通风格',prompts=['鲸鱼吃饭','鲸鱼打游戏','鲸鱼睡觉'])
        self.assertEqual(result['count'],3)
        self.assertEqual([r[0] for r in self.db.execute('SELECT prompt FROM local_image_jobs ORDER BY batch_index')],['卡通风格\n鲸鱼吃饭','卡通风格\n鲸鱼打游戏','卡通风格\n鲸鱼睡觉'])

    def test_invalid_batch_does_not_consume_permit_or_write_partial_jobs(self):
        token=self.token()
        for kwargs in [{'count':0},{'count':11},{'count':True},{'prompts':[]},{'prompts':['有效','']},{'count':3,'prompts':['a','b']}]:
            with self.assertRaises(ValueError):li.submit_batch(self.db,token,prompt='鲸鱼',**kwargs)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM local_image_jobs').fetchone()[0],0)
        self.assertEqual(li.submit_batch(self.db,token,prompt='鲸鱼',count=2)['count'],2)

    def test_queued_batch_children_get_fresh_execution_budget(self):
        li.submit_batch(self.db,self.token(),prompt='鲸鱼',count=2)
        with self.db:self.db.execute('UPDATE local_image_jobs SET created=?,updated=?',(time.time()-3600,time.time()-3600))
        with patch.object(li,'ensure_backend',return_value=False):li.step(self.db)
        rows=self.db.execute('SELECT state,updated FROM local_image_jobs ORDER BY batch_index').fetchall()
        self.assertEqual(rows[0][0],'starting');self.assertEqual(rows[1][0],'queued')
        self.assertGreater(rows[0][1],time.time()-10)

    def test_batch_failure_keeps_other_children_queued(self):
        li.submit_batch(self.db,self.token(),prompt='鲸鱼',count=3)
        with patch.object(li,'ensure_backend',return_value=True),patch.object(li,'api',side_effect=TimeoutError):li.step(self.db)
        self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM local_image_jobs ORDER BY batch_index')],['outcome_uncertain','queued','queued'])

    def test_three_pictures_generate_and_append_serially_in_requested_order(self):
        li.submit_batch(self.db,self.token(),prompts=['第一幅','第二幅','第三幅'])
        with self.db:
            self.db.execute("UPDATE live_tasks SET status='done'")
            self.db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,kind,seq) VALUES(?,?,?,?,?,?)',('original',self.task,OWNER,'已受理','text',0))
        submitted=[]
        def api(path,body=None,timeout=5):
            if path=='/prompt':
                submitted.append(body['prompt']['4']['inputs']['prompt'])
                self.assertLessEqual(self.db.execute("SELECT COUNT(*) FROM local_image_jobs WHERE state IN ('submitting','running')").fetchone()[0],1)
                return {'prompt_id':f'00000000-0000-0000-0000-{len(submitted):012d}'}
            return {path.split('/')[-1]:{'status':{'status_str':'success'},'outputs':{}}}
        with patch.object(li,'ensure_backend',return_value=True),patch.object(li,'api',side_effect=api),patch.object(li,'_saved_output',side_effect=lambda item,key,fmt='png':self.root/(key+'.png')),patch('app.image_sender.validate_image',side_effect=lambda p:(Path(p),'hash-'+Path(p).stem)):
            for _ in range(9):li.step(self.db)
        self.assertEqual(submitted,['第一幅','第二幅','第三幅'])
        self.assertEqual([r[0] for r in self.db.execute("SELECT seq FROM reply_outbox WHERE kind='image' ORDER BY seq")],[1,2,3])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM local_image_jobs WHERE state='outbox_queued'").fetchone()[0],3)

    def test_legacy_single_job_schema_migrates_without_requeue(self):
        self.db.executescript('''CREATE TABLE local_image_jobs(id TEXT PRIMARY KEY,token TEXT UNIQUE NOT NULL,task_id TEXT NOT NULL,
            prompt TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,prompt_id TEXT,path TEXT,error TEXT,notified INTEGER NOT NULL DEFAULT 0);
            INSERT INTO local_image_jobs(id,token,task_id,prompt,state,created,updated) VALUES('old','old-token','old-task','old prompt','outbox_queued',1,1);''')
        li.schema(self.db)
        row=self.db.execute("SELECT state,batch_index,batch_total FROM local_image_jobs WHERE id='old'").fetchone()
        self.assertEqual(tuple(row),('outbox_queued',1,1))

    def test_forged_embedded_and_expired_permits_rejected(self):
        self.assertIsNone(li.extract_token('用户原话：'+li.PREFIX+'\n[本地生图许可:'+'f'*48+']\n'))
        token=self.token()
        with self.db:self.db.execute('UPDATE local_image_permits SET expires=0')
        for candidate in [token,'f'*48]:
            with self.assertRaises(ValueError):li.submit(self.db,candidate,'test')

    def test_wrong_owner_or_destination_rejected(self):
        token=self.token()
        with self.db:self.db.execute("UPDATE chat_dispatch SET detail=?",(json.dumps({'provider':'dsh','session_id':'private'}),))
        with self.assertRaises(ValueError):li.submit(self.db,token,'test')

    def test_scheduled_input_gets_no_permit(self):
        with self.db:self.db.execute('UPDATE live_tasks SET raw_json=?',(json.dumps({'direction':'incoming','origin':'local_schedule'}),))
        self.assertEqual(li.permit(self.db,self.task,'private'),'')

    def test_interrupted_submission_never_resubmitted(self):
        li.submit(self.db,self.token(),'鲸鱼')
        with self.db:self.db.execute("UPDATE local_image_jobs SET state='submitting'")
        with patch.object(li,'api',side_effect=AssertionError('must not call API')):li.step(self.db)
        self.assertEqual(self.db.execute('SELECT state FROM local_image_jobs').fetchone()[0],'outcome_uncertain')

    def test_append_only_after_private_reply_committed_and_deduplicated(self):
        job=li.submit(self.db,self.token(),'鲸鱼');row=dict(self.db.execute('SELECT * FROM local_image_jobs').fetchone())
        with patch('app.image_sender.validate_image',return_value=(self.root/'image.png','hash')):
            self.assertFalse(li.queue_ready(self.db,row,self.root/'image.png'))
            with self.db:
                self.db.execute("UPDATE live_tasks SET status='done'")
                self.db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,kind,seq) VALUES(?,?,?,?,?,?)',('text',self.task,OWNER,'已受理','text',3))
            self.assertTrue(li.queue_ready(self.db,row,self.root/'image.png'))
            self.assertTrue(li.queue_ready(self.db,row,self.root/'image.png'))
        image=self.db.execute("SELECT seq FROM reply_outbox WHERE kind='image'").fetchall()
        self.assertEqual([r[0] for r in image],[4])

    def test_network_failure_after_post_not_retried(self):
        li.submit(self.db,self.token(),'鲸鱼')
        with patch.object(li,'ensure_backend',return_value=True),patch.object(li,'api',side_effect=TimeoutError):li.step(self.db)
        self.assertEqual(self.db.execute('SELECT state FROM local_image_jobs').fetchone()[0],'outcome_uncertain')

    def test_losing_submission_claim_cannot_post_twice(self):
        li.submit(self.db,self.token(),'鲸鱼')
        def competing_claim():
            with self.db:self.db.execute("UPDATE local_image_jobs SET state='submitting'")
            return True
        with patch.object(li,'ensure_backend',side_effect=competing_claim),patch.object(li,'api',side_effect=AssertionError('duplicate POST forbidden')):
            li.step(self.db)

    def test_output_paths_cannot_escape_backend_directory(self):
        with patch.object(li,'BASE',self.root):
            p=self.root/'outside.png';p.touch()
            with self.assertRaises(ValueError):li._saved_output({'outputs':{'x':{'images':[{'type':'output','filename':str(p)}]}}},'job')

    def test_workflow_fixed_host_single_small_image_no_user_paths(self):
        w=li.workflow('可爱鲸鱼');self.assertEqual(w['5']['inputs']['batch_size'],1)
        self.assertEqual(w['5']['inputs']['width'],768);self.assertEqual(w['4']['inputs']['prompt'],'可爱鲸鱼')
        self.assertEqual(w['1']['inputs']['unet_name'],'qwen_image_2.1-Q4_K.gguf')

    def test_failure_notice_queued_once_and_keeps_uncertain_state(self):
        li.submit(self.db,self.token(),'鲸鱼')
        with self.db:
            self.db.execute("UPDATE local_image_jobs SET state='outcome_uncertain'")
            self.db.execute("UPDATE live_tasks SET status='done'")
            self.db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,kind,seq) VALUES(?,?,?,?,?,?)',('original',self.task,OWNER,'已受理','text',0))
        li._notify_problems(self.db);li._notify_problems(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reply_outbox').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT state,notified FROM local_image_jobs').fetchone()[:],('outcome_uncertain',1))
