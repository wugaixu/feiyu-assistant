import io,json,unittest
from unittest.mock import Mock,patch
from app.console_server import Handler

class ConsoleStorageTests(unittest.TestCase):
    def handler(self,path,body,origin='http://127.0.0.1:8793'):
        h=object.__new__(Handler);h.path=path
        raw=json.dumps(body).encode();h.headers={'Host':'127.0.0.1:8793','Content-Type':'application/json','Content-Length':str(len(raw))}
        if origin:h.headers['Origin']=origin
        h.rfile=io.BytesIO(raw);h._send=Mock();return h

    def test_save_uses_explicit_confirmation(self):
        h=self.handler('/api/storage',{'config':{'cleanup_enabled':False}})
        with patch('app.resource_storage.save',return_value={}) as save:h.do_POST()
        save.assert_called_once_with({'cleanup_enabled':False},False);self.assertEqual(h._send.call_args.args[0],200)

    def test_foreign_origin_rejected_before_mutation(self):
        h=self.handler('/api/storage/cleanup',{'confirmed':True,'token':'x'},'https://example.invalid')
        with patch('app.resource_storage.run_preview') as run:h.do_POST()
        run.assert_not_called();self.assertEqual(h._send.call_args.args[0],403)

    def test_delete_requires_confirmation(self):
        h=self.handler('/api/storage/cleanup',{'token':'x'})
        with patch('app.resource_storage.run_preview') as run:h.do_POST()
        run.assert_not_called();self.assertEqual(h._send.call_args.args[0],400)

    def test_preview_does_not_execute_cleanup(self):
        h=self.handler('/api/storage/preview',{})
        with patch('app.resource_storage.preview',return_value={'items':[]}) as preview,patch('app.resource_storage.run_preview') as run:h.do_POST()
        preview.assert_called_once();run.assert_not_called()

if __name__=='__main__':unittest.main()
