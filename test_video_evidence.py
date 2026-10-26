import unittest
import sqlite3,json
from unittest.mock import patch
from app.video_evidence import select_times
from app.douyin_comments import COMMENT_LIMIT, normalize


class EvidenceTests(unittest.TestCase):
    def test_sampling_covers_long_video_end(self):
        times=select_times(600,9)
        self.assertLess(times[0],60)
        self.assertGreater(times[-1],540)
        self.assertEqual(len(set(times)),9)

    def test_scene_snapping_does_not_collapse_into_opening(self):
        times=select_times(90,9,[1,2,3,4,5,6,7])
        self.assertEqual(times[0],5)
        self.assertGreater(times[-1],80)

    def test_invalid_sampling_rejected(self):
        for duration,count in [(0,9),(90,0),(90,100)]:
            with self.assertRaises(ValueError): select_times(duration,count)

    def test_comment_payload_is_bounded_and_excludes_account_data(self):
        rows=normalize({'comments':[{'cid':str(i),'text':'x'*1000,'user':{'secret':'private'},'digg_count':i} for i in range(20)]})
        self.assertEqual(len(rows),COMMENT_LIMIT)
        self.assertEqual(len(rows[0]['text']),300)
        self.assertNotIn('user',rows[0])

    def test_comment_readback_matches_receipt_id_not_similar_text(self):
        from app.douyin_comments import verify
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE video_comments(video_id TEXT,status TEXT,detail TEXT)')
        db.execute('INSERT INTO video_comments VALUES(?,?,?)',('123','accepted_unverified',json.dumps({'receipt':[{'status_code':0,'comment_id':'expected'}]})));db.commit()
        with patch('app.douyin_comments.fetch',return_value={'status':'done','items':[{'comment_id':'other','text':'same text'}]}):
            self.assertEqual(verify(db,None,'123')['status'],'not_observed')
        with patch('app.douyin_comments.fetch',return_value={'status':'done','items':[{'comment_id':'expected','text':'exact comment'}]}):
            result=verify(db,None,'123')
            self.assertEqual(result['status'],'visible_in_history')
            self.assertFalse(result['owner_notification_confirmed'])


if __name__=='__main__':unittest.main()
