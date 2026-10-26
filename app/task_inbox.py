"""Explicit one-use invitations. Stores proposals only; never executes commands."""
import json
import re
import secrets
import sqlite3
import time


class TaskInbox:
    def __init__(self, db):
        self.db = db
        db.executescript('''
            CREATE TABLE IF NOT EXISTS task_invitations (
                token TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL,
                used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS task_proposals (
                token TEXT PRIMARY KEY, owner TEXT NOT NULL, raw_text TEXT NOT NULL,
                evidence TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'awaiting_local_review');
        ''')

    def issue(self, owner, lifetime=1800):
        if not owner or not 1 <= lifetime <= 3600:
            raise ValueError('Owner and bounded lifetime required')
        token = secrets.token_hex(16)
        with self.db:
            self.db.execute('INSERT INTO task_invitations(token,owner,expires) VALUES(?,?,?)',
                            (token, owner, time.time() + lifetime))
        return token

    def observe(self, snapshot, verified_owner):
        """Caller must freshly verify the active contact around this snapshot.

        A non-outgoing DOM bubble remains unverified, so even an invitation only
        creates a proposal for local review, never an authorized task.
        """
        if not verified_owner or snapshot.get('sender_profile') != verified_owner:
            return 0
        accepted = 0
        for message in snapshot.get('messages', []):
            if message.get('kind') != 'text' or message.get('direction') == 'outgoing':
                continue
            raw = message.get('raw_text', '')
            match = re.fullmatch(r'任务 ([0-9a-f]{32})\n([\s\S]{1,8000})', raw)
            if not match:
                continue
            token = match.group(1)
            with self.db:
                changed = self.db.execute('''UPDATE task_invitations SET used=1
                    WHERE token=? AND owner=? AND used=0 AND expires>?''',
                    (token, verified_owner, time.time())).rowcount
                if not changed:
                    continue
                self.db.execute('INSERT INTO task_proposals(token,owner,raw_text,evidence) VALUES(?,?,?,?)',
                                (token, verified_owner, raw, json.dumps(snapshot, ensure_ascii=False)))
                accepted += 1
        return accepted
