import json,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from app import console_server as cs,sdk_sender
from app.live_service import retry_decision

class OutboundStateTests(unittest.TestCase):
    def test_all_message_types_and_whale_receipts_are_visible_without_writes(self):
        with tempfile.TemporaryDirectory(dir=cs.ROOT) as directory:
            path=Path(directory)/'db.sqlite3';db=sqlite3.connect(path)
            db.execute('CREATE TABLE reply_outbox(id TEXT,task_id TEXT,text TEXT,status TEXT,detail TEXT,kind TEXT,path TEXT,work_json TEXT)')
            receipt=json.dumps({'channel':'whale','result':'server_accepted','receipt_scope':'saved_in_local_whale_history'})
            for kind in ('text','image','audio','share'):
                db.execute('INSERT INTO reply_outbox VALUES(?,?,?,?,?,?,?,?)',(kind,'task','实际原文' if kind in ('text','audio') else '',
                    'server_accepted',receipt,kind,r'D:\private\voice.wav' if kind=='audio' else None,json.dumps({'title':'作品标题'}) if kind=='share' else None))
            db.commit();before=db.execute('SELECT * FROM reply_outbox').fetchall();db.close()
            with patch.object(cs,'ASSISTANT_DB',path),patch.object(cs,'ROOT',Path(directory)):
                state=cs.outbound_state()
            self.assertEqual([x['kind'] for x in state['items']],['share','audio','image','text'])
            self.assertEqual(state['items'][1]['file_name'],'voice.wav')
            self.assertEqual(state['items'][0]['work_title'],'作品标题')
            for item in state['items']:
                self.assertEqual(item['status'],'server_accepted');self.assertIn('尚未确认手机',item['assessment']['detail'])
                self.assertNotIn('path',item)
            with closing(sqlite3.connect(path)) as check:self.assertEqual(check.execute('SELECT * FROM reply_outbox').fetchall(),before)

    def test_text_feedback_is_not_evidence_for_audio_receipt(self):
        from app.delivery_review import automatic
        current={'kind':'audio','channel':'whale','status':'outcome_uncertain','check_code':None}
        past={'kind':'text','channel':'whale','status':'outcome_uncertain','check_code':None,'review':{'receipt':{'value':'confirmed'}}}
        self.assertNotIn('推测通道可用',automatic([current,past])['detail'])

    def test_business_check_cannot_silently_appear_as_success(self):
        with tempfile.TemporaryDirectory(dir=cs.ROOT) as directory:
            path=Path(directory)/'db.sqlite3';db=sqlite3.connect(path)
            db.execute('CREATE TABLE reply_outbox(id TEXT,task_id TEXT,text TEXT,status TEXT,detail TEXT,kind TEXT DEFAULT \'text\')')
            db.execute('INSERT INTO reply_outbox(id,task_id,text,status,detail) VALUES(?,?,?,?,?)',('1','task','test','server_accepted',json.dumps({'ok':True,'statusCode':0,'checkCode':8101,'serverMessageId':'123'})))
            db.commit();db.close()
            with patch.object(cs,'ASSISTANT_DB',path):state=cs.outbound_state()
        self.assertEqual(state['alerts'][0]['status'],'outcome_uncertain')
        self.assertEqual(state['alerts'][0]['stored_status'],'server_accepted')
        self.assertEqual(state['alerts'][0]['check_code'],8101)

    def test_unverified_business_code_never_qualifies_for_retry(self):
        result=sdk_sender.classify({'ok':True,'statusCode':0,'checkCode':8101,'serverMessageId':'123'})
        self.assertFalse(retry_decision(0,result,{'retry_rejected':True,'retry_max':1})['retry'])

    def test_clear_business_rejection_keeps_bounded_retry(self):
        result=sdk_sender.classify({'ok':False,'statusCode':0,'checkCode':8610})
        self.assertTrue(retry_decision(0,result,{'retry_rejected':True,'retry_max':1})['retry'])
        self.assertFalse(retry_decision(1,result,{'retry_rejected':True,'retry_max':1})['retry'])

if __name__=='__main__':unittest.main()
