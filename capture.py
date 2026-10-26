"""Read-only session recorder for a dedicated, manually logged-in Edge profile."""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from app.database import MessageStore
from app.douyin_web_adapter import DouyinWebAdapter

ROOT = Path(r'__RUNTIME_ROOT__')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--auto-start', action='store_true', help='Record read-only page changes while the user logs in manually')
    parser.add_argument('--contact-profile', help='Explicitly configured, user-confirmed owner profile URL')
    parser.add_argument('--contact-title', help='UI selection hint only; profile verification remains mandatory')
    parser.add_argument('--background-test', action='store_true')
    parser.add_argument('--fixed-reply-test-text', help='One-shot prototype reply to this exact test text; requires --contact-profile')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600:
        parser.error('--seconds must be between 1 and 3600')
    if args.fixed_reply_test_text and not args.contact_profile:
        parser.error('--fixed-reply-test-text requires --contact-profile')
    ROOT.mkdir(parents=True, exist_ok=True)
    stop = ROOT / 'capture.stop'
    if stop.exists():
        print('检测到停止标记；如需重新运行，请先手动移除 capture.stop。')
        return
    script = (Path(__file__).parent / 'app' / 'dom_probe.js').read_text(encoding='utf-8')
    store = MessageStore(ROOT / 'assistant.sqlite3')
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(ROOT / 'edge-profile'), channel='msedge', headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.contact_profile or 'https://www.douyin.com/', wait_until='domcontentloaded', timeout=45000)
                if not args.auto_start:
                    input('请手动登录小号并打开测试会话。按 Enter 开始只读记录；不会回复或执行任务：')
                else:
                    print('专用窗口已打开。' + ('固定回复测试已启用；不会执行本地任务。' if args.fixed_reply_test_text else '只读记录已启动。'), flush=True)
                adapter = None
                if args.contact_profile:
                    page.get_by_role('button', name='私信', exact=True).click(timeout=20000)
                    if args.contact_title:
                        contact = page.locator('[data-e2e="conversation-item"]').filter(has_text=args.contact_title)
                        contact.first.wait_for(state='visible', timeout=15000)
                        if contact.count() != 1:
                            raise ValueError('Contact title is ambiguous; select the contact manually')
                        contact.click()
                    adapter = DouyinWebAdapter(page, args.contact_profile)
                    verified_profile = adapter.verify_active_contact()
                    print('已通过会话头像正常跳转核验主账号：', verified_profile, flush=True)
                if args.background_test:
                    background_tab = context.new_page()
                    background_tab.goto('about:blank')
                    background_tab.bring_to_front()
                    print('已切到空白标签，继续观察原抖音标签。', flush=True)
                deadline = time.monotonic() + args.seconds
                previous = None
                while time.monotonic() < deadline and not stop.exists():
                    candidates = [p for p in context.pages if not p.is_closed() and p.url.startswith('https://www.douyin.com/')]
                    if not candidates:
                        print('页面已关闭，停止记录。')
                        break
                    # Follow a normal chat popup opened by the user in this dedicated context.
                    if not args.background_test:
                        page = candidates[-1]
                    snapshot = page.evaluate(script)
                    comparison = dict(snapshot)
                    comparison.pop('observed_at', None)
                    fingerprint = json.dumps(comparison, ensure_ascii=False, sort_keys=True)
                    if fingerprint != previous:
                        store.record(snapshot, allowed_profile=None)
                        print('已记录页面变化：', snapshot.get('status', 'panel_present'))
                        previous = fingerprint
                    if args.fixed_reply_test_text and adapter:
                        matches = [m for m in snapshot.get('messages', []) if m.get('raw_text') == args.fixed_reply_test_text and m.get('direction') != 'outgoing']
                        if matches:
                            key = adapter.allowed_profile + ':' + args.fixed_reply_test_text
                            store.db.execute('CREATE TABLE IF NOT EXISTS prototype_replies (test_key TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT)')
                            store.db.commit()
                            adapter.verify_active_contact()
                            with store.db:
                                reserved = store.db.execute('INSERT OR IGNORE INTO prototype_replies VALUES(?, ?, ?)', (key, 'attempted', None)).rowcount
                            if reserved:
                                try:
                                    outcome = adapter.send_fixed_reply('通信测试：已收到消息，当前仅测试通信，尚未启动本地任务。')
                                    with store.db:
                                        store.db.execute('UPDATE prototype_replies SET status=?,detail=? WHERE test_key=?', ('visible_in_history', json.dumps(outcome), key))
                                    page.screenshot(path=str(ROOT / 'reply-proof.png'))
                                    print('固定回复已在发出记录中确认；不会重试或执行任务。', flush=True)
                                except Exception as exc:
                                    with store.db:
                                        store.db.execute('UPDATE prototype_replies SET status=?,detail=? WHERE test_key=?', ('outcome_uncertain', str(exc), key))
                                    print('回复结果未确认，已记录并停止重试。', flush=True)
                            args.fixed_reply_test_text = None
                    page.wait_for_timeout(2000)
            except Exception as exc:
                (ROOT / 'capture-error.json').write_text(json.dumps({
                    'error': str(exc), 'url': page.url,
                    'visible_text': page.locator('body').inner_text(timeout=5000)[:16000]
                }, ensure_ascii=False, indent=2), encoding='utf-8')
                raise
            finally:
                context.close()
    finally:
        store.close()


if __name__ == '__main__':
    main()
