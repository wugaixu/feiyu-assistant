import io
import json
import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from app import event_dispatch as ed,console_server as cs,dsh_client
from app.live_service import LiveStore

BASE=Path(r'__RUNTIME_ROOT__')
SID='session-5e010aa6-8bd0-41f9-af47-6770566c14d6'
TID='f'*32


class DshDispatchTests(unittest.TestCase):
    def setUp(self):
        self.store=LiveStore(':memory:');self.db=self.store.db
        self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',(TID,'owner',1,'{"kind":"text","raw_text":"你好"}'));self.db.commit()
        self.config={'provider':'dsh','dsh':{'session_id':SID}}
        self.patches=[patch.object(ed,'target_config',return_value=self.config),patch.object(dsh_client,'sessions',return_value=[{'id':SID}]),patch.object(dsh_client,'capabilities',return_value={'protocol_version':4}),patch.object(ed,'envelope',return_value='[抖音私信] test')]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.db.close()

    def test_dsh_does_not_resolve_or_launch_codex_and_deduplicates(self):
        with patch.object(ed,'codex_executable',side_effect=AssertionError('must not resolve')),patch.object(dsh_client,'prompt',return_value='douyin-'+TID) as prompt:
            self.assertEqual(ed.dispatch_one(self.db)['status'],'queued')
            self.assertIsNone(ed.dispatch_one(self.db))
        prompt.assert_called_once();args=prompt.call_args.args
        self.assertEqual(args[:2],(SID,TID));self.assertEqual(args[2],'[抖音私信] test')
        detail=json.loads(self.db.execute('SELECT detail FROM chat_dispatch').fetchone()[0])
        self.assertEqual(detail,{'provider':'dsh','session_id':SID})

    def test_offline_keeps_task_unreserved(self):
        with patch.object(dsh_client,'sessions',side_effect=ConnectionError('offline')),patch.object(dsh_client,'prompt') as prompt:
            with self.assertRaises(ConnectionError):ed.dispatch_one(self.db)
        prompt.assert_not_called();self.assertEqual(self.db.execute('SELECT count(*) FROM chat_dispatch').fetchone()[0],0)

    def test_missing_session_not_reserved(self):
        with patch.object(dsh_client,'sessions',return_value=[]):
            with self.assertRaises(ValueError):ed.dispatch_one(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM chat_dispatch').fetchone()[0],0)

    def test_old_protocol_keeps_task_unreserved(self):
        with patch.object(dsh_client,'capabilities',return_value={'version':2}):
            with self.assertRaises(ValueError):ed.dispatch_one(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM chat_dispatch').fetchone()[0],0)

    def test_uncertain_outcome_never_retried_even_after_provider_switch(self):
        with patch.object(dsh_client,'prompt',side_effect=ConnectionError('timeout')) as prompt:
            self.assertEqual(ed.dispatch_one(self.db)['status'],'outcome_uncertain')
            self.config['provider']='codex'
            self.assertIsNone(ed.dispatch_one(self.db))
        prompt.assert_called_once()

    def test_explicit_rejection_is_not_misreported_as_transport_uncertainty(self):
        with patch.object(dsh_client,'prompt',side_effect=dsh_client.DeliveryRejected('invalid_request')):
            self.assertEqual(ed.dispatch_one(self.db)['status'],'rejected')
            self.assertIsNone(ed.dispatch_one(self.db))
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'pending')

    def test_unknown_provider_no_fallback(self):
        self.config['provider']='invalid'
        with self.assertRaises(ValueError):ed.dispatch_one(self.db)

    def test_provider_choice_remains_snapshot_during_enqueue(self):
        def prompt(*args):
            self.config['provider']='codex'
            self.assertEqual(args[0],SID)
            return 'douyin-'+TID
        with patch.object(dsh_client,'prompt',side_effect=prompt):
            self.assertEqual(ed.dispatch_one(self.db)['status'],'queued')
        self.assertEqual(json.loads(self.db.execute('SELECT detail FROM chat_dispatch').fetchone()[0])['provider'],'dsh')


class ConsoleAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=BASE)
        self.file=Path(self.temp.name)/'target.json'
        self.original={'mode':'explicit','thread_id':cs.DEFAULT_THREAD,'candidates':[cs.DEFAULT_THREAD],'extra':'preserved'}
        self.file.write_text(json.dumps(self.original),encoding='utf-8')
        self.p=patch.object(cs,'TARGET_FILE',self.file);self.p.start()

    def tearDown(self):self.p.stop();self.temp.cleanup()

    def test_switch_and_back_preserves_codex_and_resource_settings(self):
        with patch.object(dsh_client,'sessions',return_value=[{'id':SID}]):
            result=cs.set_agent('dsh',SID)
        self.assertEqual(result['effective'],SID)
        result=cs.set_agent('codex')
        config=json.loads(self.file.read_text())
        self.assertEqual(config['thread_id'],self.original['thread_id'])
        self.assertEqual(config['extra'],'preserved');self.assertEqual(config['dsh']['session_id'],SID)

    def test_unavailable_selection_does_not_change_config(self):
        with patch.object(dsh_client,'sessions',return_value=[]):
            with self.assertRaises(ValueError):cs.set_agent('dsh',SID)
        self.assertEqual(json.loads(self.file.read_text()),self.original)

    def test_foreign_browser_origin_cannot_switch_provider(self):
        h=object.__new__(cs.Handler);h.path='/api/agent'
        h.headers={'Host':'127.0.0.1:8793','Origin':'https://example.invalid','Content-Type':'application/json'}
        h.rfile=io.BytesIO(b'{}');h._send=Mock()
        with patch.object(cs,'set_agent') as setter:h.do_POST()
        setter.assert_not_called();self.assertEqual(h._send.call_args.args[0],403)

    def test_client_preserves_safe_http_rejection_and_keeps_unknown_errors_uncertain(self):
        import urllib.error
        config=Path(self.temp.name)/'dsh-bridge.json';config.write_text(json.dumps({'token':'x'*48}))
        for code,expected in [('invalid_request',dsh_client.DeliveryRejected),('connector_request_failed',ConnectionError)]:
            failure=urllib.error.HTTPError('http://127.0.0.1:8794/prompt',400,'Bad request',{},io.BytesIO(json.dumps({'error':code}).encode()))
            with patch.object(dsh_client,'ROOT',Path(self.temp.name)),patch.object(dsh_client.OPENER,'open',side_effect=failure):
                with self.assertRaises(expected) as caught:dsh_client.request('/prompt',{})
                if expected is ConnectionError:self.assertNotIsInstance(caught.exception,dsh_client.DeliveryRejected)

    def test_client_acceptance_requires_matching_receipt(self):
        self.prepare_task()
        with patch.object(dsh_client,'ROOT',Path(self.temp.name)),patch.object(dsh_client,'request',return_value={'accepted':True,'request_id':'wrong'}):
            with self.assertRaises(ConnectionError):dsh_client.prompt(SID,TID,'[抖音私信]')

    def prepare_task(self,owner=None,destination=SID):
        from app.sdk_receiver import OWNER
        db=sqlite3.connect(Path(self.temp.name)/'assistant.sqlite3')
        try:
            db.execute('CREATE TABLE live_tasks(id TEXT,owner TEXT,status TEXT)')
            db.execute('CREATE TABLE chat_dispatch(task_id TEXT,status TEXT,detail TEXT)')
            db.execute('INSERT INTO live_tasks VALUES(?,?,?)',(TID,owner or OWNER,'pending'))
            db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',(TID,'attempted',json.dumps({'provider':'dsh','session_id':destination})))
            db.commit()
        finally:db.close()

    def test_client_blocks_fixture_owner_before_live_request(self):
        self.prepare_task(owner='owner')
        with patch.object(dsh_client,'ROOT',Path(self.temp.name)),patch.object(dsh_client,'request') as send:
            with self.assertRaises(ValueError):dsh_client.prompt(SID,TID,'[抖音私信]')
        send.assert_not_called()

    def test_client_blocks_destination_mismatch(self):
        self.prepare_task(destination='other')
        with patch.object(dsh_client,'ROOT',Path(self.temp.name)),patch.object(dsh_client,'request') as send:
            with self.assertRaises(ValueError):dsh_client.prompt(SID,TID,'[抖音私信]')
        send.assert_not_called()


if __name__=='__main__':unittest.main()
