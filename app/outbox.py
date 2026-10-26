"""Single ordered outbound queue for text, images and share cards.

The old split tables (reply_outbox text rows / image_outbox / share_outbox /
reply_batch_items) are migrated into one reply_outbox table with a `kind`,
`seq` and payload columns.  Migration is idempotent and runs before any sender
starts, so pending rows and their order are preserved.
"""
import sqlite3
import json
import time

SUCCESS=('server_accepted','visible_in_history','confirmed')
CREATE='''CREATE TABLE IF NOT EXISTS reply_outbox(
    id TEXT PRIMARY KEY, task_id TEXT, owner TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', detail TEXT,
    kind TEXT NOT NULL DEFAULT 'text', seq INTEGER NOT NULL DEFAULT 0,
    path TEXT, sha256 TEXT, work_json TEXT)'''
ADD_COLUMNS=(('kind',"TEXT NOT NULL DEFAULT 'text'"),('seq','INTEGER NOT NULL DEFAULT 0'),
             ('path','TEXT'),('sha256','TEXT'),('work_json','TEXT'))

def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

def _copy_legacy(db):
    tables=_tables(db)
    if 'image_outbox' in tables:
        db.execute("INSERT OR IGNORE INTO reply_outbox(id,task_id,owner,text,status,detail,kind,seq,path,sha256) "
                   "SELECT id,task_id,owner,'',status,detail,'image',0,path,sha256 FROM image_outbox")
    if 'share_outbox' in tables:
        db.execute("INSERT OR IGNORE INTO reply_outbox(id,task_id,owner,text,status,detail,kind,seq,work_json) "
                   "SELECT id,task_id,owner,'',status,detail,'share',0,work_json FROM share_outbox")
    if 'reply_batch_items' in tables:
        db.execute("UPDATE reply_outbox SET seq=COALESCE((SELECT b.seq FROM reply_batch_items b "
                   "WHERE b.outbox_id=reply_outbox.id),seq)")
    if 'reply_batch_items' in tables:db.execute('DROP TABLE reply_batch_items')
    if 'image_outbox' in tables:db.execute('DROP TABLE image_outbox')
    if 'share_outbox' in tables:db.execute('DROP TABLE share_outbox')

def schema(db):
    """Create and migrate the unified queue; safe to call repeatedly."""
    if 'reply_outbox' not in _tables(db):
        db.execute(CREATE)
    else:
        columns={r[1] for r in db.execute('PRAGMA table_info(reply_outbox)')}
        for name,decl in ADD_COLUMNS:
            if name not in columns:db.execute(f'ALTER TABLE reply_outbox ADD COLUMN {name} {decl}')
    _copy_legacy(db)
    db.execute('CREATE INDEX IF NOT EXISTS reply_outbox_task_seq ON reply_outbox(task_id,seq)')
    db.execute('CREATE INDEX IF NOT EXISTS reply_outbox_owner_status ON reply_outbox(owner,status)')
    db.commit()

def allowed(db,key):
    current=db.execute('SELECT task_id,seq FROM reply_outbox WHERE id=?',(key,)).fetchone()
    if not current:return True
    for status, in db.execute('SELECT status FROM reply_outbox WHERE task_id=? AND seq<? AND id<>? ORDER BY seq',
                              (current[0],current[1],key)):
        if status not in (*SUCCESS,'outcome_uncertain'):return False
    return True


def recover_interrupted(db,now=None,timeout=300):
    """Close abandoned attempts without ever rearming a send or inventing success."""
    now=time.time() if now is None else now
    if 'outbound_attempts' not in _tables(db):return 0
    recovered=0
    db.execute('CREATE TABLE IF NOT EXISTS outbound_recovery_observations(outbox_id TEXT PRIMARY KEY,observed REAL NOT NULL)');db.commit()
    with db:
        db.execute('BEGIN IMMEDIATE')
        # Legacy senders may have reserved an image without an attempt journal.
        # Start an observation window instead of inventing a historical timestamp.
        db.execute("INSERT OR IGNORE INTO outbound_recovery_observations SELECT o.id,? FROM reply_outbox o WHERE o.status='attempted' AND NOT EXISTS(SELECT 1 FROM outbound_attempts a WHERE a.outbox_id=o.id)",(now,))
        orphans=db.execute("SELECT o.id,o.detail FROM reply_outbox o JOIN outbound_recovery_observations r ON r.outbox_id=o.id WHERE o.status='attempted' AND r.observed<=? AND NOT EXISTS(SELECT 1 FROM outbound_attempts a WHERE a.outbox_id=o.id)",(now-timeout,)).fetchall()
        for key,detail in orphans:
            try:value=json.loads(detail or '{}')
            except (ValueError,TypeError):value={}
            if not isinstance(value,dict):value={}
            value.update(result='outcome_uncertain',reason='missing_attempt_journal_after_observation',automatic_retry=False,recovered_at=now,
                         batch_blocked='Legacy attempt has no receipt journal; no replay, following parts may continue.')
            recovered+=db.execute("UPDATE reply_outbox SET status='outcome_uncertain',detail=? WHERE id=? AND status='attempted'",(json.dumps(value,ensure_ascii=False),key)).rowcount
        rows=db.execute("SELECT o.id,o.detail,a.id,a.started,a.finished FROM reply_outbox o JOIN outbound_attempts a ON a.id=(SELECT a2.id FROM outbound_attempts a2 WHERE a2.outbox_id=o.id ORDER BY a2.started DESC LIMIT 1) WHERE o.status='attempted' AND a.finished IS NULL AND a.started<=?",(now-timeout,)).fetchall()
        for key,detail,attempt,started,finished in rows:
            try:value=json.loads(detail or '{}')
            except (ValueError,TypeError):value={}
            if not isinstance(value,dict):value={}
            value.update(result='outcome_uncertain',reason='interrupted_send_timeout',automatic_retry=False,
                         batch_blocked='Previous attempt has no final receipt; subsequent parts may continue without replaying it.',recovered_at=now)
            encoded=json.dumps(value,ensure_ascii=False)
            recovered+=db.execute("UPDATE reply_outbox SET status='outcome_uncertain',detail=? WHERE id=? AND status='attempted'",(encoded,key)).rowcount
            db.execute("UPDATE outbound_attempts SET finished=?,status='outcome_uncertain',detail=? WHERE id=? AND finished IS NULL",(now,encoded,attempt))
    return recovered
