"""On-demand, hidden real-browser comment publishing (single transport).

The dedicated Edge profile (already logged in as the assistant account) is
launched only while a comment is being published, hidden immediately, and
closed right after. There is no always-on browser process and no HTTP
publishing path: the comment is typed and submitted through the normal web
comment editor, with a real owner mention created by the platform's own
suggestion list. Nothing here signs or replays a private write API.
"""
import json
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit, parse_qs

from app.live_service import ROOT
from app.sdk_receiver import OWNER, OWNER_UID, ACCOUNT_UID

PROFILE = ROOT / 'edge-profile'
OWNER_SEC_UID = OWNER.rsplit('/', 1)[-1]
OWNER_NICKNAME = '__OWNER_NICKNAME__'
ATLIST_PATH = '/aweme/v1/web/familiar/atlist/'
PUBLISH_PATH = '/aweme/v1/web/comment/publish/'
COMMENT_LIST_PATH = '/aweme/v1/web/comment/list/'
WINDOW_HIDE_TIMEOUT = 8.0


def target_paths(video_id, kind='video'):
    """Canonical work URL first, the other route as a fallback."""
    if kind in ('image_gallery', 'note', 'image'):
        return ['/note/' + video_id, '/video/' + video_id]
    return ['/video/' + video_id, '/note/' + video_id]


def page_on_target(url, video_id):
    """True only when the loaded page path is the requested work itself."""
    parsed=urlsplit(str(url or ''))
    if parsed.scheme!='https' or parsed.hostname!='www.douyin.com':return False
    path = parsed.path.rstrip('/')
    return path in (f'/video/{video_id}', f'/note/{video_id}')


def work_identity_diagnostic(page,video_id):
    """Public DOM only; hydration may include feed items, so record, don't gate."""
    parsed=urlsplit(str(page.url));query=parse_qs(parsed.query)
    query_ids={key:query[key] for key in ('aweme_id','modal_id','item_id') if key in query}
    ids=[];error=None
    try:
        raw=page.locator('script#RENDER_DATA').text_content(timeout=1000) if page.locator('script#RENDER_DATA').count() else None
        value=json.loads(unquote(raw)) if raw else {}
        def walk(node,depth=0):
            if depth>20:return
            if isinstance(node,dict):
                for key,child in node.items():
                    if key in ('aweme_detail','awemeDetail','itemInfo') and isinstance(child,dict):
                        identity=str(child.get('aweme_id') or child.get('awemeId') or child.get('id') or '')
                        if identity.isdigit() and identity not in ids:ids.append(identity)
                    if isinstance(child,(list,dict)):walk(child,depth+1)
            elif isinstance(node,list):
                for child in node[:100]:walk(child,depth+1)
        walk(value)
    except Exception as exc:error=type(exc).__name__
    return {'requested_id':str(video_id),'path':parsed.path,'query_work_ids':query_ids,
            'render_work_ids':ids[:20],'render_error':error,'diagnostic_only':True}


def _our_msedge_pids():
    """Pids of the dedicated-profile Edge browser processes (root only)."""
    command = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
               "Where-Object { $_.CommandLine -match 'edge-profile' -and $_.CommandLine -notmatch '--type=' } | "
               "Select-Object -ExpandProperty ProcessId")
    try:
        result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        return []
    return [int(value) for value in result.stdout.split() if value.strip().isdigit()]


def hide_dedicated_windows():
    """Hide the dedicated browser's window without touching any other Edge."""
    from app.window_visibility import Native
    native = Native()
    hidden = []
    deadline = time.time() + WINDOW_HIDE_TIMEOUT
    while time.time() < deadline:
        for pid in _our_msedge_pids():
            try:
                windows = native.windows(pid)
            except Exception:
                continue
            for window in windows:
                if window['visible']:
                    native.show(window['hwnd'], False)
                    hidden.append(window['hwnd'])
        if hidden:
            time.sleep(0.3)
            break
        time.sleep(0.4)
    return hidden


def avatar_resource(url):
    """CDN-path identity of an avatar URL, independent of size/CDN prefix."""
    if not isinstance(url, str) or not url:
        return None
    tail = url.split('?')[0].split('/')[-1]
    return tail.split('~')[0].split('.')[0] or None


