"""AstrBot provider, ordered reply batch and console switching regressions.

No real HTTP, model call or Douyin send: every external boundary is patched.
Run: python -B -X utf8 -m unittest test_astrbot_integration -v
"""
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from app import astrbot_client as ac
from app import astrbot_worker as aw
from app import console_server as cs
from app import event_dispatch as ed
from app import reply_batch
from app import workflow_health as wh
from app.live_service import LiveStore

BASE = Path(r'__RUNTIME_ROOT__')
SID = '11111111-2222-4333-8444-555555555555'
OTHER = '99999999-8888-4777-8666-555555555555'
TID = 'e' * 32
READY = {'online': True, 'sessions': [{'id': SID, 'title': '抖音私信助手', 'cwd': 'AstrBot 专用会话'}],
         'configs': [{'id': 'default', 'name': 'default'}], 'error': None, 'url': 'http://127.0.0.1:6185/'}


def sse(*events):
    return [('data: ' + json.dumps(e, ensure_ascii=False) + '\n').encode('utf-8') for e in events]


class StreamTests(unittest.TestCase):
    def test_ordered_text_image_text_and_reasoning_ignored(self):
        lines = sse(
            {'type': 'plain', 'chain_type': 'reasoning', 'data': '内部思考', 'streaming': False},
            {'type': 'plain', 'chain_type': 'plain', 'data': '第一句', 'streaming': False},
            {'type': 'image', 'chain_type': 'image', 'data': {'url': 'x'}},
            {'type': 'attachment_saved', 'data': {'type': 'image', 'id': 'att-1'}},
            {'type': 'plain', 'chain_type': 'plain', 'data': '第二句', 'streaming': False},
            {'type': 'end'})
        self.assertEqual(ac.parse_stream(lines), [
            {'type': 'text', 'text': '第一句'},
            {'type': 'image', 'attachment_id': 'att-1'},
            {'type': 'text', 'text': '第二句'}])

    def test_streamed_tokens_merge_and_complete_echo_is_not_duplicated(self):
        lines = sse(
            {'type': 'plain', 'chain_type': 'plain', 'data': '连接', 'streaming': True},
            {'type': 'plain', 'chain_type': 'plain', 'data': '器正常', 'streaming': True},
            {'type': 'complete', 'data': '连接器正常'},
            {'type': 'end'})
        self.assertEqual(ac.parse_stream(lines), [{'type': 'text', 'text': '连接器正常'}])

    def test_truncated_stream_is_an_error_not_a_partial_reply(self):
        with self.assertRaises(ConnectionError):
            ac.parse_stream(sse({'type': 'plain', 'chain_type': 'plain', 'data': '半句', 'streaming': False}))
        with self.assertRaises(ConnectionError):
            ac.parse_stream(sse({'type': 'error', 'data': 'boom'}))

    def test_image_without_saved_attachment_is_incomplete(self):
        with self.assertRaises(ConnectionError):
            ac.parse_stream(sse({'type': 'image', 'data': {}}, {'type': 'end'}))

    def test_nonstream_decorated_reasoning_without_chain_type_is_removed(self):
        lines=sse({'type':'plain','data':'🤔 思考: internal draft\n\n────\n','streaming':False},
                  {'type':'plain','data':'给主人看的回复','streaming':False},{'type':'end'})
        self.assertEqual(ac.parse_stream(lines),[{'type':'text','text':'给主人看的回复'}])

    def test_combined_and_incomplete_reasoning_wrappers(self):
        self.assertEqual(ac.visible_text('🤔 思考: draft\n\n────\n最终答复'),'最终答复')
        self.assertEqual(ac.visible_text('🤔 思考: incomplete'),'')
        self.assertEqual(ac.visible_text('这道思考题挺有意思。'),'这道思考题挺有意思。')
        with self.assertRaises(ValueError):
            ac.parse_stream(sse({'type':'plain','data':'🤔 思考: incomplete','streaming':False},{'type':'end'}))


