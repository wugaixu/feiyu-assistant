import io,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app import sdk_login,console_server

class SDKLoginTests(unittest.TestCase):
    def test_qr_is_only_shown_when_current_and_fresh(self):
        with tempfile.TemporaryDirectory(dir=r'__TEMP_PARENT__') as d:
            root=Path(d);(root/'sdk-login.png').write_bytes(b'\x89PNG\r\n\x1a\nexample')
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            for stamp,shown in [(time.time(),True),(time.time()-1000,False)]:
                response.read.return_value=json.dumps({'online':False,'note':'qr_required','qr_created_at':stamp}).encode()
                with patch.object(sdk_login,'ROOT',root),patch.object(sdk_login.OPENER,'open',return_value=response):result=sdk_login.state()
                self.assertEqual('qr_image' in result,shown)
    def test_foreign_origin_and_phone_cannot_trigger_login(self):
        from app.whale_channel import POST_ROUTES,GET_ROUTES
        self.assertNotIn('/api/douyin-login',POST_ROUTES);self.assertNotIn('/api/douyin-login',GET_ROUTES)
        h=object.__new__(console_server.Handler);h.path='/api/douyin-login';h.headers={'Host':'127.0.0.1:8793','Origin':'https://bad.invalid','Content-Type':'application/json','Content-Length':'2'};h.rfile=io.BytesIO(b'{}');h._send=Mock()
        with patch.object(sdk_login,'start') as start:h.do_POST()
        start.assert_not_called();self.assertEqual(h._send.call_args.args[0],403)

if __name__=='__main__':unittest.main()
