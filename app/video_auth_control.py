"""One-shot SMS request explicitly authorized in the local Codex conversation."""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(r'__RUNTIME_ROOT__')


def tick(context):
    verify_tick(context)
    probe = ROOT / 'video-auth-probe.request'
    if probe.exists():
        probe.unlink()
        page = getattr(context, '_local_video_worker_page', None)
        if page is not None and not page.is_closed():
            from app.upstream_observation import observe, compact
            state = compact(observe(page))
            state['drafts'] = page.locator('.comment-input-inner-container [contenteditable="true"]').all_inner_texts()
            state['comment_rows'] = page.locator('[data-e2e="comment-item"]').evaluate_all('(es)=>es.slice(0,30).map(e=>({text:e.innerText.slice(0,800),id:e.getAttribute("data-id")||e.getAttribute("data-comment-id"),self:e.getAttribute("data-is-self")}))')
            (ROOT / 'video-auth-probe.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    request = ROOT / 'video-sms-request.json'
    if not request.exists():
        return
    attempted = ROOT / 'video-sms-attempted.json'
    result_path = ROOT / 'video-sms-result.json'
    # Reserve before any UI action. Even a crash cannot cause an automatic retry.
    if attempted.exists():
        return
    request.replace(attempted)
    result = {'status': 'not_sent', 'time': time.time(), 'automatic_retry': False}
    try:
        data = json.loads(attempted.read_text(encoding='utf-8-sig'))
        if data != {'action': 'request_sms', 'authorization': 'local-user-2026-10-23'}:
            raise ValueError('Unknown local authorization')
        page = getattr(context, '_local_video_worker_page', None)
        if page is None or page.is_closed() or urlsplit(page.url).netloc != 'www.douyin.com':
            raise ValueError('Existing Douyin video page unavailable')
        if not (ROOT / 'video-auth-required.json').exists():
            raise ValueError('No recorded authentication challenge')
        field = page.locator('input[placeholder="请输入验证码"]:visible')
        heading = page.get_by_text('接收短信验证码', exact=True)
        button = page.get_by_text('获取验证码', exact=True)
        if field.count() != 1 or heading.count() != 1 or not heading.is_visible() or button.count() != 1 or not button.is_visible():
            raise ValueError('Expected SMS challenge is not currently visible; no click performed')
        result['status'] = 'request_outcome_uncertain'
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        button.click(timeout=5000)
        page.wait_for_timeout(1800)
        text = page.locator('body').inner_text()
        import re
        sent = bool(re.search(r'重新获取.{0,10}\d|\d+\s*[sS秒]|验证码已发送|发送成功', text))
        result['status'] = 'sent_ui_confirmed' if sent else 'request_outcome_uncertain'
        page.screenshot(path=str(ROOT / 'video-sms-after-request.png'))
    except Exception as exc:
        result['error'] = str(exc)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def verify_tick(context):
    secret = ROOT / 'video-sms-code-once.json'
    if not secret.exists():
        return
    data = json.loads(secret.read_text(encoding='utf-8-sig'))
    secret.unlink()
    audit = ROOT / 'video-sms-verification.json'
    if audit.exists():
        return
    result = {'status': 'attempted', 'time': time.time(), 'automatic_retry': False}
    audit.write_text(json.dumps(result), encoding='utf-8')
    try:
        import re
        code = data.get('code', '')
        if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
            raise ValueError('Expected user-supplied six-digit code')
        page = getattr(context, '_local_video_worker_page', None)
        if page is None or page.is_closed() or urlsplit(page.url).netloc != 'www.douyin.com':
            raise ValueError('Existing authentication page unavailable')
        field = page.locator('input[placeholder="请输入验证码"]:visible')
        button = page.get_by_text('验证', exact=True)
        if field.count() != 1 or button.count() != 1 or not button.is_visible():
            raise ValueError('Original SMS challenge is no longer unambiguous')
        field.fill(code)
        button.click(timeout=5000)
        page.wait_for_timeout(1800)
        result['status'] = 'challenge_closed' if field.count() == 0 else 'verification_not_completed'
        # Keep the browsing guard until the prior comment outcome is reconciled.
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        data.clear()
    audit.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
