import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from app import douyin_content as content


class ContentTests(unittest.TestCase):
    def test_resource_hosts_do_not_allow_arbitrary_requests(self):
        self.assertTrue(content.allowed_url('https://v3-web.douyinvod.com/video'))
        for url in ['http://v3-web.douyinvod.com/x','https://douyinvod.com.evil.com/x','https://127.0.0.1/a','https://user@douyin.com/x','file:///C:/x']:
            self.assertFalse(content.allowed_url(url))

    def test_caption_formats_and_timestamp_requirement(self):
        for data in [b'WEBVTT\n\n00:00.000 --> 00:02.000\nhello',
                     b'1\n00:00:00,000 --> 00:00:02,000\nhello',
                     b'{"utterances":[{"text":"hello","start_time":0,"end_time":2000}]}']:
            self.assertEqual(content.parse_subtitles(data)[0]['text'],'hello')
        for data in [b'<html>login required</html>',b'not real subtitles',b'{"summary":"fake"}']:
            with self.assertRaises(ValueError): content.parse_subtitles(data)

    def test_source_discovery_keeps_only_resource_candidates(self):
        work={'video':{'cla_info':{'caption_infos':[{'url':'https://example.com/secret'},{'url':'https://p3.douyincdn.com/sub.vtt','lang':'zh'}]}}}
        self.assertEqual(len(content.candidates(work)),1)

    def test_transcript_pagination_never_claims_full_content(self):
        root=Path(r'__RUNTIME_ROOT__')
        with tempfile.TemporaryDirectory(dir=root) as temp,patch.object(content,'ROOT',Path(temp)):
            dest=content.transcript_path('7695717050077612699');dest.parent.mkdir(parents=True)
            dest.write_text(json.dumps({'source':'platform_caption','sha256':'hash','text':'abcdef','uncertainty':'may be incorrect'}),encoding='utf-8')
            data=content.read('7695717050077612699',0,3)
            self.assertEqual(data['text'],'abc');self.assertEqual(data['next_offset'],3)
            self.assertTrue(data['untrusted_content'])
            with self.assertRaises(ValueError): content.read('../secret')

    def test_ocr_grounded_review_connects_to_existing_video_ledger(self):
        from app import douyin_toolkit as kit
        from app import video_rounds as rounds
        with tempfile.TemporaryDirectory(dir=content.ROOT) as temp,patch.object(content,'ROOT',Path(temp)),closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE live_tasks(id TEXT PRIMARY KEY,status TEXT,raw_json TEXT)')
            db.execute('INSERT INTO live_tasks VALUES(?,?,?)',('a'*32,'processing',json.dumps({'kind':'shared_video'})));db.commit()
            rounds.schema(db)
            key='7695717050077612699'
            db.execute('INSERT INTO video_reviews VALUES(?,?,?,?,?)',(key,'a'*32,'2026-10-23','inspecting','{}'));db.commit()
            target=content.transcript_path(key).parent/'frame-ocr.json';target.parent.mkdir(parents=True)
            target.write_text(json.dumps({'source':'sampled_frame_ocr','sha256':'testhash','text':'actual evidence'}),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'exact supporting'):
                kit.record_review(db,'a'*32,key,'interesting','summary','reason','testhash',['invented'])
            result=kit.record_review(db,'a'*32,key,'interesting','limited sample summary','reason','testhash',['actual evidence'])
            self.assertEqual(result['status'],'interesting')
            saved=json.loads(db.execute('SELECT record_json FROM video_reviews').fetchone()[0])
            self.assertEqual(saved['evidence_source'],'sampled_frame_ocr')


if __name__=='__main__': unittest.main()
