"""DSH audit regressions. All databases/resources isolated; no real sends."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import outbox, local_runtime as lr, astrbot_worker as aw, sdk_receiver as rx
from app import shared_comments as comments, workflow_health, event_dispatch, task_context
from app.live_service import LiveStore


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=lr.ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = LiveStore(':memory:')
        self.db = self.store.db
        self.addCleanup(self.db.close)
        for module, name in ((lr, 'ROOT'), (aw, 'ROOT'), (task_context, 'PACKETS')):
            mock = patch.object(module, name, self.root)
            mock.start(); self.addCleanup(mock.stop)

    def task(self, key, kind='text', state='pending', received=100, **extra):
        raw = {'kind': kind, 'direction': 'incoming', 'transport': 'sdk', **extra}
        self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',
                        (key, rx.OWNER, received, json.dumps(raw), state))
        self.db.commit()

    def test_one_poisoned_event_does_not_rollback_neighbors(self):
        def event(seq):
            return dict(seq=seq, message_id=str(seq), sender_uid=rx.OWNER_UID,
                        account_uid=rx.ACCOUNT_UID, content={'kind': 'text'})
        bad = event(2); bad['content'] = 'broken'
        foreign = event(3); foreign['sender_uid'] = 'other'
        self.assertEqual(rx.ingest(self.db, [event(1), bad, foreign, event(4)]), 2)
        self.assertEqual(self.db.execute('SELECT seq FROM sdk_inbound_cursor').fetchone()[0], 4)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sdk_inbound_rejections').fetchone()[0], 2)
        self.assertEqual(rx.ingest(self.db, [event(1), bad, foreign, event(4)]), 0)

    def attempts(self):
        self.db.execute('CREATE TABLE outbound_attempts(id TEXT PRIMARY KEY,outbox_id TEXT,started REAL,finished REAL,characters INTEGER,status TEXT,detail TEXT)')
        self.db.commit()

    def test_expired_attempt_becomes_uncertain_and_tail_can_continue_without_replay(self):
        self.attempts()
        self.db.execute("INSERT INTO reply_outbox(id,task_id,owner,text,status,seq) VALUES('head','task',?,'A','attempted',0)",(rx.OWNER,))
        self.db.execute("INSERT INTO reply_outbox(id,task_id,owner,text,seq) VALUES('tail','task',?,'B',1)",(rx.OWNER,))
        self.db.execute("INSERT INTO outbound_attempts VALUES('a','head',100,NULL,1,'attempted',NULL)")
        self.db.commit()
        self.assertFalse(outbox.allowed(self.db, 'tail'))
        self.assertEqual(outbox.recover_interrupted(self.db, now=399), 0)
        self.assertEqual(outbox.recover_interrupted(self.db, now=401), 1)
        self.assertTrue(outbox.allowed(self.db, 'tail'))
        self.assertEqual(self.db.execute("SELECT status FROM reply_outbox WHERE id='head'").fetchone()[0], 'outcome_uncertain')
        self.assertEqual(outbox.recover_interrupted(self.db, now=999), 0)
        with patch('app.sdk_sender.health', return_value={'online':True,'uid':rx.ACCOUNT_UID}), patch('app.sdk_sender.send',return_value={'result':'server_accepted'}) as send, patch.object(lr,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}):
            lr.outbound_step(self.db,rx.OWNER)
        send.assert_called_once_with('B')

    def test_media_write_failure_is_terminal_and_does_not_repeat_capture(self):
        self.task('media', kind='image', state='media_pending')
        with patch('app.sdk_media.capture',side_effect=OSError('disk full')) as capture:
            result=lr.media_step(self.db,rx.OWNER)
            self.assertIsNone(lr.media_step(self.db,rx.OWNER))
        capture.assert_called_once()
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'resource_saved')
        record=json.loads(self.db.execute('SELECT record_json FROM media_records').fetchone()[0])
        self.assertEqual(record['failure_reason'],'media_write_failed')

    def test_legacy_attempt_without_journal_waits_observation_window(self):
        self.attempts()
        self.db.execute("INSERT INTO reply_outbox(id,owner,text,status) VALUES('legacy',?,'A','attempted')",(rx.OWNER,));self.db.commit()
        self.assertEqual(outbox.recover_interrupted(self.db,now=100),0)
        self.assertEqual(outbox.recover_interrupted(self.db,now=399),0)
        self.assertEqual(outbox.recover_interrupted(self.db,now=400),1)
        detail=json.loads(self.db.execute("SELECT detail FROM reply_outbox WHERE id='legacy'").fetchone()[0])
        self.assertEqual(detail['reason'],'missing_attempt_journal_after_observation')
        self.assertEqual(self.db.execute('SELECT count(*) FROM outbound_attempts').fetchone()[0],0)

    def test_failed_standalone_media_not_redelivered(self):
        from app.media_pipeline import dispatch_standalone_shared
        self.task('media',kind='image',state='resource_saved',association_policy='media_10s_v1')
        self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('media',rx.OWNER,100,'image',json.dumps({'status':'unavailable','failure_reason':'download_failed'})))
        self.db.commit()
        dispatch_standalone_shared(self.db,rx.OWNER)
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'resource_saved')

    def test_stale_or_future_media_does_not_block_current_text(self):
        self.task('stale',kind='image',state='media_pending',received=1)
        self.task('future',kind='video',state='media_pending',received=2000)
        self.task('text',received=1000,raw_text='当前消息')
        thread='11111111-1111-1111-1111-111111111111'
        with patch.object(event_dispatch,'target_config',return_value={'provider':'codex'}),patch.object(event_dispatch,'choose_thread',return_value={'thread_id':thread}),patch.object(event_dispatch,'thread_id',return_value=thread):
            result=event_dispatch.dispatch_one(self.db,runner=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=f'Queued message {thread} for thread {thread}.',stderr=''),executable='codex.exe')
        self.assertEqual(result['task_id'],'text')

    def test_completion_time_controls_send_gap(self):
        now=[100.0]
        self.db.execute("INSERT INTO reply_outbox(id,owner,text) VALUES('one',?,'A')",(rx.OWNER,))
        self.db.execute("INSERT INTO reply_outbox(id,owner,text) VALUES('two',?,'B')",(rx.OWNER,));self.db.commit()
        def send(text):
            now[0]=110.0
            return {'result':'server_accepted'}
        with patch.object(lr.time,'time',side_effect=lambda:now[0]),patch.object(lr,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':1}),patch('app.sdk_sender.health',return_value={'online':True,'uid':rx.ACCOUNT_UID}),patch('app.sdk_sender.send',side_effect=send) as sender:
            lr.outbound_step(self.db,rx.OWNER)
            now[0]=110.5
            self.assertEqual(lr.outbound_step(self.db,rx.OWNER)['state'],'cooldown')
            self.assertEqual(sender.call_count,1)

    def test_bad_comment_limit_falls_back_without_changing_config(self):
        path=self.root/'comment-policy.json';path.write_text('{"enabled":true,"daily_limit":"bad"}')
        with patch.object(comments,'POLICY',path):cfg=comments.policy()
        self.assertEqual(cfg['daily_limit'],10);self.assertTrue(cfg['_warnings'])
        self.assertIn('"bad"',path.read_text())

    def test_comment_failure_is_isolated_from_media_job(self):
        with patch.object(lr,'LiveStore',return_value=SimpleNamespace(db=self.db)),patch.object(lr,'media_step',return_value={'task_id':'new-media'}) as media,patch.object(comments,'step') as comment:
            self.assertEqual(lr.media_job(rx.OWNER)['task_id'],'new-media')
        media.assert_called_once();comment.assert_not_called()
        # media_job closes its store; replace the fixture for tearDown.
        self.store=LiveStore(':memory:');self.db=self.store.db;self.addCleanup(self.db.close)

    def test_dom_identity_is_diagnostic_and_gallery_readback_uses_note(self):
        from app.browser_publish import HiddenCommentBrowser, work_identity_diagnostic
        class Page:
            url='https://www.douyin.com/note/123?modal_id=456'
            def locator(self,selector):
                return SimpleNamespace(count=lambda:1,text_content=lambda **kw:json.dumps({'aweme_detail':{'aweme_id':'123'}}))
            def on(self,event,callback):self.callback=callback
            def goto(self,url,**kw):
                self.visited=url
                self.callback(SimpleNamespace(url='https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=123',json=lambda:{'status_code':0,'comments':[]}))
            def remove_listener(self,*args):pass
        page=Page()
        diagnostic=work_identity_diagnostic(page,'123')
        self.assertEqual(diagnostic['render_work_ids'],['123'])
        self.assertTrue(diagnostic['diagnostic_only'])
        browser=HiddenCommentBrowser();browser.page=page
        browser.comments('123',kind='image_gallery')
        self.assertEqual(page.visited,'https://www.douyin.com/note/123')

    def test_background_state_cache_does_not_cache_ui_refresh(self):
        from app import astrbot_client as client
        client._state_cache.clear()
        with patch.object(client,'ROOT',self.root),patch.object(client,'_state',return_value={'online':True,'sessions':[]}) as fetch:
            one=client.state(cache_seconds=5);one['online']=False
            self.assertTrue(client.state(cache_seconds=5)['online'])
            self.assertEqual(fetch.call_count,1)
            client.state();self.assertEqual(fetch.call_count,2)
        client._state_cache.clear()

    def test_comment_exception_cannot_undo_reply(self):
        self.task('task',raw_text='你好')
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT PRIMARY KEY,attempted REAL,status TEXT,queue_id TEXT,detail TEXT)')
        self.db.execute('INSERT INTO chat_dispatch VALUES(?,0,?,NULL,?)',('task','queued',json.dumps({'provider':'astrbot','session_id':'session'})));self.db.commit()
        fake=SimpleNamespace(db=self.db)
        # Worker owns its connection, so use a file-backed isolated store.
        target=self.root/'assistant.sqlite3'
        disk=sqlite3.connect(target);self.db.backup(disk);disk.close()
        with patch.object(aw.client,'chat',return_value=[{'type':'text','text':'你好呀'}]):
            aw.run('task',{'session_id':'session'})
        disk=sqlite3.connect(target)
        try:
            self.assertEqual(disk.execute('SELECT status FROM live_tasks').fetchone()[0],'done')
            self.assertEqual(disk.execute('SELECT text FROM reply_outbox').fetchone()[0],'你好呀')
        finally:disk.close()

    def test_only_accepted_replies_enter_history(self):
        for key,direction,status in [('yes','incoming','server_accepted'),('maybe','incoming','outcome_uncertain'),('fake','unverified','server_accepted')]:
            self.task(key,state='done',raw_text=key,direction=direction)
            self.db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,status) VALUES(?,?,?,?,?)',(key,key,rx.OWNER,'reply-'+key,status))
        self.db.commit()
        history=aw.recent_history(self.db,rx.OWNER)
        self.assertIn('reply-yes',history);self.assertNotIn('reply-maybe',history);self.assertNotIn('reply-fake',history)

    def test_fences_preserve_code_but_not_surrounding_paragraphs(self):
        parts=aw._text_parts('前文\n\n```python\na=1\n\nb=2\n```\n\n后文',{})
        self.assertEqual(len(parts),3)
        self.assertIn('a=1\n\nb=2',parts[1]['text'])
        self.assertEqual(len(aw._text_parts('行内 ``` 标记\n\n下一段',{})),2)

    def test_output_limits_warn_once_and_prompt_uses_same_limit(self):
        report={}
        parts=aw.finalize_parts([{'type':'text','text':'\n\n'.join(['段落']*30)},{'type':'text','text':'后文'}],'task',report)
        self.assertEqual(len(parts),aw.MAX_PARTS)
        self.assertEqual(len([x for x in report['warnings'] if '回复段数' in x]),1)
        self.assertIn(str(aw.MAX_PARTS)+' 条',aw.dm_prompt({'kind':'text','raw_text':'你好'},[]))

    def test_health_counts_all_outgoing_kinds_and_uncertainty(self):
        for i,(kind,status) in enumerate([('image','pending'),('share','attempted'),('text','outcome_uncertain')]):
            self.db.execute("INSERT INTO reply_outbox(id,owner,text,kind,status) VALUES(?,?,'',?,?)",(str(i),rx.OWNER,kind,status))
        self.db.commit()
        dest=self.root/'health.sqlite3';disk=sqlite3.connect(dest);self.db.backup(disk);disk.close()
        snapshot=workflow_health.snapshot(dest)
        self.assertEqual(snapshot['reply_pending'],1)
        self.assertEqual(snapshot['reply_attempted'],1)
        self.assertEqual(snapshot['reply_uncertain'],1)


if __name__=='__main__':unittest.main()
