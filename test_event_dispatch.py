import sqlite3
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app import event_dispatch as ed
from app.event_dispatch import dispatch_one, thread_id
from app.live_service import LiveStore


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.target_patch=patch.object(ed,'target_config',return_value={'provider':'codex','mode':'explicit','thread_id':ed.DEFAULT_THREAD_ID})
        self.target_patch.start();self.addCleanup(self.target_patch.stop)
        self.store = LiveStore(':memory:')
        self.db = self.store.db
        with self.db:
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                            ('1'*32, 'owner', 1, '{"kind":"text","raw_text":"你好 & $(echo test)"}'))

    def tearDown(self):
        self.db.close()

    def test_native_argv_delivery_and_no_duplicate(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stdout='Queued message 11111111-1111-1111-1111-111111111111 for thread '+thread_id()+'.\n', stderr='')
        result = dispatch_one(self.db, run, 'C:/codex.exe')
        self.assertEqual(result['status'], 'queued')
        self.assertFalse(calls[0][1]['shell'])
        self.assertIn('你好 & $(echo test)', calls[0][0][-1])
        self.assertIsNone(dispatch_one(self.db, run, 'C:/codex.exe'))
        self.assertEqual(len(calls), 1)


class TargetSelectionTests(unittest.TestCase):
    """The dispatch target is configuration-driven, never pinned in source."""

    A = '00000000-0000-0000-0000-000000000000'
    B = '01a16473-fb08-7353-af43-5536fa74734f'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        folder = Path(self.tmp.name)
        self.config = folder / 'target.json'
        self.state = folder / 'state_5.sqlite'
        self.saved = (ed.TARGET_FILE, ed.STATE_DB)
        ed.TARGET_FILE = self.config
        ed.STATE_DB = self.state

    def tearDown(self):
        ed.TARGET_FILE, ed.STATE_DB = self.saved
        self.tmp.cleanup()

    def configure(self, **value):
        self.config.write_text(json.dumps(value), encoding='utf-8')

    def activity(self, rows):
        db = sqlite3.connect(self.state)
        db.execute('CREATE TABLE IF NOT EXISTS threads (id TEXT, recency_at_ms INTEGER, archived INTEGER, title TEXT)')
        db.execute('DELETE FROM threads')
        db.executemany('INSERT INTO threads VALUES (?,?,?,?)', rows)
        db.commit()
        db.close()

    def test_legacy_auto_never_follows_activity(self):
        self.configure(mode='most_recent',thread_id=self.A,candidates=[self.A,self.B],fallback_thread_id=self.B)
        self.activity([(self.A,100,0,'x'),(self.B,900,0,'y')])
        self.assertEqual(thread_id(),self.A)
    def test_missing_selection_never_uses_candidates_or_fallback(self):
        self.configure(mode='most_recent',candidates=[self.A,self.B],fallback_thread_id=self.B)
        self.assertIsNone(thread_id())
        self.assertEqual(choose_report_reason(),'selection_required')
    def test_explicit_mode_pins_one_window(self):
        self.configure(mode='explicit',thread_id=self.B)
        self.activity([(self.A,1000,0,'x'),(self.B,1,0,'y')])
        self.assertEqual(thread_id(),self.B)
    def test_invalid_config_cannot_redirect_delivery(self):
        self.config.write_text('{"thread_id":"not-a-thread"}',encoding='utf-8')
        self.assertIsNone(thread_id())


def choose_report_reason():
    return ed.choose_thread()['reason']

    def test_timeout_not_retried(self):
        def fail(*args, **kwargs):
            raise TimeoutError('unknown delivery')
        self.assertEqual(dispatch_one(self.db, fail, 'C:/codex.exe')['status'], 'outcome_uncertain')
        self.assertIsNone(dispatch_one(self.db, fail, 'C:/codex.exe'))

    def test_shell_launcher_rejected(self):
        with self.assertRaises(ValueError):
            dispatch_one(self.db, executable='C:/codex.cmd')


if __name__ == '__main__':
    unittest.main()
