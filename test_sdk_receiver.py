import json,sqlite3,unittest
from app.sdk_receiver import ingest,OWNER_UID,ACCOUNT_UID

class ReceiveTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE live_tasks(id TEXT PRIMARY KEY,owner TEXT,received REAL,raw_json TEXT,status TEXT)')
    def event(self,mid='message1',seq=1,kind='text'):
        return dict(seq=seq,message_id=mid,sender_uid=OWNER_UID,account_uid=ACCOUNT_UID,text='hello',content={'kind':kind})
    def test_replay_deduplicates(self):
        self.assertEqual(ingest(self.db,[self.event()]),1)
        self.assertEqual(ingest(self.db,[self.event()]),0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM live_tasks').fetchone()[0],1)
    def test_same_text_different_ids(self):
        self.assertEqual(ingest(self.db,[self.event(),self.event('message2',2)]),2)
    def test_sender_mismatch_is_quarantined_and_cursor_advances(self):
        event=self.event();event['sender_uid']='other'
        self.assertEqual(ingest(self.db,[event]),0)
        self.assertEqual(self.db.execute('SELECT seq FROM sdk_inbound_cursor').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM live_tasks').fetchone()[0],0)
    def test_media_preserved(self):
        ingest(self.db,[self.event(kind='share')])
        raw,state=self.db.execute('SELECT raw_json,status FROM live_tasks').fetchone()
        self.assertEqual(state,'media_pending');self.assertEqual(json.loads(raw)['kind'],'video_card')

    def test_platform_time_preserves_order_in_out_of_order_batch(self):
        text=self.event('text',1);text['timestamp']=200
        video=self.event('video',2,'share');video['timestamp']=100
        ingest(self.db,[text,video])
        first=json.loads(self.db.execute('SELECT raw_json FROM live_tasks ORDER BY received LIMIT 1').fetchone()[0])
        self.assertEqual(first['kind'],'video_card')

if __name__=='__main__':unittest.main()
