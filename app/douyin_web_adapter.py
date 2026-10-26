"""Normal UI operations for the dedicated Playwright session; no private APIs."""
from pathlib import Path
from urllib.parse import urlsplit


def profile_identity(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'www.douyin.com':
        raise ValueError('Unexpected profile origin')
    path = parsed.path.rstrip('/')
    if not path.startswith('/user/') or path == '/user/self':
        raise ValueError('A concrete account profile is required')
    return path


class DouyinWebAdapter:
    def __init__(self, page, allowed_profile):
        self.page = page
        self.allowed_profile = profile_identity(allowed_profile)
        self.script = (Path(__file__).parent / 'dom_probe.js').read_text(encoding='utf-8')

    def verify_active_contact(self):
        # The avatar navigation was verified on the actual popup-chat UI.
        avatar = self.page.locator('[data-stack-layer="chat"] .StackLayoutStackChatHeaderuser [data-apm-action="个人页卡片"]')
        avatar.wait_for(state='visible', timeout=15000)
        if avatar.count() != 1:
            raise ValueError('The verified chat-header layout is missing or ambiguous')
        with self.page.expect_popup(timeout=10000) as event:
            avatar.click()
        popup = event.value
        try:
            popup.wait_for_url('https://www.douyin.com/user/**', timeout=15000)
            actual = profile_identity(popup.url)
            if actual != self.allowed_profile:
                raise ValueError('Contact profile does not match the configured owner')
            return actual
        finally:
            popup.close()

    def read_snapshot(self):
        result = self.page.evaluate(self.script)
        # Verification intentionally separate: callers cannot treat a nickname as identity.
        return result

    def send_fixed_reply(self, text):
        if text != '通信测试：已收到消息，当前仅测试通信，尚未启动本地任务。':
            raise ValueError('Only the fixed prototype reply is supported')
        self.verify_active_contact()
        editor = self.page.locator('[data-e2e="msg-input"] [contenteditable="true"]')
        if editor.count() != 1 or editor.inner_text().strip('\u200b \n'):
            raise ValueError('Message editor is missing or contains an existing draft')
        editor.fill(text)
        # No retry: if click/verification fails, the send outcome must be reviewed locally.
        self.page.locator('.e2e-send-msg-btn').click()
        sent = self.page.locator('.messageMessageBoxisFromMe .TextMessageTextpureText').filter(has_text=text)
        sent.last.wait_for(state='visible', timeout=10000)
        return {'status': 'visible_in_outgoing_history', 'delivery_confirmed': False}
