import json, sqlite3, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
from app import voice_gate as gate
from app.live_service import ROOT

TOKEN = 'a' * 48


def build(folder, transport='whale', status='processing', expires=None):
    db = sqlite3.connect(Path(folder) / 'assistant.sqlite3')
    try:
        db.execute('CREATE TABLE live_tasks (id TEXT PRIMARY KEY,status TEXT,raw_json TEXT)')
        db.execute('CREATE TABLE whale_voice_permits (token TEXT PRIMARY KEY,task_id TEXT,expires REAL)')
        db.execute('INSERT INTO live_tasks VALUES (?,?,?)',
                   ('t1', status, json.dumps({'transport': transport, 'direction': 'incoming'})))
        db.execute('INSERT INTO whale_voice_permits VALUES (?,?,?)',
                   (TOKEN, 't1', time.time() + 600 if expires is None else expires))
        db.commit()
    finally:
        db.close()


class VoiceGateTests(unittest.TestCase):
    def test_token_is_extracted_only_in_permit_form(self):
        self.assertEqual(gate.token_of('前缀\n[鲸聊语音许可:' + TOKEN + ']\n后文'), TOKEN)
        self.assertEqual(gate.token_of('普通文字'), '')
        self.assertEqual(gate.token_of(None), '')
        self.assertEqual(gate.token_of('[鲸聊语音许可:abc]'), '')

    def test_whale_owner_turn_is_allowed(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            build(folder)
            with patch.object(gate, 'ROOT', Path(folder)):
                self.assertTrue(gate.permit_valid(TOKEN))
                self.assertTrue(gate.allowed('[鲸聊语音许可:' + TOKEN + ']'))
                self.assertEqual(gate.status_text('[鲸聊语音许可:' + TOKEN + ']'), '')

    def test_other_transports_and_states_are_refused(self):
        for kwargs in ({'transport': 'douyin'}, {'status': 'done'}, {'expires': time.time() - 1}):
            with tempfile.TemporaryDirectory(dir=ROOT) as folder:
                build(folder, **kwargs)
                with patch.object(gate, 'ROOT', Path(folder)):
                    self.assertFalse(gate.permit_valid(TOKEN), kwargs)
                    self.assertFalse(gate.allowed('[鲸聊语音许可:' + TOKEN + ']'), kwargs)

    def test_missing_database_or_token_is_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            with patch.object(gate, 'ROOT', Path(folder)):
                self.assertFalse(gate.permit_valid(TOKEN))
                self.assertFalse(gate.allowed('没有许可'))
                text = gate.status_text('没有许可')
                self.assertIn('文字', text)
            with patch.object(gate, 'ROOT', Path(folder)):
                self.assertFalse(gate.permit_valid('../etc'))
                self.assertFalse(gate.permit_valid(None))

    def test_expired_text_is_explained(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            build(folder, expires=time.time() - 5)
            with patch.object(gate, 'ROOT', Path(folder)):
                self.assertIn('过期', gate.status_text('[鲸聊语音许可:' + TOKEN + ']'))


if __name__ == '__main__':
    unittest.main()