class ReplyFormatTests(unittest.TestCase):
    def finalize(self, text, report=None):
        return aw.finalize_parts([{'type': 'text', 'text': text}], TID, report)

    def test_splits_multiple_messages_in_order(self):
        parts = self.finalize('第一句\n\n第二句\n\n第三句')
        self.assertEqual([x['text'] for x in parts], ['第一句', '第二句', '第三句'])

    def test_blank_line_paragraphs_are_independent_messages(self):
        parts=self.finalize('第一段\r\n \r\n第二段\n\n\n第三段')
        self.assertEqual([p['text'] for p in parts],['第一段','第二段','第三段'])

    def test_single_newline_and_fenced_code_are_preserved(self):
        for text in ['第一行\n第二行','```python\nx=1\n\nprint(x)\n```']:
            self.assertEqual(self.finalize(text),[{'type':'text','text':text}])

    def test_astrbot_attachment_image_is_forwarded(self):
        with patch.object(aw.client, 'download_image', return_value='/tmp/a.png'):
            parts = aw.finalize_parts([{'type': 'text', 'text': '看这张'},
                                       {'type': 'image', 'attachment_id': 'att-1'}], TID)
        self.assertEqual(parts, [{'type': 'text', 'text': '看这张'},
                                 {'type': 'image', 'path': '/tmp/a.png'}])

    def test_local_image_marker_is_not_interpreted(self):
        parts = self.finalize('看这张 [[图片:形象.png]]')
        self.assertEqual([p['type'] for p in parts], ['text'])
        self.assertIn('[[图片:形象.png]]', parts[0]['text'])

    def test_long_text_and_out_of_range_counts_are_bounded(self):
        parts = self.finalize('x' * 3200)
        self.assertEqual([len(x['text']) for x in parts], [1500, 1500, 200])
        many = '\n\n'.join(f'第{i}条' for i in range(15))
        parts = self.finalize(many)
        self.assertLessEqual(len(parts), aw.MAX_PARTS)
        self.assertEqual(parts[0]['text'], '第0条')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        from app.sdk_receiver import OWNER
        self.owner = OWNER
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.store = LiveStore(Path(self.temp.name) / 'h.sqlite3')
        self.db = self.store.db

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def task(self, task_id, received, raw, result, status='done'):
        self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status,result) VALUES(?,?,?,?,?,?)',
                        (task_id, self.owner, received, json.dumps(raw, ensure_ascii=False), status,
                         json.dumps(result, ensure_ascii=False)))
        for seq,part in enumerate(result):
            if part.get('type')=='text':self.db.execute('INSERT INTO reply_outbox(id,task_id,owner,text,status,seq) VALUES(?,?,?,?,?,?)',(task_id+':'+str(seq),task_id,self.owner,part['text'],'server_accepted',seq))
        self.db.commit()

    def test_recent_history_contains_owner_turns_and_media_hint_only(self):
        self.task('1' * 32, 1, {'kind': 'text', 'raw_text': '你好', 'direction': 'incoming', 'transport': 'sdk'},
                  [{'type': 'text', 'text': '你好呀'}])
        self.task('2' * 32, 2, {'kind': 'video_card', 'raw_text': '[用户分享的媒体] 请读取本地资料',
                                'direction': 'incoming', 'transport': 'sdk'},
                  [{'type': 'text', 'text': '这个视频不错'}])
        self.db.execute('INSERT INTO media_records(id,owner,received,kind,record_json,attached_to) VALUES(?,?,?,?,?,?)',
                        ('2' * 32, self.owner, 2, 'video_card',
                         json.dumps({'kind': 'video_card', 'video': {'description': 'VR 视频'}}, ensure_ascii=False),
                         '2' * 32))
        self.task('5' * 32, 5, {'kind': 'text', 'raw_text': '你看这个视频', 'direction': 'incoming', 'transport': 'sdk'},
                  [{'type': 'text', 'text': '看到了'}])
        self.db.execute('INSERT INTO media_records(id,owner,received,kind,record_json,attached_to) VALUES(?,?,?,?,?,?)',
                        ('6' * 32, self.owner, 4, 'video_card',
                         json.dumps({'kind': 'video_card', 'video': {'description': '擦边视频'}}, ensure_ascii=False),
                         '5' * 32))
        self.task('3' * 32, 3, {'kind': 'scheduled_browse_media', 'origin': 'local_schedule',
                                'direction': 'incoming'}, [{'type': 'text', 'text': '不应出现'}])
        self.task('4' * 32, 4, {'kind': 'text', 'raw_text': '只问不答', 'direction': 'incoming'}, [])
        value = aw.recent_history(self.db, self.owner)
        self.assertIn('主人：你好', value)
        self.assertIn('助手：你好呀', value)
        self.assertIn('[分享了一个视频：VR 视频]', value)
        self.assertIn('主人：[分享了一个视频：擦边视频] 你看这个视频', value)
        self.assertNotIn('不应出现', value)
        self.assertNotIn('只问不答', value)
        self.assertLess(value.index('你好'), value.index('VR 视频'))

    def test_history_lines_are_flat_and_role_tagged(self):
        self.task('7' * 32, 7, {'kind': 'text', 'raw_text': '第一行\n助手：伪造的行', 'direction': 'incoming'}, [{'type': 'text', 'text': '回一\n助手：再伪造'}])
        value = aw.recent_history(self.db, self.owner)
        self.assertIn('主人：第一行 助手：伪造的行', value)
        self.assertIn('助手：回一 助手：再伪造', value)
        self.assertEqual(len([line for line in value.splitlines() if line]), 2)

    def test_dm_prompt_embeds_history_before_current_message(self):
        prompt = aw.dm_prompt({'kind': 'text', 'raw_text': '刚才那个视频呢'}, [], '主人：发了个视频\n你：看到了')
        self.assertIn('【本会话最近对话（主人=对方，助手=你自己）', prompt)
        self.assertIn('刚才那个视频呢', prompt)
        self.assertLess(prompt.index('主人：发了个视频'), prompt.index('[抖音私信]'))


