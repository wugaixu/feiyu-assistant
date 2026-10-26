import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import bridge
from app.live_service import LiveStore


class BridgeTests(unittest.TestCase):
    def test_claim_finish_and_no_duplicate_result(self):
        root = Path(r'__RUNTIME_ROOT__\tests')
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            runtime = Path(folder)
            store = LiveStore(runtime / 'assistant.sqlite3')
            task_id = '1' * 32
            with store.db:
                store.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                                 (task_id, 'owner', 1, '{}'))
            def run(*args):
                with patch.object(bridge, 'ROOT', runtime), patch.object(sys, 'argv', ['bridge.py', *args]), contextlib.redirect_stdout(io.StringIO()):
                    bridge.main()
            run('claim', '--id', task_id)
            with self.assertRaises(ValueError):
                run('claim', '--id', task_id)
            reply = runtime / 'reply.txt'
            reply.write_text('你好，已经收到。', encoding='utf-8')
            run('finish', '--id', task_id, '--result-file', str(reply))
            with self.assertRaises(ValueError):
                run('finish', '--id', task_id, '--result-file', str(reply))
            self.assertEqual(store.db.execute('select count(*) from reply_outbox').fetchone()[0], 1)
            self.assertEqual(store.db.execute('select status from live_tasks').fetchone()[0], 'done')
            store.db.close()


if __name__ == '__main__':
    unittest.main()
