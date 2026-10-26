import json
import sqlite3
import unittest
import time
from unittest.mock import patch
from app import douyin_toolkit as kit
from douyin_mcp import handle


class Response:
    status = 200
    def __init__(self, body):
        self.data = body
        self.disposed = False
    def body(self): return self.data
    def dispose(self): self.disposed = True


class Context:
    def __init__(self, response):
        self.request = self
        self.response = response
        self.calls = 0
    def get(self, url, **kwargs):
        self.calls += 1
        if url != kit.ENDPOINT: raise AssertionError('Unexpected URL')
        return self.response


class ToolkitTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        kit.schema(self.db)
        self.addCleanup(self.db.close)

    def test_normalization_excludes_user_and_comment_objects_and_credentials(self):
        data = {'data': [{'aweme_info': {'aweme_id': '7689005697501179897', 'desc': 'x'*900,
                 'video': {'duration': 900, 'play_addr': {'url_list': ['secret-signed-url']}},
                 'author': {'nickname': 'Author', 'sec_uid': 'private-id'}}},
                 {'aweme_id': '7689005697501179897', 'text': 'comment'}]}
        rows = kit.normalize_items(data, 3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['description']), 600)
        self.assertNotIn('secret', json.dumps(rows))
        self.assertNotIn('private-id', json.dumps(rows))
        self.assertFalse(rows[0]['transcript_available'])

    def test_empty_or_verify_response_cannot_be_claimed_successful(self):
        for body, expected in [(b'', 'unavailable'), (b'{"status_code":0,"data":{"verify_check":{}}}', 'needs_verification'),
                               (b'<html>login</html>', 'unavailable'), (b'{}', 'unavailable')]:
            response = Response(body)
            output = kit.search_http(Context(response), 'Codex', 3)
            self.assertEqual(output['status'], expected)
            self.assertFalse(output['automatic_retry'])
            self.assertTrue(response.disposed)

    def test_attempted_operations_are_not_reexecuted(self):
        self.db.execute('INSERT INTO douyin_tool_operations VALUES(?,?,?,?,?,NULL)', ('a'*32, 'search', '{}', 'attempted', 1))
        self.db.commit()
        context = Context(Response(b''))
        kit.tick(self.db, context)
        self.assertEqual(context.calls, 0)

    def test_read_exception_is_persisted_without_automatic_retry(self):
        self.db.execute('INSERT INTO douyin_tool_operations VALUES(?,?,?,?,?,NULL)',('d'*32,'comments','{}','pending',1));self.db.commit()
        with patch('app.douyin_toolkit.execute_read',side_effect=RuntimeError('private request information')):
            kit.tick(self.db,None)
        value=kit.result(self.db,'d'*32)
        self.assertEqual(value['status'],'failed')
        self.assertNotIn('private',json.dumps(value))
        self.assertFalse(value['result']['automatic_retry'])

    def test_comment_items_do_not_enter_video_cache(self):
        args=json.dumps({'video_id':'7695717050077612699'})
        self.db.execute('INSERT INTO douyin_tool_operations VALUES(?,?,?,?,?,NULL)',('c'*32,'comments',args,'pending',1));self.db.commit()
        value={'status':'done','items':[{'comment_id':'123','text':'Actual audience comment'}]}
        with patch('app.douyin_comments.fetch',return_value=value):
            kit.tick(self.db,None)
        self.assertEqual(kit.result(self.db,'c'*32)['status'],'done')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM douyin_video_cache').fetchone()[0],0)

    def test_arbitrary_keywords_and_tool_arguments_rejected(self):
        with patch('app.video_rounds.config', return_value={'keywords':['Codex'], 'tags':[]}):
            with self.assertRaises(ValueError): kit.submit_search(self.db, 'arbitrary')
            output = kit.submit_search(self.db, 'Codex', 3)
            with self.assertRaises(ValueError): kit.submit_search(self.db, 'Codex', 3)
            self.assertEqual(kit.result(self.db, output['operation_id'])['status'], 'pending')
        with self.assertRaises(ValueError): kit.call(self.db, 'douyin_status', {'shell':'anything'})

    def test_other_read_tools_do_not_consume_search_budget(self):
        now=time.time()
        for i in range(35):
            self.db.execute('INSERT INTO douyin_tool_operations VALUES(?,?,?,?,?,NULL)',(f'{i:032x}','comments','{}','done',now))
        self.db.commit()
        with patch('app.video_rounds.config',return_value={'keywords':['Codex'],'tags':[]}):
            result=kit.submit_search(self.db,'Codex',3)
        self.assertEqual(result['status'],'pending')

    def test_mcp_handshake_notifications_and_errors(self):
        init = handle(self.db, {'jsonrpc':'2.0', 'id':1, 'method':'initialize'})
        self.assertIn('tools', init['result']['capabilities'])
        self.assertIsNone(handle(self.db, {'jsonrpc':'2.0', 'method':'notifications/initialized'}))
        listing = handle(self.db, {'jsonrpc':'2.0', 'id':2, 'method':'tools/list'})
        self.assertEqual(len(listing['result']['tools']), len(kit.TOOLS))
        error = handle(self.db, {'jsonrpc':'2.0', 'id':3, 'method':'tools/call', 'params':{'name':'shell'}})
        self.assertTrue(error['result']['isError'])

    def test_comment_cannot_bypass_claim_or_evidence_review(self):
        self.db.execute('CREATE TABLE live_tasks(id TEXT PRIMARY KEY,status TEXT)')
        self.db.execute('INSERT INTO live_tasks VALUES(?,?)',('a'*32,'processing'))
        self.db.commit()
        with self.assertRaises(ValueError):
            kit.submit_comment(self.db,'b'*32,'7695717050077612699','A comment')
        with self.assertRaisesRegex(ValueError,'Interesting evidence review'):
            kit.submit_comment(self.db,'a'*32,'7695717050077612699','A comment')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM video_browser_commands').fetchone()[0],0)

    def test_interesting_review_cannot_rely_on_only_metadata(self):
        self.db.execute('CREATE TABLE live_tasks(id TEXT PRIMARY KEY,status TEXT)')
        self.db.execute('INSERT INTO live_tasks VALUES(?,?)',('a'*32,'processing'))
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'Metadata alone'):
            kit.record_review(self.db,'a'*32,'1111111111111111111','interesting','summary','reason')
        error = handle(self.db, {'jsonrpc':'2.0', 'id':4, 'method':'tools/call', 'params':[]})
        self.assertTrue(error['result']['isError'])


if __name__ == '__main__': unittest.main()