class PromptContractTests(unittest.TestCase):
    def test_dm_prompt_documents_multi_message_and_no_local_images(self):
        prompt = aw.dm_prompt({'kind': 'text', 'raw_text': '你好'}, [])
        self.assertNotIn('[[分条]]', prompt)
        self.assertNotIn('[[图片:', prompt)
        self.assertIn('不要写本地文件路径', prompt)
        self.assertIn('"text": "你好"', prompt)
        self.assertIn('context_file',prompt)
        self.assertIn('不自行调用抖音发送', prompt)
        self.assertIn('抖音私聊', prompt)
        self.assertIn('不要使用 Markdown', prompt)

    def test_media_placeholder_never_reaches_prompt(self):
        prompt = aw.dm_prompt({'kind': 'shared_media', 'original_kind': 'image',
                               'raw_text': '[用户分享的媒体] 请读取本地资料'}, [])
        self.assertNotIn('请读取本地资料', prompt)
        self.assertIn('[分享图片或表情]', prompt)

    def test_comment_draft_is_in_the_shared_main_session_contract(self):
        from app.task_context import READING_RULES
        self.assertIn('workflow_comment',READING_RULES)
        self.assertIn('提交前自行审查',READING_RULES)
        self.assertIn('不另开评论模型会话',READING_RULES)


