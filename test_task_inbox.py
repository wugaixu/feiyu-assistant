import sqlite3
import unittest
from app.task_inbox import TaskInbox


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.inbox = TaskInbox(self.db)

    def tearDown(self):
        self.db.close()

    def snapshot(self, token, direction='unverified', owner='owner'):
        return {'sender_profile': owner, 'messages': [
            {'kind': 'text', 'direction': direction, 'raw_text': '任务 ' + token + '\n生成预览'}]}

    def test_one_use_and_reopen(self):
        token = self.inbox.issue('owner')
        snap = self.snapshot(token)
        self.assertEqual(self.inbox.observe(snap, 'owner'), 1)
        reopened = TaskInbox(self.db)
        self.assertEqual(reopened.observe(snap, 'owner'), 0)
        self.assertEqual(self.db.execute('SELECT status FROM task_proposals').fetchone()[0], 'awaiting_local_review')

    def test_identity_direction_expiry_and_history(self):
        token = self.inbox.issue('owner')
        self.assertEqual(self.inbox.observe(self.snapshot(token), None), 0)
        self.assertEqual(self.inbox.observe(self.snapshot(token, owner='stranger'), 'owner'), 0)
        self.assertEqual(self.inbox.observe(self.snapshot(token, direction='outgoing'), 'owner'), 0)
        self.db.execute('UPDATE task_invitations SET expires=0')
        self.db.commit()
        self.assertEqual(self.inbox.observe(self.snapshot(token), 'owner'), 0)
        self.assertEqual(self.inbox.observe(self.snapshot('0' * 32), 'owner'), 0)


if __name__ == '__main__':
    unittest.main()
