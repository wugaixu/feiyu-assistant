import io,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app import queue_maintenance as qm,console_server as cs
from app.live_service import LiveStore
from app.sdk_receiver import OWNER


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.store=LiveStore(':memory:');self.db=self.store.db;self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO live_tasks VALUES('task',?,1,?,'processing',NULL)",(OWNER,json.dumps({'kind':'text','raw_text':'测试'})))
        self.db.execute('CREATE TABLE IF NOT EXISTS chat_dispatch(task_id TEXT PRIMARY KEY,status TEXT,detail TEXT)')
        self.db.execute("INSERT INTO chat_dispatch VALUES('task','queued','{}')")
        self.db.execute('CREATE TABLE IF NOT EXISTS outbound_attempts(id TEXT PRIMARY KEY,outbox_id TEXT,started REAL,finished REAL,status TEXT,detail TEXT)')
        self.db.commit()

    def send(self,key='reply',status='pending',seq=0,task='task'):
        self.db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,status,seq,detail) VALUES(?,?,?,?,?,?,?)',(key,task,OWNER,'回复',status,seq,'{"original":true}'));self.db.commit()

    def test_end_releases_processing_and_rejects_late_completion(self):
        self.send()
        qm.apply(self.db,'task','task','end','processing')
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'failed')
        self.assertEqual(self.db.execute('SELECT status FROM chat_dispatch').fetchone()[0],'cancelled')
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'cancelled')
        from app.reply_batch import finish
        with self.assertRaises(ValueError):finish(self.db,'task',[{'type':'text','text':'迟到回复'}])
        self.assertEqual(self.db.execute('SELECT count(*) FROM reply_outbox').fetchone()[0],1)

    def test_end_cancels_only_unpublished_comment_drafts(self):
        self.db.execute('CREATE TABLE shared_comment_outbox(task_id TEXT,status TEXT)')
        self.db.executemany('INSERT INTO shared_comment_outbox VALUES(?,?)',[('task','waiting_for_reply'),('task','attempted'),('other','pending')]);self.db.commit()
        qm.apply(self.db,'task','task','end','processing')
        self.assertEqual([r[0] for r in self.db.execute('SELECT status FROM shared_comment_outbox')],['cancelled','attempted','pending'])

    def test_running_send_blocks_task_end_without_partial_changes(self):
        self.send(status='attempted')
        with self.assertRaises(ValueError):qm.apply(self.db,'task','task','end','processing')
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'processing')

    def test_status_compare_prevents_stale_or_repeated_actions(self):
        self.send(status='server_accepted')
        with self.assertRaises(ValueError):qm.apply(self.db,'send','reply','cancel','pending')
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'server_accepted')

    def test_batch_cancel_preserves_attempts_and_other_tasks(self):
        self.send();self.send('second',seq=1);self.send('sent','server_accepted',2);self.send('other',task='other')
        qm.apply(self.db,'send','reply','cancel','pending')
        self.assertEqual(dict(self.db.execute('SELECT id,status FROM reply_outbox')),{'reply':'cancelled','second':'cancelled','sent':'server_accepted','other':'pending'})
        self.assertEqual(len(qm.state(self.db)['history']),2)

    def test_unknown_observation_preserves_receipt_and_quiets_only_that_record(self):
        self.send(status='outcome_uncertain')
        before=self.db.execute('SELECT status,detail FROM reply_outbox').fetchone()
        qm.apply(self.db,'send','reply','missing','outcome_uncertain')
        self.assertEqual(before,self.db.execute('SELECT status,detail FROM reply_outbox').fetchone())
        self.assertEqual(qm.state(self.db)['sends'],[])
        self.assertTrue(qm.resolved('outcome_uncertain',qm.resolutions(self.db)[('send','reply')]))
        self.assertFalse(qm.resolved('server_accepted',qm.resolutions(self.db)[('send','reply')]))

    def test_fresh_and_unjournaled_sends_cannot_be_closed(self):
        self.send(status='attempted')
        with self.assertRaises(ValueError):qm.apply(self.db,'send','reply','close_attempt','attempted',now=1000)
        self.db.execute("INSERT INTO outbound_attempts VALUES('attempt','reply',900,NULL,'attempted','{}')");self.db.commit()
        with self.assertRaises(ValueError):qm.apply(self.db,'send','reply','close_attempt','attempted',now=1000)
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'attempted')

    def test_stale_attempt_becomes_unknown_never_pending(self):
        self.send(status='attempted')
        self.db.execute("INSERT INTO outbound_attempts VALUES('attempt','reply',1,NULL,'attempted','{}')");self.db.commit()
        qm.apply(self.db,'send','reply','close_attempt','attempted',now=1000)
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'outcome_uncertain')
        self.assertEqual(self.db.execute('SELECT finished,status FROM outbound_attempts').fetchone(),(1000,'outcome_uncertain'))

    def test_unknown_cannot_be_rearmed_or_claimed_success(self):
        self.send(status='outcome_uncertain')
        for action in ('retry','pending','done','cancel'):
            with self.assertRaises(ValueError):qm.apply(self.db,'send','reply',action,'outcome_uncertain')
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'outcome_uncertain')

    def test_old_active_tasks_remain_accessible(self):
        self.db.executemany('INSERT INTO live_tasks VALUES(?,?,?, ?,?,NULL)',[(f'done{i}',OWNER,i+2,'{}','done') for i in range(120)]);self.db.commit()
        self.assertEqual([x['id'] for x in qm.state(self.db)['tasks']],['task'])

    def test_health_keeps_raw_count_but_excludes_handled_alert(self):
        from app.workflow_health import snapshot
        root=Path(r'__TEMP_PARENT__')
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path=Path(directory)/'state.db';dest=sqlite3.connect(path)
            self.send(status='outcome_uncertain');qm.apply(self.db,'send','reply','dismiss','outcome_uncertain')
            self.db.backup(dest);dest.close()
            result=snapshot(path)
            self.assertTrue(result['ok']);self.assertEqual(result['reply_uncertain'],0)
            self.assertEqual(result['reply_counts']['outcome_uncertain'],1)

    def test_foreign_origin_and_unpaired_bridge_cannot_mutate(self):
        for headers in ({'Origin':'https://bad.invalid'},{'X-Whale-Bridge':'invalid','X-Whale-Device':'invalid'}):
            h=object.__new__(cs.Handler);h.path='/api/maintenance';h.headers={'Host':'127.0.0.1:8793','Content-Type':'application/json','Content-Length':'2',**headers};h.rfile=io.BytesIO(b'{}');h._send=Mock()
            with patch('app.queue_maintenance.apply') as apply,patch('app.whale_channel.authenticate',return_value=False):h.do_POST()
            apply.assert_not_called();self.assertEqual(h._send.call_args.args[0],403)

    def test_local_endpoint_uses_snapshot_and_common_module(self):
        body={'kind':'task','id':'task','action':'end','expected_status':'processing'}
        raw=json.dumps(body).encode();h=object.__new__(cs.Handler);h.path='/api/maintenance'
        h.headers={'Host':'127.0.0.1:8793','Content-Type':'application/json','Content-Length':str(len(raw))};h.rfile=io.BytesIO(raw);h._send=Mock()
        with patch('app.queue_maintenance.apply',return_value={'ok':True,'resent':False}) as apply,patch.object(cs,'ASSISTANT_DB',':memory:'):
            h.do_POST()
        self.assertEqual(h._send.call_args.args[0],200)
        self.assertEqual(apply.call_args.args[1:],('task','task','end','processing',''))


