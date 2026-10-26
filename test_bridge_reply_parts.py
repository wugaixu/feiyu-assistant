import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import bridge
from app.live_service import LiveStore, ROOT

TASK = 'a' * 32


class BridgeReplyPartsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = LiveStore(self.root / 'assistant.sqlite3')
        db = self.store.db
        from app import outbox
        outbox.schema(db)
        db.execute("CREATE TABLE IF NOT EXISTS live_tasks (id TEXT PRIMARY KEY,owner TEXT,received REAL,raw_json TEXT,status TEXT,result TEXT)")
        db.execute("INSERT INTO live_tasks VALUES (?,?,?,?,?,?)", (TASK, 'owner', 0, '{}', 'processing', None))
        db.commit()
        self.reply = self.root / 'reply.txt'

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def finish(self, text, status='done'):
        self.reply.write_text(text, encoding='utf-8')
        argv = ['bridge', 'finish', '--id', TASK, '--result-file', str(self.reply), '--status', status]
        with patch.object(bridge, 'ROOT', self.root), patch.object(bridge.sys, 'argv', argv):
            bridge.main()

    def rows(self):
        return [tuple(row) for row in self.store.db.execute(
            "SELECT id,kind,seq,text FROM reply_outbox ORDER BY seq")]

    def test_blank_lines_become_separate_messages(self):
        self.finish('第一段\n第二行\n\n第二段\n\n第三段')
        rows = self.rows()
        self.assertEqual([row[1] for row in rows], ['text', 'text', 'text'])
        self.assertEqual([row[2] for row in rows], [0, 1, 2])
        self.assertEqual([row[3] for row in rows], ['第一段\n第二行', '第二段', '第三段'])
        self.assertEqual(rows[0][0], TASK + ':batch:0')
        status = self.store.db.execute('SELECT status FROM live_tasks WHERE id=?', (TASK,)).fetchone()[0]
        self.assertEqual(status, 'done')

    def test_single_paragraph_stays_one_message(self):
        self.finish('就一句话\n没空行')
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], '就一句话\n没空行')

    def test_non_done_status_keeps_single_record(self):
        self.store.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?", (TASK,))
        self.store.db.commit()
        self.finish('需要你再确认一下', status='needs_input')
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], TASK + ':batch:0')
        status = self.store.db.execute('SELECT status FROM live_tasks WHERE id=?', (TASK,)).fetchone()[0]
        self.assertEqual(status, 'needs_input')

    def test_text_over_limit_is_split_by_paragraph_rule(self):
        long_text = 'x' * 1600
        self.finish(long_text)
        rows = self.rows()
        self.assertEqual([len(row[3]) for row in rows], [1500, 100])

    def test_structured_reply_uses_same_batch_and_rejects_cli_audio(self):
        import json
        parts=self.root/'parts.json'
        parts.write_text(json.dumps([{'type':'text','text':'一\n\n二'},{'type':'share','work':{'itemId':'7697598283775355313','title':'作品'}}]),encoding='utf-8')
        argv=['bridge','finish','--id',TASK,'--parts-file',str(parts)]
        with patch.object(bridge,'ROOT',self.root),patch.object(bridge.sys,'argv',argv):bridge.main()
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT kind FROM reply_outbox ORDER BY seq')],['text','text','share'])
        self.store.db.execute('DELETE FROM reply_outbox');self.store.db.execute("UPDATE live_tasks SET status='processing'");self.store.db.commit()
        parts.write_text(json.dumps([{'type':'audio','path':'not-allowed.wav'}]),encoding='utf-8')
        with patch.object(bridge,'ROOT',self.root),patch.object(bridge.sys,'argv',argv):
            with self.assertRaises(ValueError):bridge.main()


if __name__ == '__main__':
    unittest.main()
