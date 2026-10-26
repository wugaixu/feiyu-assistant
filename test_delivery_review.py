import sqlite3,tempfile,unittest,json,threading,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from app import delivery_review as review,console_server as server
from app.workflow_health import assess


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=server.ROOT)
        self.root=Path(self.temp.name);self.db=self.root/'business.sqlite3';self.audit=self.root/'delivery-reviews.sqlite3'
        db=sqlite3.connect(self.db)
        db.execute('CREATE TABLE reply_outbox(id TEXT,task_id TEXT,text TEXT,status TEXT,detail TEXT,kind TEXT DEFAULT \'text\')')
        db.execute("INSERT INTO reply_outbox(id,task_id,text,status,detail) VALUES('r','t','正文','outcome_uncertain','{}')")
        db.execute("INSERT INTO reply_outbox(id,task_id,text,status,detail) VALUES('p','t','待发','pending','{}')")
        db.execute('CREATE TABLE shared_comment_outbox(video_id TEXT,status TEXT)')
        db.execute("INSERT INTO shared_comment_outbox VALUES('v','accepted_unverified')")
        db.commit();db.close()
    def tearDown(self):self.temp.cleanup()

    def test_separate_comment_and_mention_and_auditable_correction(self):
        review.save(self.audit,self.db,'comment','v','visibility','confirmed')
        review.save(self.audit,self.db,'comment','v','mention','missing')
        review.save(self.audit,self.db,'comment','v','mention','confirmed')
        observations=review.latest(self.audit)[('comment','v')]
        self.assertEqual(observations['visibility']['value'],'confirmed');self.assertEqual(observations['mention']['value'],'confirmed')
        db=sqlite3.connect(self.audit);self.assertEqual(db.execute('SELECT count(*) FROM reviews').fetchone()[0],3);db.close()
        db=sqlite3.connect(self.db);self.assertEqual(db.execute('SELECT status FROM shared_comment_outbox').fetchone()[0],'accepted_unverified');db.close()

    def test_reject_nonexistent_pending_invalid_and_sql_injection(self):
        for args in [('reply','absent','receipt','confirmed'),('reply','p','receipt','confirmed'),('reply','r','mention','confirmed'),('reply','r','receipt','bad'),('comment',"v' OR 1=1 --",'mention','confirmed')]:
            with self.assertRaises(ValueError):review.save(self.audit,self.db,*args)
        self.assertFalse(self.audit.exists())

    def test_received_or_deferred_removes_alert_without_changing_receipt(self):
        with patch.object(server,'ROOT',self.root),patch.object(server,'ASSISTANT_DB',self.db):
            self.assertEqual(len(server.outbound_state()['alerts']),1)
            for value in ('confirmed','deferred'):
                review.save(self.audit,self.db,'reply','r','receipt',value)
                state=server.outbound_state();self.assertFalse(state['alerts'])
                self.assertEqual(next(x for x in state['items'] if x['id']=='r')['stored_status'],'outcome_uncertain')
            review.save(self.audit,self.db,'reply','r','receipt','missing')
            self.assertEqual(len(server.outbound_state()['alerts']),1)
            review.save(self.audit,self.db,'reply','r','receipt','unknown')
            self.assertEqual(len(server.outbound_state()['alerts']),1)

    def test_read_does_not_create_audit_database(self):
        self.assertEqual(review.latest(self.audit),{});self.assertFalse(self.audit.exists())

    def test_http_review_guards_origin_and_persists_local_observation(self):
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        worker=threading.Thread(target=lambda:http.serve_forever(poll_interval=.05),daemon=True);worker.start()
        def request(origin):
            req=urllib.request.Request(f'http://127.0.0.1:{http.server_port}/api/review',
                data=json.dumps({'kind':'reply','id':'r','field':'receipt','value':'deferred'}).encode(),
                headers={'Host':'127.0.0.1:8793','Origin':origin,'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=2) as result:return json.load(result)
        try:
            with patch.object(server,'ROOT',self.root),patch.object(server,'ASSISTANT_DB',self.db):
                with self.assertRaises(urllib.error.HTTPError) as error:request('https://evil.example')
                self.assertEqual(error.exception.code,403);error.exception.close();self.assertFalse(self.audit.exists())
                with self.assertRaises(urllib.error.HTTPError) as error:request('http://127.0.0.1:8793')
                self.assertEqual(error.exception.code,400);error.exception.close()
                self.assertFalse(self.audit.exists())
        finally:http.shutdown();worker.join();http.server_close()

    def test_auto_uses_newest_evidence_not_historical_failures(self):
        items=[{'status':'server_accepted'}, {'status':'platform_not_accepted','review':{'receipt':{'value':'missing'}}}]
        self.assertEqual(review.automatic(items)['state'],'idle')
        self.assertEqual(review.automatic([{'status':'platform_not_accepted'}])['state'],'warn')

    def test_matching_history_is_inference_and_conflicts_stay_uncertain(self):
        current={'status':'outcome_uncertain','check_code':8101}
        past={'status':'outcome_uncertain','check_code':8101,'review':{'receipt':{'value':'confirmed'}}}
        result=review.automatic([current,past]);self.assertEqual(result['state'],'idle');self.assertIn('推测',result['detail'])
        past['review']['receipt']['value']='missing'
        self.assertEqual(review.automatic([current,past])['state'],'note')

    def test_comment_program_timeout_is_actionable_without_recipient_checks(self):
        record={'status':'outcome_uncertain','error_class':'TimeoutError','diagnostic':{'phase':'select_owner'}}
        result=review.automatic([record],'comment')
        self.assertEqual(result['state'],'warn');self.assertIn('选择主人提及发生超时',result['detail'])


if __name__=='__main__':unittest.main()