class LateSenderTests(unittest.TestCase):
    def test_late_rejection_cannot_rearm_owner_closed_attempt(self):
        from app import local_runtime as lr
        with tempfile.TemporaryDirectory(dir=Path(r'__TEMP_PARENT__')) as directory:
            store=LiveStore(':memory:');db=store.db
            try:
                db.execute("INSERT INTO reply_outbox(id,task_id,owner,text,status) VALUES('reply','task',?,'hello','pending')",(OWNER,));db.commit()
                def late_result(text):
                    db.execute("UPDATE outbound_attempts SET started=1 WHERE outbox_id='reply'");db.commit()
                    qm.apply(db,'send','reply','close_attempt','attempted')
                    return {'result':'platform_not_accepted'}
                with patch.object(lr,'ROOT',Path(directory)),patch.object(lr,'dm_policy',return_value={'enabled':True,'retry_rejected':True,'retry_max':1,'retry_delay_seconds':0,'min_send_gap_seconds':0}),patch('app.sdk_sender.health',return_value={'online':True,'uid':'__ACCOUNT_UID__'}),patch('app.sdk_sender.send',side_effect=late_result) as send:
                    lr.outbound_step(db,OWNER);lr.outbound_step(db,OWNER)
                self.assertEqual(send.call_count,1)
                self.assertEqual(db.execute('SELECT status FROM reply_outbox').fetchone()[0],'outcome_uncertain')
            finally:db.close()


if __name__=='__main__':unittest.main()
