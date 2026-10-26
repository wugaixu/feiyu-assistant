"""Phase 1 only: public DOM probe. Never sends messages or launches tasks."""
import argparse
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(r"__RUNTIME_ROOT__")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--login', action='store_true', help='Keep browser open for manual login; press Enter in terminal to close')
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(ROOT / 'edge-profile'), channel='msedge', headless=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto('https://www.douyin.com/', wait_until='domcontentloaded', timeout=45000)
            if args.login:
                input('请手动登录专用小号，打开测试会话；完成后按 Enter。程序不会发送消息：')
            # Inspect only normal visible page content. No cookie/storage export or private API.
            result = {'url': page.url, 'title': page.title(), 'visible_text': page.locator('body').inner_text(timeout=10000)[:16000], 'message_reading': 'unverified', 'sending': 'unverified'}
            (ROOT / 'web-probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print('检查记录已保存至：', str(ROOT / 'web-probe.json'))
        finally:
            context.close()

if __name__ == '__main__':
    main()
