import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app import event_dispatch as ed
from app.event_dispatch import dispatch_one, thread_id
from app.live_service import LiveStore


class BurstMergeTests(unittest.TestCase):
    def setUp(self):
        self.target = patch.object(ed, 'target_config',
                                   return_value={'provider': 'codex', 'mode': 'explicit', 'thread_id': ed.DEFAULT_THREAD_ID})
        self.target.start(); self.addCleanup(self.target.stop)
        self.store = LiveStore(':memory:')
        self.db = self.store.db

    def tearDown(self):
        self.db.close()

    def add_text(self, task_id, received, text, status='pending', transport='sdk', device=None):
        value = {'kind': 'text', 'raw_text': text, 'transport': transport}
        if device:
            value['reply_device'] = device
        with self.db:
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                            (task_id, 'owner', received, json.dumps(value), status))

    def runner(self, calls):
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0,
                                   stdout='Queued message 11111111-1111-1111-1111-111111111111 for thread ' + thread_id() + '.\n',
                                   stderr='')
        return run

    def test_quiet_window_waits_for_the_latest_message(self):
        now = time.time()
        self.add_text('a' * 32, now - 30, '第一句')
        self.add_text('b' * 32, now - 3, '第二句')
        self.assertIsNone(dispatch_one(self.db, self.runner([]), 'C:/codex.exe'))

    def test_burst_is_delivered_once_with_every_message(self):
        now = time.time(); calls = []
        self.add_text('a' * 32, now - 30, '第一句')
        self.add_text('b' * 32, now - 20, '第二句')
        self.assertEqual(dispatch_one(self.db, self.runner(calls), 'C:/codex.exe')['status'], 'queued')
        self.assertEqual(len(calls), 1)
        raw = json.loads(self.db.execute('select raw_json from live_tasks where id=?', ('a' * 32,)).fetchone()[0])
        self.assertEqual([item['text'] for item in raw['merged_messages']], ['第一句', '第二句'])
        self.assertIn('第一句', raw['raw_text'])
        self.assertIn('第二句', raw['raw_text'])
        self.assertIsNone(dispatch_one(self.db, self.runner(calls), 'C:/codex.exe'))
        self.assertEqual(len(calls), 1)

    def test_messages_further_apart_form_separate_bursts(self):
        now = time.time(); calls = []
        self.add_text('a' * 32, now - 60, '很早的一句')
        self.add_text('b' * 32, now - 20, '最新的一句')
        self.assertEqual(dispatch_one(self.db, self.runner(calls), 'C:/codex.exe')['status'], 'queued')
        raw = json.loads(self.db.execute('select raw_json from live_tasks where id=?', ('b' * 32,)).fetchone()[0])
        self.assertNotIn('merged_messages', raw)
        self.assertEqual(self.db.execute('select status from live_tasks where id=?', ('a' * 32,)).fetchone()[0], 'pending')

    def test_stale_orphan_from_an_older_burst_is_ignored(self):
        now = time.time(); calls = []
        with self.db:
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                            ('v' * 32, 'owner', now - 7200,
                             json.dumps({'kind': 'shared_video', 'raw_text': '[分享抖音作品] 旧视频', 'transport': 'sdk'}),
                             'pending'))
        self.add_text('a' * 32, now - 30, '吃的是粉丝汤')
        self.add_text('b' * 32, now - 20, '和锅贴')
        self.assertEqual(dispatch_one(self.db, self.runner(calls), 'C:/codex.exe')['status'], 'queued')
        self.assertEqual(len(calls), 1)
        raw = json.loads(self.db.execute('select raw_json from live_tasks where id=?', ('a' * 32,)).fetchone()[0])
        self.assertEqual([item['text'] for item in raw['merged_messages']], ['吃的是粉丝汤', '和锅贴'])
        self.assertEqual(self.db.execute('select status from live_tasks where id=?', ('v' * 32,)).fetchone()[0], 'pending')
        self.assertIsNone(dispatch_one(self.db, self.runner(calls), 'C:/codex.exe'))

    def test_saved_media_joins_the_burst_and_attaches(self):
        now = time.time(); calls = []
        self.add_text('a' * 32, now - 40, '看这个')
        with self.db:
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                            ('b' * 32, 'owner', now - 30,
                             json.dumps({'kind': 'shared_media', 'original_kind': 'image', 'transport': 'sdk'}),
                             'resource_saved'))
            self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',
                            ('b' * 32, 'owner', now - 30, 'image',
                             json.dumps({'kind': 'image', 'status': 'saved', 'files': [{'path': 'D:/media/x.png', 'status': 'saved'}]})))
        self.assertEqual(dispatch_one(self.db, self.runner(calls), 'C:/codex.exe')['status'], 'queued')
        self.assertEqual(len(calls), 1)
        attached = self.db.execute('select attached_to from media_records where id=?', ('b' * 32,)).fetchone()[0]
        self.assertEqual(attached, 'a' * 32)
        self.assertEqual(self.db.execute('select status from live_tasks where id=?', ('b' * 32,)).fetchone()[0], 'merged')
        packet=json.loads(calls[0][-1].splitlines()[1])
        from pathlib import Path
        self.assertIn('D:/media/x.png',Path(packet['context_file']).read_text(encoding='utf-8'))

    def test_long_contiguous_chain_keeps_oldest_task_and_arrival_order(self):
        now=time.time();calls=[]
        for key,age,text in [('a',38,'一'),('b',29,'二'),('c',20,'三')]:
            self.add_text(key*32,now-age,text)
        result=dispatch_one(self.db,self.runner(calls),'C:/codex.exe')
        self.assertEqual(result['task_id'],'a'*32)
        raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',('a'*32,)).fetchone()[0])
        self.assertEqual([x['text'] for x in raw['merged_messages']],['一','二','三'])

    def test_recent_media_pending_keeps_original_quiet_window(self):
        now=time.time()
        self.add_text('a'*32,now-18,'看看')
        self.add_text('b'*32,now-9,'图片',status='media_pending')
        self.assertIsNone(dispatch_one(self.db,self.runner([]),'C:/codex.exe'))
        self.assertEqual(self.db.execute("SELECT status FROM live_tasks WHERE id=?",('a'*32,)).fetchone()[0],'pending')

    def test_retry_after_offline_target_keeps_merged_tail_and_links(self):
        now=time.time();calls=[]
        self.add_text('a'*32,now-38,'第一条')
        self.add_text('b'*32,now-29,'第二条')
        self.add_text('c'*32,now-20,'链接')
        with self.db:
            raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',('c'*32,)).fetchone()[0])
            raw['links']=['https://example.com/work']
            self.db.execute('UPDATE live_tasks SET raw_json=? WHERE id=?',(json.dumps(raw),'c'*32))
        with self.assertRaises(ValueError):dispatch_one(self.db,executable='codex.cmd')
        self.assertEqual(dispatch_one(self.db,self.runner(calls),'C:/codex.exe')['task_id'],'a'*32)
        raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',('a'*32,)).fetchone()[0])
        self.assertEqual(raw['links'],['https://example.com/work'])
        self.assertEqual(len(raw['merged_messages']),3)

    def test_old_media_within_legacy_ten_minutes_is_not_attached(self):
        now=time.time();calls=[]
        self.add_text('a'*32,now-20,'当前话题')
        with self.db:
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                            ('v'*32,'owner',now-100,json.dumps({'kind':'shared_media','transport':'sdk'}),'resource_saved'))
            self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',
                            ('v'*32,'owner',now-100,'image',json.dumps({'kind':'image','status':'saved','files':[{'path':'old.png'}]})))
        dispatch_one(self.db,self.runner(calls),'C:/codex.exe')
        self.assertIsNone(self.db.execute('SELECT attached_to FROM media_records').fetchone()[0])

    def test_new_message_during_host_check_defers_reservation(self):
        now=time.time();calls=[]
        self.add_text('a'*32,now-20,'原话')
        def availability():
            self.add_text('b'*32,now,'补充')
            return {'provider':'codex','thread_id':ed.DEFAULT_THREAD_ID}
        with patch.object(ed,'target_config',side_effect=availability):
            self.assertIsNone(dispatch_one(self.db,self.runner(calls),'C:/codex.exe'))
        self.assertEqual(calls,[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM chat_dispatch').fetchone()[0],0)

    def test_failed_media_does_not_strand_its_accompanying_text(self):
        now=time.time();calls=[]
        self.add_text('a'*32,now-29,'看看这张')
        self.add_text('b'*32,now-20,'图片',status='resource_saved')
        with self.db:
            self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',
                            ('b'*32,'owner',now-20,'image',json.dumps({'status':'unavailable','failure_reason':'write_failed'})))
        self.assertEqual(dispatch_one(self.db,self.runner(calls),'C:/codex.exe')['task_id'],'a'*32)
        self.assertIsNone(self.db.execute('SELECT attached_to FROM media_records').fetchone()[0])

    def test_other_source_is_not_merged(self):
        now = time.time(); calls = []
        self.add_text('a' * 32, now - 40, '抖音这句')
        self.add_text('b' * 32, now - 20, '鲸聊这句', transport='whale', device='dev1')
        self.assertEqual(dispatch_one(self.db, self.runner(calls), 'C:/codex.exe')['status'], 'queued')
        raw = json.loads(self.db.execute('select raw_json from live_tasks where id=?', ('a' * 32,)).fetchone()[0])
        self.assertNotIn('merged_messages', raw)

    def test_outgoing_diary_record_does_not_move_owner_quiet_window(self):
        now=time.time();calls=[]
        self.add_text('a'*32,now-20,'主人消息')
        with self.db:
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                ('d'*32,'owner',now-2,json.dumps({'kind':'diary_proactive','origin':'diary_proactive','direction':'outgoing','transport':'sdk'}),'done'))
        self.assertEqual(dispatch_one(self.db,self.runner(calls),'C:/codex.exe')['task_id'],'a'*32)


if __name__ == '__main__':
    unittest.main()