def owner_identity_from_atlist(value):
    """Exact owner entry from the platform's own @-suggestion response."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if str(node.get('sec_uid')) == OWNER_SEC_UID and node.get('uid'):
                thumb = node.get('avatar_thumb') or {}
                urls = thumb.get('url_list') or []
                found.append({'uid': str(node.get('uid')), 'sec_uid': node.get('sec_uid'),
                              'nickname': node.get('nickname'), 'avatar': urls[0] if urls else None})
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return next((entry for entry in found if str(entry['uid']) == OWNER_UID and entry['nickname']), None)


def select_verified_owner(candidate,editor,nickname,page=None):
    """Verify the hit target, click once, then wait for the real mention entity.

    Suggestion rows re-render while being selected. A locator click may keep
    waiting on a detached node after dispatch. The browser's normal pointer
    input avoids that wait; an obscured/off-screen target is never forced.
    """
    from playwright.sync_api import TimeoutError as BrowserTimeout,Error as BrowserError
    def selected():
        words=editor.locator('.douyin_mention_word')
        return words.count()==1 and words.inner_text().strip()=='@'+nickname
    def await_selected():
        deadline=time.monotonic()+3
        while True:
            if selected():return True
            if page is None or time.monotonic()>=deadline:return False
            page.wait_for_timeout(100)
    try:
        if page is None:candidate.click(timeout=5000)
        else:
            # Do not ask actionability/scroll to wait for a stable row: this list
            # replaces its nodes while displaying same-name suggestions.
            deadline=time.monotonic()+3;scrolled=False
            while True:
                try:
                    hit=candidate.evaluate('''e=>{const r=e.getBoundingClientRect();
                        const x=r.x+r.width/2,y=r.y+r.height/2;
                        const top=document.elementFromPoint(x,y);
                        return {x,y,ok:r.width>0&&r.height>0&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&!!top&&(top===e||e.contains(top))};}''',timeout=1500)
                    if hit.get('ok') or scrolled:break
                    # The mention popup may clip a matching same-name row. Use
                    # the normal DOM scrolling operation without a stable-node
                    # actionability wait, then resolve the verified locator anew.
                    candidate.evaluate('e=>e.scrollIntoView({block:"center",inline:"center",behavior:"instant"})',timeout=1500)
                    scrolled=True;page.wait_for_timeout(150)
                except BrowserError as exc:
                    if not any(s in str(exc).lower() for s in ('detached','not attached')) or time.monotonic()>=deadline:raise
                    page.wait_for_timeout(100)  # Re-resolve before any input.
            if not hit.get('ok'):raise ValueError('Verified owner candidate is obscured or outside viewport')
            page.mouse.click(hit['x'],hit['y'])
            if not await_selected():raise ValueError('Owner click did not create a verified mention; no second click')
        return False
    except BrowserTimeout:
        if await_selected():return True
        raise


def verified_owner_candidate(page,owner):
    """Prefer account links, otherwise bind avatar AND exact name to verified atlist UID."""
    if str(owner.get('uid'))!=OWNER_UID or owner.get('sec_uid')!=OWNER_SEC_UID:
        raise ValueError('Unverified mention target')
    resource=avatar_resource(owner.get('avatar'))
    if not resource:raise ValueError('Owner avatar identity unavailable')
    rows=page.locator('.QLIVVPgY:visible')
    names=page.get_by_text(owner['nickname'],exact=True)
    # Prefer a genuine account link when the website exposes it in the row.
    identity_rows=rows.filter(has=page.locator('a[href*="/user/'+OWNER_SEC_UID+'"]')).filter(has=names)
    if identity_rows.count()==1:return identity_rows.first,'account_link'
    matching=rows.filter(has=page.locator('img[src*="'+resource+'"]')).filter(has=names)
    if matching.count()!=1:raise ValueError('Unique verified owner mention candidate unavailable')
    return matching.first,'verified_avatar_and_exact_name'


class HiddenCommentBrowser:
    """Context manager: hidden dedicated Edge, closed on exit."""

    def __init__(self, viewport=None):
        self.viewport = viewport or {'width': 1280, 'height': 900}
        self._playwright = None
        self.context = None
        self.page = None
        self.hidden_windows = []
        self.last_diagnostic = {}

    def __enter__(self):
        from app.comment_profile import ProfileLease,ProfileBusy
        self._owned_lease=None
        if not getattr(self,'_external_lease',None):self._owned_lease=ProfileLease().__enter__()
        try:
            if _our_msedge_pids():raise ProfileBusy('已有专用抖音窗口占用此登录资料；请在原窗口处理，不会另开同资料浏览器')
            return self._open()
        except Exception:
            self.close();raise

    def _open(self):
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self.context = self._playwright.chromium.launch_persistent_context(
            str(PROFILE), channel='msedge', headless=False, viewport=self.viewport,
            args=['--no-first-run', '--no-default-browser-check', '--disable-features=msEdgeSidebarV2'])
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.hidden_windows = hide_dedicated_windows()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        try:
            if self.context is not None:
                self.context.close()
        except Exception:
            pass
        lease=getattr(self,'_owned_lease',None)
        if lease is not None:
            self._owned_lease=None;lease.__exit__(None,None,None)
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass

    # -- identity ---------------------------------------------------------
    def self_identity(self):
        """Logged-in account, read from the page's own rendered state (no HTTP)."""
        self.page.goto('https://www.douyin.com/user/self', wait_until='domcontentloaded', timeout=45000)
        self.page.wait_for_timeout(4000)
        return self.read_identity()

    def read_identity(self):
        """Read current rendered state without disrupting an active login form."""
        title = self.page.title()
        if '验证' in title or self.page.locator('[id^="login-full-panel"]:visible').count():
            raise ValueError('Manual login or verification required')
        nickname = title.split('的抖音')[0].strip() if '的抖音' in title else ''
        raw = self.page.evaluate(
            "()=>{const e=document.getElementById('RENDER_DATA');return e?e.textContent:null;}")
        uid = None
        try:
            data = json.loads(unquote(raw or ''))
            uid = str(((data.get('app') or {}).get('odin') or {}).get('user_id') or '') or None
        except (ValueError, TypeError):
            uid = None
        if not uid or uid=='0':
            raise ValueError('Logged-in account id unavailable from page state')
        return {'status_code': 0, 'user_uid': uid, 'nickname': nickname}

    def show_for_login(self):
        from app.window_visibility import Native
        native=Native()
        for pid in _our_msedge_pids():
            for window in native.windows(pid):native.show(window['hwnd'],True)
        self.page.bring_to_front()

    # -- publish ----------------------------------------------------------
    def publish(self, video_id, text, submit=True, mention_owner=True, kind='video'):
        """Type, optionally mention the verified owner, submit, return the receipt.

        With ``submit=False`` the flow runs fully but stops before the send
        control is clicked (used for read-only rehearsal/diagnostics).
        With ``mention_owner=False`` the reference to the owner is skipped and
        only the plain comment text is submitted.
        """
        self.last_diagnostic={'phase':'identity','submitted':False}
        me = self.self_identity()
        if me.get('user_uid') != ACCOUNT_UID:
            raise ValueError('Publishing account is not the verified assistant account')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Comment text required')
        page = self.page
        captured = {'atlist': None, 'publish': None, 'publish_url': None}

        def on_target():
            return page_on_target(page.url, video_id)

        def observed(response):
            path = urlsplit(response.url).path
            if '/comment/publish' in path:
                captured['publish_url'] = response.url.split('?')[0]
                try:
                    captured['publish'] = response.json()
                except Exception:
                    try:
                        captured['publish'] = json.loads(response.text())
                    except Exception:
                        pass
                return
            if ATLIST_PATH in path:
                try:
                    captured['atlist'] = response.json()
                except Exception:
                    pass
        page.on('response', observed)
        try:
            self.last_diagnostic['phase']='open_work'
            opened=False
            attempts=[]
            for path in target_paths(video_id, kind):
                attempts.append(path)
                page.goto('https://www.douyin.com' + path, wait_until='domcontentloaded', timeout=45000)
                page.locator('.comment-input-inner-container, [data-e2e="comment-list"], video').first.wait_for(state='attached',timeout=10000)
                if on_target():
                    opened=True
                    break
            self.last_diagnostic['work_paths']=attempts
            if not opened:
                raise ValueError('Work page did not open the requested video; refusing to comment')
            self.last_diagnostic['target_url']=page.url
            self.last_diagnostic['work_identity_loaded']=work_identity_diagnostic(page,video_id)
            page.keyboard.press('Escape')
            page.wait_for_timeout(500)
            if not on_target():
                raise ValueError('Page left the requested work before commenting; refusing to comment')
            self.last_diagnostic['phase']='open_comment_editor'
            editor = _open_comment_editor(page)
            self.last_diagnostic['editor_opened']=True
            self.last_diagnostic['work_identity_before_type']=work_identity_diagnostic(page,video_id)
            if editor.inner_text().strip('\u200b \t\r\n'):
                raise ValueError('Existing comment draft; no overwrite')
            if not on_target():
                raise ValueError('Page left the requested work before typing; comment not submitted')
            self.last_diagnostic['phase']='type_comment'
            editor.click(timeout=5000)
            editor.press_sequentially(text + ' ', delay=20)
            page.wait_for_timeout(600)
            if mention_owner:
                self.last_diagnostic['phase']='owner_suggestions'
                editor.press_sequentially('@' + OWNER_NICKNAME, delay=80)
                deadline = time.time() + 12
                while not captured['atlist'] and time.time() < deadline:
                    page.wait_for_timeout(400)
                owner = owner_identity_from_atlist(captured['atlist'])
                if not owner:
                    raise ValueError('Owner is absent from the verified @-suggestion response')
                candidate,method=verified_owner_candidate(page,owner)
                self.last_diagnostic['phase']='select_owner'
                self.last_diagnostic['owner_match_method']=method
                name_target=candidate.get_by_text(owner['nickname'],exact=True)
                click_target=name_target if name_target.count()==1 else candidate
                self.last_diagnostic['owner_selection_input']='verified_hit_test_single_pointer'
                recovered=select_verified_owner(click_target,editor,owner['nickname'],page=page)
                if recovered:self.last_diagnostic['selection_timeout_entity_verified']=True
                page.wait_for_timeout(1200)
                words = editor.locator('.douyin_mention_word')
                if words.count() != 1 or words.inner_text().strip() != '@' + owner['nickname']:
                    raise ValueError('Real owner mention entity was not created')
                editor_text = editor.inner_text()
            else:
                self.last_diagnostic['phase']='plain_comment'
                self.last_diagnostic['mention_owner']=False
                owner = None
                editor_text = text
            button = page.locator('.comment-input-inner-container:visible .commentInput-right-ct '
                                  'span:has(svg path[d^="M17.5 30"])')
            if button.count() != 1 or not button.is_visible():
                raise ValueError('Verified publish control missing')
            if not on_target():
                raise ValueError('Page left the requested work before submit; comment not submitted')
            self.last_diagnostic['target_url_before_submit']=page.url
            self.last_diagnostic['work_identity_before_submit']=work_identity_diagnostic(page,video_id)
            if not submit:
                self.last_diagnostic = {'submitted': False,
                                        **({'owner': owner['uid']} if owner else {'mention_owner': False}),
                                        'editor_text': editor_text}
                return {'status_code': None, 'comment': None,
                        **({'owner_identity': owner} if owner else {}),
                        'editor_text': editor_text, 'rehearsed': True}
            self.last_diagnostic.update(phase='click_publish',submitted=True)
            button.click(timeout=5000)
            self.last_diagnostic['phase']='publish_receipt'
            deadline = time.time() + 12
            while not captured['publish'] and time.time() < deadline:
                if not editor.inner_text().strip('\u200b \t\r\n'):
                    page.wait_for_timeout(300)
                    break
                page.wait_for_timeout(300)
            receipt = captured['publish']
            if not isinstance(receipt, dict):
                # The submit click was made but no publish response was observed
                # (some builds post without an interceptable JSON reply). Return
                # an empty receipt so the caller resolves via a readback instead
                # of discarding a possibly-successful publish.
                self.last_diagnostic.update(submitted=True,publish_response='missing',publish_url=captured['publish_url'])
                return {'status_code': None, 'comment': None,
                        **({'owner_identity': owner} if owner else {}),
                        'editor_text': editor_text, 'publish_response': 'missing'}
            receipt = dict(receipt)
            if owner:
                receipt['owner_identity'] = {'uid': owner['uid'], 'sec_uid': owner['sec_uid'],
                                             'nickname': owner['nickname']}
            receipt['editor_text'] = editor_text
            self.last_diagnostic.update(status_code=receipt.get('status_code'),has_comment=isinstance(receipt.get('comment'),dict))
            return receipt
        except Exception as exc:
            self.last_diagnostic.update(error_class=type(exc).__name__,error=str(exc)[:1200])
            raise
        finally:
            page.remove_listener('response', observed)

    # -- readback ---------------------------------------------------------
    def comments(self, video_id,kind='video'):
        """First-page comment list, captured from the page's own request."""
        page = self.page
        holder = {'value': None}

        def observed(response):
            response_url=urlsplit(response.url)
            if '/comment/list' not in response_url.path or parse_qs(response_url.query).get('aweme_id')!=[str(video_id)]:
                return
            try:
                holder['value'] = response.json()
            except Exception:
                pass
        page.on('response', observed)
        try:
            page.goto('https://www.douyin.com' + target_paths(video_id,kind)[0], wait_until='domcontentloaded', timeout=45000)
            deadline = time.time() + 10
            while not holder['value'] and time.time() < deadline:
                page.wait_for_timeout(400)
        finally:
            page.remove_listener('response', observed)
        if not isinstance(holder['value'], dict):
            raise ValueError('Comment readback unavailable from page')
        return holder['value']


