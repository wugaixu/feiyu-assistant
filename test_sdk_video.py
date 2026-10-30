import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from types import SimpleNamespace
from app import sdk_video

class DirectVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=sdk_video.ROOT);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        root_patch=patch.object(sdk_video,'ROOT',self.root);root_patch.start();self.addCleanup(root_patch.stop)
    def response(self,key='0'*32):
        result=MagicMock();result.__enter__.return_value=result
        result.read.return_value=json.dumps({'ok':True,'url':'https://example.douyinvod.com/video','key':key}).encode()
        return result
    def test_resolve_download_validate_without_browser(self):
        def run(argv,**kwargs):
            Path(argv[-1]).write_bytes(b'video fixture')
            self.assertIsInstance(argv,list);self.assertNotIn('shell',kwargs)
            self.assertIn('-protocol_whitelist',argv)
            return SimpleNamespace(returncode=0)
        with patch('app.sdk_video.urllib.request.urlopen',return_value=self.response()),patch.object(sdk_video,'resource_bytes',return_value=b'encrypted'),patch.object(sdk_video.subprocess,'run',side_effect=run),patch.object(sdk_video,'validate_video') as verify:
            result=sdk_video.download('12345',self.root)
        self.assertTrue(result['decode_verified']);verify.assert_called_once()
        self.assertNotIn('key',result)
    def test_invalid_message_or_key_rejected(self):
        with self.assertRaises(ValueError):sdk_video.download('not-an-id',self.root)
        with patch('app.sdk_video.urllib.request.urlopen',return_value=self.response('bad')):
            with self.assertRaises(ValueError):sdk_video.download('12345',self.root)

if __name__=='__main__':unittest.main()
