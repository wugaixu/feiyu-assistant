"""Live-session inbox/outbox. No shell execution or private platform APIs."""
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(r'__RUNTIME_ROOT__')


DEFAULT_MIN_SEND_GAP_SECONDS=1

def dm_policy():
    path=ROOT/'dm-policy.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'acknowledgements':True,'min_send_gap_seconds':DEFAULT_MIN_SEND_GAP_SECONDS,'enabled':True}


def retry_decision(attempts, result, policy, now=None):
    """Decide whether an outbound reply may be re-queued.

    Only an explicit rejection classified as platform_not_accepted can retry.
    A business check code such as 8101 is ambiguous and never retries. An
    uncertain outcome may already have arrived.
    """
    attempts = int(attempts or 0)
    if result != 'platform_not_accepted' or not policy.get('retry_rejected', False):
        return {'retry': False, 'attempts': attempts}
    if attempts >= int(policy.get('retry_max', 1)):
        return {'retry': False, 'attempts': attempts}
    delay = float(policy.get('retry_delay_seconds', 180))
    return {'retry': True, 'attempts': attempts + 1,
            'not_before': (time.time() if now is None else now) + delay}


def stable_links(links):
    # The web app renders URL text with href="#": resolving it includes the
    # current modal URL, which is unrelated to the message identity.
    return [dict(link, href=link.get('text', '') if link.get('href', '').endswith('#') else link.get('href', '')) for link in links]


def stable_signature(value):
    parts = json.loads(value)
    if parts[1]=='video_card':parts[1]='other'
    parts[3] = stable_links(parts[3])
    return json.dumps(parts, ensure_ascii=False, sort_keys=True)


def follow_latest(page):
    chat = page.locator('[data-stack-layer="chat"]')
    unread = chat.get_by_text(re.compile(r'^\s*\d+\s*条新消息\s*$'))
    if unread.count() == 1 and unread.is_visible():
        unread.click(timeout=3000, no_wait_after=True)
        page.wait_for_timeout(300)
        return True
    return False


def signature(message):
    kind = message.get('kind')
    raw_text = message.get('raw_text')
    if kind == 'audio':
        duration = re.match(r'^\d+["″]', raw_text or '')
        raw_text = message.get('audio_duration') or (duration.group(0) if duration else raw_text)
    if kind in ('image', 'audio', 'video','video_card'):
        kind = 'other'  # Compatible with checkpoints predating media classification.
    return json.dumps([message.get('direction'), kind,
                       raw_text, stable_links(message.get('links', []))],
                      ensure_ascii=False, sort_keys=True)


def prefix_additions(before, after):
    """Newest-first list. Require an unambiguous overlap of >=3 bubbles.

    Reordering, history pagination and insufficient overlap fail closed.
    Count-based overlap preserves consecutive identical texts.
    """
    if before == after:
        return 0
    candidates = []
    for count in range(len(after)):
        overlap = min(len(before), len(after) - count)
        if overlap >= 3 and after[count:count + overlap] == before[:overlap]:
            candidates.append(count)
    return candidates[0] if len(candidates) == 1 else None


class LiveStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS live_checkpoint (
                owner TEXT PRIMARY KEY, sequence TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS live_tasks (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, received REAL NOT NULL,
                raw_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                result TEXT);
            CREATE TABLE IF NOT EXISTS reply_outbox (
                id TEXT PRIMARY KEY, task_id TEXT, owner TEXT NOT NULL,
                text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', detail TEXT);
            CREATE TABLE IF NOT EXISTS media_records (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, received REAL NOT NULL,
                kind TEXT NOT NULL, record_json TEXT NOT NULL, attached_to TEXT);
        ''')
        from app import outbox
        outbox.schema(self.db)
        with self.db:
            for owner, sequence in self.db.execute('SELECT owner,sequence FROM live_checkpoint').fetchall():
                values = json.loads(sequence)
                migrated = []
                for value in values:
                    parts = json.loads(value)
                    if parts[1] in ('image','audio','video'):
                        parts[1] = 'other'
                    migrated.append(json.dumps(parts,ensure_ascii=False,sort_keys=True))
                if migrated != values:
                    self.db.execute('UPDATE live_checkpoint SET sequence=? WHERE owner=?',
                                    (json.dumps(migrated,ensure_ascii=False),owner))

    def observe(self, owner, messages):
        current = [signature(m) for m in messages]
        row = self.db.execute('SELECT sequence FROM live_checkpoint WHERE owner=?', (owner,)).fetchone()
        # The first snapshot is a baseline, never a set of historical tasks.
        count = 0 if row is None else prefix_additions([stable_signature(v) for v in json.loads(row[0])], current)
        if count is None:
            return {'state': 'ambiguous_sequence', 'accepted': 0}
        accepted = 0
        with self.db:
            for message in reversed(messages[:count]):
                if message.get('direction') == 'outgoing':
                    continue
                if message.get('kind') not in ('text', 'video_card', 'image', 'audio', 'video'):
                    continue
                if message.get('raw_text', '').strip().startswith(('测试001', '测试002', '测试003', '测试004')):
                    continue
                task_id = uuid.uuid4().hex
                state = 'pending' if message['kind'] == 'text' else 'media_pending'
                self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                                (task_id, owner, time.time(), json.dumps(message, ensure_ascii=False),state))
                text = '已收到你的消息，已进入本会话待处理队列。'
                if dm_policy().get('acknowledgements',True):
                    self.db.execute('INSERT INTO reply_outbox(id,task_id,owner,text) VALUES(?,?,?,?)',
                                    (task_id + ':ack', task_id, owner, text))
                accepted += 1
            self.db.execute('INSERT OR REPLACE INTO live_checkpoint VALUES(?,?)',
                            (owner, json.dumps(current, ensure_ascii=False)))
        return {'state': 'ready', 'accepted': accepted}


def install(force=False):
    """Update only this project's running adapter; keep its browser untouched."""
    from app.douyin_web_adapter import DouyinWebAdapter
    if getattr(DouyinWebAdapter, '_live_installed', False) and not force:
        return
    original_verify = getattr(DouyinWebAdapter, '_live_original_verify', DouyinWebAdapter.verify_active_contact)
    original_read = getattr(DouyinWebAdapter, '_live_original_read', DouyinWebAdapter.read_snapshot)
    DouyinWebAdapter._live_original_verify = original_verify
    DouyinWebAdapter._live_original_read = original_read
    store = LiveStore(ROOT / 'assistant.sqlite3')
    runtime = {'state': 'starting', 'last_identity_check': None, 'verify_count': 0}

    def verify(self):
        from app.local_runtime import enabled as local_enabled
        if local_enabled():return self.allowed_profile
        valid = self.page.evaluate('''() => {
            const s=window.__localAssistantContact;
            const h=document.querySelector('[data-stack-layer="chat"] .StackLayoutStackChatHeaderuser');
            const t=document.querySelector('[data-stack-layer="chat"] .StackLayoutStackChatHeadertitle');
            return !!s && s.header===h && s.html===h?.outerHTML && s.title===t?.textContent;
        }''')
        if valid:
            return self.allowed_profile
        owner = original_verify(self)
        self.page.evaluate('''() => {
            const h=document.querySelector('[data-stack-layer="chat"] .StackLayoutStackChatHeaderuser');
            const t=document.querySelector('[data-stack-layer="chat"] .StackLayoutStackChatHeadertitle');
            window.__localAssistantContact={header:h,html:h.outerHTML,title:t?.textContent};
        }''')
        runtime['last_identity_check'] = time.time()
        runtime['verify_count'] += 1
        return owner

    def send(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 1500:
            raise ValueError('Reply must contain 1..1500 characters')
        verify(self)
        editor = self.page.locator('[data-e2e="msg-input"] [contenteditable="true"]')
        if editor.count() != 1 or editor.inner_text().strip('\u200b \n'):
            raise ValueError('Existing user draft; reply paused')
        editor.fill(text)
        verify(self)
        def matches():
            texts = self.page.locator('.messageMessageBoxisFromMe [data-e2e="msg-item-content"]').all_inner_texts()
            compact = lambda s: re.sub(r'\s+', '', s)
            return sum(compact(value) == compact(text) for value in texts)
        before = matches()
        from app.send_receipt import decode, SendRejected
        with self.page.expect_response(lambda r: r.url.split('?')[0]=='https://imapi.douyin.com/v1/message/send',timeout=15000) as response_event:
            self.page.locator('.e2e-send-msg-btn').click(timeout=5000, no_wait_after=True)
        response = response_event.value
        receipt = decode(response.body())
        receipt['http_status'] = response.status
        (ROOT/'last-send-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False),encoding='utf-8')
        if response.status!=200 or not receipt['accepted']:
            raise SendRejected(receipt)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if matches() > before:
                return receipt
            self.page.wait_for_timeout(250)
        raise TimeoutError('No new outgoing bubble; outcome uncertain')

    def read(self):
        from app.local_runtime import enabled as local_enabled
        if local_enabled():return {'source':'sdk_local','messages':[]}
        verify(self)
        try:
            from app.video_rounds import tick, browser_tick
            tick(store.db,self.allowed_profile)
            browser_tick(store.db,self.page.context)
            runtime.pop('video_round_error',None)
        except Exception as exc:
            runtime['video_round_error']=str(exc)[:1000]
        from app.sdk_receiver import enabled as sdk_inbound_enabled
        sdk_inbound = sdk_inbound_enabled()
        followed = False if sdk_inbound else follow_latest(self.page)
        snapshot = {'source':'douyin_sdk','messages':[]} if sdk_inbound else original_read(self)
        runtime['followed_latest'] = followed
        verify(self)
        owner = self.allowed_profile
        outcome = {'state':'ready','accepted':0,'inbound_transport':'sdk'} if sdk_inbound else store.observe(owner, snapshot.get('messages', []))
        from app.media_pipeline import process_one, import_requested_samples
        import_requested_samples(store.db, owner, snapshot)
        verify(self)
        media_result = process_one(store.db, self.page, owner)
        verify(self)
        if media_result:
            runtime['last_media'] = media_result
        runtime.update(outcome)
        if outcome['state'] == 'ambiguous_sequence':
            runtime.setdefault('sequence_blocked_since', time.time())
            (ROOT / 'sequence-recovery.json').write_text(json.dumps({
                'updated_at':time.time(), 'blocked_since':runtime['sequence_blocked_since'],
                'reason':'No unique overlap with verified checkpoint',
                'messages':snapshot.get('messages', [])},ensure_ascii=False),encoding='utf-8')
        else:
            runtime.pop('sequence_blocked_since', None)
        runtime['updated_at'] = time.time()
        runtime['browser_pid_preserved'] = True
        from app.event_dispatch import dispatch_one
        try:
            dispatched = dispatch_one(store.db)
            if dispatched:
                runtime['last_dispatch'] = dispatched
        except Exception as exc:
            runtime['dispatch_error'] = str(exc)[:1000]
        # Explicit rejections may be retried a bounded number of times after a
        # delay; uncertain outcomes and crashes never resend.
        store.db.execute('''CREATE TABLE IF NOT EXISTS outbound_retries(
            outbox_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL,
            not_before REAL NOT NULL, last_status TEXT)''')
        store.db.commit()
        row = store.db.execute('''SELECT o.id,o.text FROM reply_outbox o
            LEFT JOIN outbound_retries r ON r.outbox_id=o.id
            WHERE o.owner=? AND o.status='pending' AND o.kind='text' AND COALESCE(r.not_before,0)<=?
            ORDER BY o.rowid LIMIT 1''', (owner, time.time())).fetchone()
        test_row = None
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbound_tests'").fetchone():
            test_row = store.db.execute("SELECT o.id,o.text FROM reply_outbox o JOIN outbound_tests t ON t.outbox_id=o.id WHERE o.owner=? AND o.status='pending' AND o.kind='text' AND t.not_before<=? ORDER BY t.not_before LIMIT 1",(owner,time.time())).fetchone()
        if test_row:
            row = test_row
        policy=dm_policy()
        stamp=ROOT/'last-send-at.json'
        last_attempt=json.loads(stamp.read_text(encoding='utf-8')).get('time',0) if stamp.exists() else 0
        cooling=time.time()-last_attempt<policy.get('min_send_gap_seconds',DEFAULT_MIN_SEND_GAP_SECONDS)
        runtime['outbound_state']='cooldown' if cooling else 'enabled'
        if not policy.get('enabled',True) or (cooling and not test_row):
            runtime['outbound_state']='cooldown' if cooling else 'disabled'
        elif row:
            editor = self.page.locator('[data-e2e="msg-input"] [contenteditable="true"]')
            if editor.count() == 1 and not editor.inner_text().strip('\u200b \n'):
                with store.db:
                    claimed = store.db.execute("UPDATE reply_outbox SET status='attempted' WHERE id=? AND status='pending'", (row[0],)).rowcount
                if claimed:
                    stamp.write_text(json.dumps({'time':time.time(),'outbox_id':row[0]}),encoding='utf-8')
                    # Prefer the SDK sender: it holds its own login and device
                    # identity, so the platform confirms delivery there instead
                    # of intermittently refusing the web input box.
                    from app import sdk_sender
                    if sdk_sender.health():
                        outcome = sdk_sender.send(row[1])
                        result = outcome.pop('result', 'outcome_uncertain')
                        detail = json.dumps(outcome, ensure_ascii=False)
                        runtime['last_reply_channel'] = 'sdk'
                    else:
                        try:
                            receipt = send(self, row[1])
                            result, detail = 'server_accepted', json.dumps(receipt,ensure_ascii=False)
                        except Exception as exc:
                            from app.send_receipt import SendRejected
                            result = 'platform_not_accepted' if isinstance(exc,SendRejected) else 'outcome_uncertain'
                            detail = str(exc)
                        runtime['last_reply_channel'] = 'web'
                    seen = store.db.execute('SELECT attempts FROM outbound_retries WHERE outbox_id=?',(row[0],)).fetchone()
                    decision = retry_decision(seen[0] if seen else 0, result, policy)
                    with store.db:
                        store.db.execute('UPDATE reply_outbox SET status=?,detail=? WHERE id=?',
                                         ('pending' if decision['retry'] else result, detail, row[0]))
                        store.db.execute('INSERT OR REPLACE INTO outbound_retries VALUES(?,?,?,?)',
                                         (row[0], decision['attempts'], decision.get('not_before',0), result))
                    runtime['last_reply_status'] = result
                    if decision['retry']:
                        runtime['last_reply_retry'] = {'outbox_id': row[0], 'attempt': decision['attempts'],
                                                       'not_before': decision['not_before']}
        else:
            from app.image_sender import process_one as send_image
            image_result=send_image(store.db,self.page,owner,lambda:verify(self))
            if image_result:
                runtime['last_image_reply']=image_result
        (ROOT / 'live-service-status.json').write_text(json.dumps(runtime, ensure_ascii=False), encoding='utf-8')
        return snapshot

    DouyinWebAdapter.verify_active_contact = verify
    DouyinWebAdapter.read_snapshot = read
    DouyinWebAdapter._live_installed = True
    (ROOT / 'live-install.json').write_text(json.dumps({'installed_at': time.time()}), encoding='utf-8')
