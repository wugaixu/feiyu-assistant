"""Common task admission and safe completion-state reconciliation."""
import json


def claim(db,task_id,expected=None):
    from app.sdk_receiver import OWNER
    with db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT owner,raw_json,status FROM live_tasks WHERE id=?',(task_id,)).fetchone()
        if not row or row[0]!=OWNER or row[2]!='pending':raise ValueError('Verified pending owner task required')
        if expected is not None:
            dest=db.execute('SELECT status,detail FROM chat_dispatch WHERE task_id=?',(task_id,)).fetchone()
            snapshot=json.loads(dest[1]) if dest else {}
            if not dest or dest[0] not in ('attempted','queued') or any(snapshot.get(k)!=v for k,v in expected.items()):
                raise ValueError('Task destination does not match this worker')
        changed=db.execute("UPDATE live_tasks SET status='processing' WHERE id=? AND status='pending' AND NOT EXISTS(SELECT 1 FROM live_tasks WHERE status='processing')",(task_id,)).rowcount
        if not changed:raise ValueError('Task already claimed or another task processing')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='video_rounds'").fetchone():
            db.execute("UPDATE video_rounds SET status='processing' WHERE id=?",(task_id,))
    return row


def reconcile_completed(db):
    """Only repair metadata, never requeue, finish a task or create a message."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_dispatch'").fetchone():return 0
    with db:
        return db.execute("""UPDATE chat_dispatch SET status='processed' WHERE status='queued'
            AND task_id IN (SELECT t.id FROM live_tasks t WHERE t.status IN ('done','needs_input','failed')
            AND EXISTS(SELECT 1 FROM reply_outbox o WHERE o.task_id=t.id))""").rowcount