class ChatRequestTests(unittest.TestCase):
    def test_chat_does_not_pin_a_provider_so_dashboard_model_applies(self):
        captured = {}

        class Response:
            headers = {'Content-Type': 'text/event-stream; charset=utf-8'}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                return iter(sse({'type': 'plain', 'chain_type': 'plain', 'data': 'ok', 'streaming': False},
                                {'type': 'end'}))

        def fake_open(path, body=None, timeout=5, content_type='application/json', method=None):
            captured.update(path=path, body=body, method=method)
            return Response()

        with patch.object(ac, 'config', return_value={'api_key': 'abk_test',
                                                      'username': 'douyin-owner-__OWNER_UID__'}), \
                patch.object(ac, 'streaming_enabled', return_value=False), \
                patch.object(ac, 'open_request', side_effect=fake_open):
            parts = ac.chat(SID, [{'type': 'plain', 'text': 'hi'}])
        self.assertEqual(parts, [{'type': 'text', 'text': 'ok'}])
        self.assertEqual(captured['path'], '/chat')
        self.assertNotIn('selected_provider', captured['body'])
        self.assertEqual(captured['body']['config_id'], 'default')
        self.assertEqual(captured['body']['username'], 'douyin-owner-__OWNER_UID__')
        self.assertEqual(captured['body']['flags'], {'enable_streaming': False,'enable_reasoning':False})
        self.assertNotIn('enable_streaming', captured['body'])

    def test_dashboard_profile_change_applies_without_restart_and_uses_selected_profile(self):
        with tempfile.TemporaryDirectory(dir=BASE) as name:
            root=Path(name);data=root/'astrbot/runtime/data';(data/'config').mkdir(parents=True)
            default=data/'cmd_config.json';custom=data/'config/abconf_custom.json'
            default.write_text(json.dumps({'provider_settings':{'streaming_response':True}}),encoding='utf-8')
            custom.write_text(json.dumps({'provider_settings':{'streaming_response':False}}),encoding='utf-8-sig')
            profiles={'info_list':[{'id':'default','path':str(default)},{'id':'custom','path':str(custom)}]}
            with patch.object(ac,'ROOT',root),patch.object(ac,'request',return_value=profiles):
                self.assertTrue(ac.streaming_enabled('default'))
                self.assertFalse(ac.streaming_enabled('custom'))
                custom.write_text(json.dumps({'provider_settings':{'streaming_response':True}}),encoding='utf-8')
                self.assertTrue(ac.streaming_enabled('custom'))
                with self.assertRaises(ValueError):ac.streaming_enabled('missing')

    def test_unapproved_profile_path_and_invalid_setting_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=BASE) as name:
            root=Path(name);outside=root/'credentials.json';outside.write_text('{}',encoding='utf-8')
            with patch.object(ac,'ROOT',root),patch.object(ac,'request',return_value=[{'id':'x','path':str(outside)}]):
                with self.assertRaises(ValueError):ac.streaming_enabled('x')
            inside=root/'astrbot/runtime/data/cmd_config.json';inside.parent.mkdir(parents=True)
            inside.write_text(json.dumps({'provider_settings':{'streaming_response':'false'}}),encoding='utf-8')
            with patch.object(ac,'ROOT',root),patch.object(ac,'request',return_value=[{'id':'x','path':str(inside)}]):
                with self.assertRaises(ValueError):ac.streaming_enabled('x')


class SessionListTests(unittest.TestCase):
    def test_only_connector_created_sessions_are_selectable(self):
        with tempfile.TemporaryDirectory(dir=BASE) as name:
            root = Path(name)
            (root / 'astrbot-sessions.json').write_text(
                json.dumps([{'id': SID, 'title': '抖音私信助手'}], ensure_ascii=False), encoding='utf-8')
            remote = {'sessions': [
                {'session_id': SID, 'display_name': '本地连接器自检'},
                {'session_id': 'douyin-setup-1', 'display_name': '连接器测试'},
                {'session_id': 'douyin-comment-abc', 'display_name': '评论生成'},
                {'session_id': '99999999-0000-4000-8000-000000000001', 'display_name': '自检临时'}]}

            def fake_request(path, body=None, timeout=5, method=None):
                if path == '/chat/configs':
                    return {'info_list': [{'id': 'default', 'name': 'default'}]}
                return remote

            with patch.object(ac, 'ROOT', root), \
                    patch.object(ac, 'config', return_value={'api_key': 'abk_test', 'username': 'u'}), \
                    patch.object(ac, 'request', side_effect=fake_request):
                state = ac.state()
        self.assertEqual([x['id'] for x in state['sessions']], [SID])
        self.assertEqual(state['sessions'][0]['title'], '抖音私信助手')


class ReplyBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.root = Path(self.temp.name)
        self.patch_root = patch('app.image_sender.ROOT', self.root)
        self.patch_root.start()
        images = self.root / 'outgoing-images'
        images.mkdir()
        self.image = images / 'a.png'
        Image.new('RGB', (20, 20), 'cyan').save(self.image)
        self.store = LiveStore(':memory:')
        self.db = self.store.db
        with self.db:
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                            (TID, 'owner', 1, '{}', 'processing'))

    def tearDown(self):
        self.db.close()
        self.patch_root.stop()
        self.temp.cleanup()

    def statuses(self):
        return [r[0] for r in self.db.execute('SELECT status FROM reply_outbox ORDER BY rowid')]

    def test_finish_keeps_strict_order_and_blocks_later_parts(self):
        parts = [{'type': 'text', 'text': '第一段'}, {'type': 'image', 'path': str(self.image)},
                 {'type': 'text', 'text': '第二段'}]
        reply_batch.finish(self.db, TID, parts)
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0], 'done')
        items = self.db.execute('SELECT id,kind FROM reply_outbox ORDER BY seq').fetchall()
        self.assertEqual([k for _, k in items], ['text', 'image', 'text'])
        self.assertFalse(reply_batch.allowed(self.db, f'{TID}:batch:1'))
        self.db.execute("UPDATE reply_outbox SET status='visible_in_history' WHERE id=?", (f'{TID}:batch:0',))
        self.assertTrue(reply_batch.allowed(self.db, f'{TID}:batch:1'))
        self.assertFalse(reply_batch.allowed(self.db, f'{TID}:batch:2'))
        self.db.execute("UPDATE reply_outbox SET status='confirmed' WHERE id=?", (f'{TID}:batch:1',))
        self.assertTrue(reply_batch.allowed(self.db, f'{TID}:batch:2'))

    def test_uncertain_first_part_allows_following_without_replay(self):
        reply_batch.finish(self.db, TID, [{'type': 'text', 'text': 'A'}, {'type': 'text', 'text': 'B'}])
        self.db.execute("UPDATE reply_outbox SET status='outcome_uncertain' WHERE id=?", (f'{TID}:batch:0',))
        self.assertTrue(reply_batch.allowed(self.db, f'{TID}:batch:1'))

    def test_invalid_batch_leaves_no_partial_outbox(self):
        with self.assertRaises(ValueError):
            reply_batch.finish(self.db, TID, [{'type': 'text', 'text': 'ok'}, {'type': 'text', 'text': 'x' * 1501}])
        self.assertEqual(self.db.execute('SELECT count(*) FROM reply_outbox').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0], 'processing')
        with self.assertRaises(ValueError):
            reply_batch.finish(self.db, TID, [])

    def test_finish_requires_the_claimed_task(self):
        reply_batch.finish(self.db, TID, [{'type': 'text', 'text': '一次'}])
        with self.assertRaises(ValueError):
            reply_batch.finish(self.db, TID, [{'type': 'text', 'text': '重复'}])


