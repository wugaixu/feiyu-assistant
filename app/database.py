import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class MessageStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY,
                observed_at TEXT NOT NULL,
                source TEXT NOT NULL,
                sender_profile TEXT,
                raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                platform_id TEXT NOT NULL,
                sender_profile TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'recorded',
                UNIQUE(source, platform_id)
            );
        ''')

    def record(self, snapshot, allowed_profile):
        """Keep observations even without stable message IDs; never guess dedup keys."""
        source = snapshot['source']
        sender = snapshot.get('sender_profile')
        inserted = 0
        with self.db:
            self.db.execute(
                'INSERT INTO observations(observed_at,source,sender_profile,raw_json) VALUES(?,?,?,?)',
                (datetime.now(timezone.utc).isoformat(), source, sender,
                 json.dumps(snapshot, ensure_ascii=False)))
            if not allowed_profile or sender != allowed_profile:
                return {'status': 'unverified_sender', 'inserted': 0}
            for message in snapshot.get('messages', []):
                # Data-index is a virtual-list position, not a stable platform message ID.
                mid = message.get('platform_id')
                if not mid or message.get('direction') != 'incoming':
                    continue
                cur = self.db.execute(
                    'INSERT OR IGNORE INTO messages(source,platform_id,sender_profile,raw_json) VALUES(?,?,?,?)',
                    (source, str(mid), sender, json.dumps(message, ensure_ascii=False)))
                inserted += cur.rowcount
        return {'status': 'observation_saved', 'inserted': inserted}

    def close(self):
        self.db.close()
