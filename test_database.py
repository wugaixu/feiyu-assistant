import tempfile
import unittest
from pathlib import Path
from app.database import MessageStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        root = Path(r'__RUNTIME_ROOT__\tests')
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.store = MessageStore(Path(self.tmp.name) / 'test.sqlite3')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_stable_id_deduplicates_but_retains_raw_observations(self):
        snap = {'source': 'test', 'sender_profile': 'owner', 'messages': [
            {'platform_id': '123', 'direction': 'incoming', 'raw_text': 'same'}]}
        self.assertEqual(self.store.record(snap, 'owner')['inserted'], 1)
        self.assertEqual(self.store.record(snap, 'owner')['inserted'], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM observations').fetchone()[0], 2)

    def test_missing_identity_or_id_cannot_create_task_message(self):
        for sender, mid, direction in [('stranger', '1', 'incoming'), ('owner', None, 'incoming'), ('owner', '2', 'outgoing')]:
            self.store.record({'source': 'test', 'sender_profile': sender, 'messages': [
                {'platform_id': mid, 'direction': direction, 'raw_text': 'test'}]}, 'owner')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