class AstrbotDispatchTests(unittest.TestCase):
    def setUp(self):
        self.store = LiveStore(':memory:')
        self.db = self.store.db
        self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                        (TID, 'owner', 1, '{"kind":"text","raw_text":"你好"}'))
        self.db.commit()
        self.config = {'provider': 'astrbot', 'astrbot': {'session_id': SID, 'config_id': 'default'}}
        self.patches = [patch.object(ed, 'target_config', return_value=self.config),
                        patch('app.astrbot_client.state', return_value=READY),
                        patch.object(aw, 'busy', return_value=False),
                        patch('app.workflow_protocol.astrbot_reader',return_value={'ready':True}),
                        patch.object(ed, 'envelope', return_value='[抖音私信] test')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.db.close()

    def test_queues_astrbot_and_records_destination_snapshot(self):
        with patch.object(aw, 'submit', return_value='astrbot-' + TID) as submit:
            self.assertEqual(ed.dispatch_one(self.db)['status'], 'queued')
            self.assertIsNone(ed.dispatch_one(self.db))
        submit.assert_called_once_with(TID, {'session_id': SID, 'config_id': 'default'})
        detail = json.loads(self.db.execute('SELECT detail FROM chat_dispatch').fetchone()[0])
        self.assertEqual(detail, {'provider': 'astrbot', 'session_id': SID, 'config_id': 'default'})

    def test_offline_service_keeps_task_unreserved_and_never_falls_back(self):
        with patch('app.astrbot_client.state', return_value={'online': False, 'sessions': [], 'configs': []}), \
                patch.object(aw, 'submit') as submit:
            with self.assertRaises(ValueError):
                ed.dispatch_one(self.db)
        submit.assert_not_called()
        self.assertEqual(self.db.execute('SELECT count(*) FROM chat_dispatch').fetchone()[0], 0)

    def test_removed_session_or_config_is_rejected_before_reservation(self):
        for state in ({**READY, 'sessions': []}, {**READY, 'configs': []}):
            with patch('app.astrbot_client.state', return_value=state):
                with self.assertRaises(ValueError):
                    ed.dispatch_one(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM chat_dispatch').fetchone()[0], 0)

    def test_busy_worker_skips_without_reserving(self):
        with patch.object(aw, 'busy', return_value=True), patch.object(aw, 'submit') as submit:
            self.assertIsNone(ed.dispatch_one(self.db))
        submit.assert_not_called()
        self.assertEqual(self.db.execute('SELECT count(*) FROM chat_dispatch').fetchone()[0], 0)


class ConsoleAstrbotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.file = Path(self.temp.name) / 'target.json'
        self.original = {'mode': 'explicit', 'thread_id': cs.DEFAULT_THREAD,
                         'candidates': [cs.DEFAULT_THREAD], 'extra': 'preserved', 'provider': 'codex'}
        self.file.write_text(json.dumps(self.original), encoding='utf-8')
        self.p = patch.object(cs, 'TARGET_FILE', self.file)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.temp.cleanup()

    def test_switch_saves_session_and_persona_and_keeps_codex_settings(self):
        with patch('app.astrbot_client.state', return_value=READY):
            result = cs.set_agent('astrbot', SID, 'default')
        self.assertEqual(result['provider'], 'astrbot')
        self.assertEqual(result['effective'], SID)
        config = json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(config['astrbot'], {'session_id': SID, 'config_id': 'default'})
        self.assertEqual(config['thread_id'], self.original['thread_id'])
        self.assertEqual(config['extra'], 'preserved')
        self.assertEqual(cs.set_agent('codex')['provider'], 'codex')
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['astrbot']['session_id'], SID)

    def test_unavailable_astrbot_selection_does_not_change_config(self):
        for state in ({**READY, 'sessions': []}, {**READY, 'configs': []}, {**READY, 'online': False}):
            with patch('app.astrbot_client.state', return_value=state):
                with self.assertRaises(ValueError):
                    cs.set_agent('astrbot', SID, 'default')
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8')), self.original)

    def test_target_state_reports_astrbot_session_and_title(self):
        config = dict(self.original, provider='astrbot', astrbot={'session_id': SID, 'config_id': 'default'})
        self.file.write_text(json.dumps(config), encoding='utf-8')
        state = cs.target_state()
        self.assertEqual(state['provider'], 'astrbot')
        self.assertEqual(state['effective'], SID)
        self.assertEqual(state['astrbot_config_id'], 'default')
        self.assertTrue(state['effective_title'])

    def test_astrbot_state_marks_selected_session(self):
        config = dict(self.original, provider='astrbot', astrbot={'session_id': SID, 'config_id': 'default'})
        self.file.write_text(json.dumps(config), encoding='utf-8')
        with patch('app.astrbot_client.state', return_value=dict(READY)):
            state = cs.astrbot_state()
        self.assertTrue(state['online'])
        self.assertEqual(state['selected_session_id'], SID)
        self.assertEqual(state['selected_title'], '抖音私信助手')
        self.assertEqual(state['provider'], 'astrbot')


class ReviewParsingTests(unittest.TestCase):
    def test_accepts_plain_fenced_and_prose_wrapped_json(self):
        self.assertEqual(aw.parse_review('{"text":"画面稳"}'), {'text': '画面稳'})
        self.assertEqual(aw.parse_review('```json\n{"text":"画面稳"}\n```'), {'text': '画面稳'})
        self.assertEqual(aw.parse_review('简评如下：{"text":"画面稳"} 以上。'), {'text': '画面稳'})
        self.assertEqual(aw.parse_review('{"text":null}'), {'text': None})

    def test_rejects_non_json(self):
        self.assertIsNone(aw.parse_review('这把拉花挺稳的。'))
        self.assertIsNone(aw.parse_review(''))
        self.assertIsNone(aw.parse_review('{"text":'))


class AstrbotWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.root = Path(self.temp.name)
        self.patch_root = patch.object(aw, 'ROOT', self.root)
        self.patch_root.start()
        from app.sdk_receiver import OWNER
        self.owner = OWNER
        self.store = LiveStore(self.root / 'assistant.sqlite3')
        with self.store.db:
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS chat_dispatch (
                task_id TEXT PRIMARY KEY, attempted REAL NOT NULL, status TEXT NOT NULL,
                queue_id TEXT, detail TEXT)''')
            self.store.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                                  (TID, OWNER, 1, json.dumps({'kind': 'text', 'raw_text': '你好'}, ensure_ascii=False),
                                   'pending'))
            self.store.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?,?,?)',
                                  (TID, 1, 'attempted', None,
                                   json.dumps({'provider': 'astrbot', 'session_id': SID, 'config_id': 'default'})))
        self.store.db.close()

    def tearDown(self):
        self.patch_root.stop()
        self.temp.cleanup()

    def target(self):
        return {'session_id': SID, 'config_id': 'default'}

    def reopen(self):
        return LiveStore(self.root / 'assistant.sqlite3')

    def test_reply_queued_once_and_dispatch_processed(self):
        with patch.object(aw.client, 'chat', return_value=[{'type': 'text', 'text': '好的，收到'}]) as chat:
            aw.run(TID, self.target())
        chat.assert_called_once()
        store = self.reopen()
        try:
            self.assertEqual(store.db.execute('SELECT status FROM live_tasks').fetchone()[0], 'done')
            self.assertEqual(store.db.execute('SELECT text FROM reply_outbox').fetchone()[0], '好的，收到')
            self.assertEqual(store.db.execute('SELECT status FROM chat_dispatch').fetchone()[0], 'processed')
        finally:
            store.db.close()
        report = json.loads((self.root / 'astrbot-receipts' / (TID + '.json')).read_text(encoding='utf-8'))
        self.assertEqual(report['state'], 'reply_queued')
        self.assertEqual(report['response_parts'], [{'type': 'text', 'text': '好的，收到'}])

    def test_model_failure_is_uncertain_and_never_retried(self):
        with patch.object(aw.client, 'chat', side_effect=ConnectionError('SSE cut')):
            aw.run(TID, self.target())
        store = self.reopen()
        try:
            self.assertEqual(store.db.execute('SELECT status FROM live_tasks').fetchone()[0], 'pending')
            self.assertEqual(store.db.execute('SELECT count(*) FROM reply_outbox').fetchone()[0], 0)
            self.assertEqual(store.db.execute('SELECT status FROM chat_dispatch').fetchone()[0], 'outcome_uncertain')
            self.assertIsNone(store.db.execute("SELECT 1 FROM live_tasks WHERE status='processing'").fetchone())
        finally:
            store.db.close()

    def test_unverified_task_is_not_marked_uncertain(self):
        store = self.reopen()
        with store.db:
            store.db.execute("DELETE FROM chat_dispatch")
        store.db.close()
        with patch.object(aw.client, 'chat') as chat:
            aw.run(TID, self.target())
        chat.assert_not_called()
        store = self.reopen()
        try:
            self.assertEqual(store.db.execute('SELECT status FROM live_tasks').fetchone()[0], 'pending')
            self.assertEqual(store.db.execute('SELECT count(*) FROM chat_dispatch').fetchone()[0], 0)
        finally:
            store.db.close()


class FakeProcess:
    def __init__(self):
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return None

    def terminate(self):
        self.terminated += 1

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed += 1


class ConsoleQuitTests(unittest.TestCase):
    def test_stop_children_terminates_tracked_even_when_powershell_fails(self):
        fake = FakeProcess()
        with patch.object(cs, '_children', [fake]), \
                patch.object(cs.subprocess, 'run', side_effect=OSError('WinError 6')):
            cs.stop_children()
        self.assertEqual(fake.terminated, 1)
        self.assertEqual(cs._children, [])

    def test_stop_children_kills_a_tracked_child_that_ignores_terminate(self):
        class Stubborn(FakeProcess):
            def wait(self, timeout=None):
                raise cs.subprocess.TimeoutExpired('child', timeout)

        fake = Stubborn()
        with patch.object(cs, '_children', [fake]), patch.object(cs.subprocess, 'run'):
            cs.stop_children()
        self.assertEqual(fake.killed, 1)

    def test_quit_schedules_exit_even_if_stop_children_raises(self):
        handler = object.__new__(cs.Handler)
        handler.path = '/api/app/quit'
        handler.headers = {'Host': '127.0.0.1:8793', 'Content-Type': 'application/json', 'Content-Length': '0'}
        handler.rfile = io.BytesIO(b'{}')
        handler._send = Mock()
        delays = []
        with patch.object(cs, 'stop_children', side_effect=OSError('bad handle')), \
                patch.object(cs.threading, 'Timer', side_effect=lambda delay, fn: delays.append(delay) or Mock()):
            handler.do_POST()
        self.assertIn(20, delays)
        self.assertIn(0.4, delays)
        handler._send.assert_called_once()
        self.assertEqual(handler._send.call_args.args[0], 200)


class AstrbotHealthTests(unittest.TestCase):
    def test_agent_module_reports_astrbot_label(self):
        workflow = {'receiver': {'age_seconds': 1, 'state': 'listening'},
                    'sdk': {'online': True, 'uid': '__ACCOUNT_UID__'}}
        target = {'provider': 'astrbot', 'effective': SID}
        policies = {'dm': {'enabled': True, 'min_send_gap_seconds': 10}, 'shared_comment': {'enabled': True}}
        data = {'ok': True, 'counts': {}, 'media': [], 'comments': [], 'undispatched': 0, 'dispatch_uncertain': 0}
        deps = {'douk': True, 'ffmpeg': True, 'sensevoice': True}
        agent = {'available': True, 'title': '抖音私信助手'}
        modules = wh.assess(workflow, target, policies, data, deps, agent, {}, now=1)
        agent_module = next(m for m in modules if m['id'] == 'agent')
        self.assertEqual(agent_module['state'], 'idle')
        self.assertEqual(agent_module['metric'], 'AstrBot')
        self.assertIn('抖音私信助手', agent_module['detail'])


if __name__ == '__main__':
    unittest.main()
