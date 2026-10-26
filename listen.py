"""Long-lived, single-instance observation service. Never runs remote commands."""
import json
import msvcrt
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from app.database import MessageStore
from app.douyin_web_adapter import DouyinWebAdapter
from app.task_inbox import TaskInbox

ROOT = Path(r'__RUNTIME_ROOT__')
OWNER = 'https://www.douyin.com__OWNER_PROFILE__'


def status(state, **fields):
    target = ROOT / 'listener-status.json'
    pending = ROOT / 'listener-status.tmp'
    pending.write_text(json.dumps(dict(state=state, updated_at=time.time(), **fields), ensure_ascii=False), encoding='utf-8')
    pending.replace(target)


def main():
    from app.local_runtime import enabled as local_enabled
    if local_enabled():
        from app.sdk_receiver import main as local_main
        return local_main()
    ROOT.mkdir(parents=True, exist_ok=True)
    lock = (ROOT / 'listener.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    store = MessageStore(ROOT / 'assistant.sqlite3')
    inbox = TaskInbox(store.db)
    stop = ROOT / 'capture.stop'
    try:
        if stop.exists():
            status('stopped', reason='stop_marker')
            return
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(ROOT / 'edge-profile'), channel='msedge', headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(OWNER, wait_until='domcontentloaded', timeout=45000)
                previous = None
                token = None
                token_expiry = 0
                while not stop.exists():
                    if local_enabled():
                        page.wait_for_timeout(10000)
                        continue
                    try:
                        if page.is_closed():
                            status('stopped', reason='browser_closed')
                            break
                        if page.locator('[id^="login-full-panel"]').is_visible():
                            status('waiting_manual_login', replies_enabled=False, execution_enabled=False)
                            page.wait_for_timeout(5000)
                            continue
                        header = page.locator('[data-stack-layer="chat"] .StackLayoutStackChatHeaderuser')
                        if not header.is_visible():
                            page.get_by_role('button', name='私信', exact=True).click(timeout=5000)
                            contacts = page.locator('[data-e2e="conversation-item"]').filter(has_text='__OWNER_NICKNAME__')
                            contacts.first.wait_for(state='visible', timeout=5000)
                            if contacts.count() != 1:
                                raise ValueError('Ambiguous owner selection')
                            contacts.click(timeout=5000)
                        adapter = DouyinWebAdapter(page, OWNER)
                        owner = adapter.verify_active_contact()
                        snapshot = adapter.read_snapshot()
                        # Recheck contact after reading; never rely on nickname.
                        if adapter.verify_active_contact() != owner:
                            raise ValueError('Contact changed while reading')
                        snapshot['sender_profile'] = owner
                        comparison = dict(snapshot)
                        comparison.pop('observed_at', None)
                        fingerprint = json.dumps(comparison, ensure_ascii=False, sort_keys=True)
                        if fingerprint != previous:
                            store.record(snapshot, allowed_profile=None)
                            accepted = inbox.observe(snapshot, owner)
                            if accepted:
                                # Reserve before sending; ambiguous outcomes never retry.
                                store.db.execute('''CREATE TABLE IF NOT EXISTS inbox_acknowledgements
                                    (token TEXT PRIMARY KEY, status TEXT NOT NULL)''')
                                store.db.commit()
                                with store.db:
                                    rows = store.db.execute('''SELECT token FROM task_proposals
                                        WHERE token NOT IN (SELECT token FROM inbox_acknowledgements)''').fetchall()
                                    for row in rows:
                                        store.db.execute('INSERT INTO inbox_acknowledgements VALUES(?,?)', (row[0], 'attempted'))
                                if rows:
                                    try:
                                        adapter.send_fixed_reply('通信测试：已收到消息，当前仅测试通信，尚未启动本地任务。')
                                        with store.db:
                                            store.db.executemany('UPDATE inbox_acknowledgements SET status=? WHERE token=?',
                                                                 [('visible_in_history', row[0]) for row in rows])
                                    except Exception:
                                        with store.db:
                                            store.db.executemany('UPDATE inbox_acknowledgements SET status=? WHERE token=?',
                                                                 [('outcome_uncertain', row[0]) for row in rows])
                            previous = fingerprint
                        if time.time() >= token_expiry:
                            token = inbox.issue(owner)
                            token_expiry = time.time() + 1800
                        status('listening', task_token=token, token_expires=token_expiry,
                               replies_enabled='proposal_acknowledgement_only', execution_enabled=False)
                        page.wait_for_timeout(10000)
                    except Exception as exc:
                        status('waiting_recovery', reason=str(exc)[:1500], replies_enabled=False, execution_enabled=False)
                        if page.is_closed():
                            break
                        page.wait_for_timeout(15000)
            finally:
                context.close()
    finally:
        store.close()
        lock.close()


if __name__ == '__main__':
    from app.live_service import install
    install()
    main()