def _click_panel_tab(page, label='评论', min_x=900):
    locator = page.get_by_text(label, exact=False)
    for index in range(min(locator.count(), 10)):
        element = locator.nth(index)
        try:
            if not element.is_visible():
                continue
            box = element.bounding_box()
            if not box or box['x'] < min_x:
                continue
            element.click(timeout=4000)
            return True
        except Exception:
            continue
    return False


def _open_comment_editor(page):
    editors = page.locator('.public-DraftEditor-content:visible')
    if editors.count():
        if editors.count()!=1:raise ValueError('Ambiguous visible comment editors')
        return editors.first
    # The current website mounts DraftEditor only AFTER its placeholder is
    # clicked. Waiting for DraftEditor before that click always times out.
    containers = page.locator('.comment-input-inner-container:visible')
    if containers.count() == 0:
        # Hidden pre-rendered comment lists can exist before the actual tab is
        # interactive. Wait for that tab instead of treating attached DOM as ready.
        deadline=time.monotonic()+10
        while not containers.count():
            if _click_panel_tab(page):break
            if time.monotonic()>=deadline:raise ValueError('Comment panel tab unavailable')
            page.wait_for_timeout(200)
        containers.first.wait_for(state='visible',timeout=8000)
    if containers.count()!=1:raise ValueError('Ambiguous visible comment input containers')
    # Scope to the actual input, never a comment containing the same words.
    placeholder = containers.first.get_by_text('留下你的精彩评论吧', exact=True)
    if not editors.count():
        placeholder.wait_for(state='visible',timeout=8000)
        placeholder.click(timeout=4000)
    editor = editors.first
    editor.wait_for(state='visible', timeout=8000)
    if editors.count()!=1:raise ValueError('Ambiguous visible comment editors')
    return editor


def probe(video_id='7695340795881362426',kind='video'):
    """Read-only check: hidden launch, login state, editor presence."""
    report = {'video_id': video_id}
    with HiddenCommentBrowser() as browser:
        report['hidden_windows'] = browser.hidden_windows
        report['self'] = browser.self_identity()
        browser.page.goto('https://www.douyin.com' + target_paths(video_id,kind)[0], wait_until='domcontentloaded', timeout=45000)
        browser.page.wait_for_timeout(5000)
        report['url'] = browser.page.url
        report['editor_count'] = browser.page.locator('.comment-input-inner-container').count()
        browser.page.screenshot(path=str(ROOT / 'browser-publish-probe.png'))
    return report


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'probe'
    if action == 'probe':
        print(json.dumps(probe(*sys.argv[2:3]), ensure_ascii=False, indent=2))
    elif action == 'identify':
        with HiddenCommentBrowser() as browser:
            print(json.dumps(browser.self_identity(), ensure_ascii=False, indent=2))
    else:
        raise SystemExit('unknown action')
