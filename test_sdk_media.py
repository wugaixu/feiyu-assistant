import io,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import sdk_media

class SDKMediaTests(unittest.TestCase):
    def test_emoji_saved_and_verified(self):
        image=io.BytesIO();Image.new('RGB',(8,9)).save(image,format='PNG')
        with tempfile.TemporaryDirectory(dir=sdk_media.ROOT) as tmp:
            with patch.object(sdk_media,'ROOT',Path(tmp)),patch.object(sdk_media,'resource_bytes',return_value=image.getvalue()):
                record=sdk_media.capture(None,uuid.uuid4().hex,{'kind':'image','sdk_content':{'kind':'emoji','url':'https://example.invalid/test'}})
                self.assertEqual(record['status'],'saved')
                self.assertEqual(record['files'][0]['size'],[8,9])
    def test_voice_recognition_keeps_file_and_failure_state(self):
        for analysis in ({'status':'done','text':'测试语音','engine':'fixture'}, {'status':'no_speech_recognized','text':''}):
            with tempfile.TemporaryDirectory(dir=sdk_media.ROOT) as tmp:
                with patch.object(sdk_media,'ROOT',Path(tmp)),patch.object(sdk_media,'resource_bytes',return_value=b'audio'),patch('app.video_audio.analyze',return_value=analysis):
                    record=sdk_media.capture(None,uuid.uuid4().hex,{'kind':'audio','sdk_content':{'kind':'audio','audio':{'urls':['https://sf9-sign.douyinstatic.com/douyin-user-audio-file/sample']}}})
                self.assertEqual(record['status'],'saved')
                self.assertTrue(Path(record['files'][0]['path']).is_file())
                self.assertEqual(record['transcript']['status'],'transcribed' if analysis['status']=='done' else analysis['status'])

    def test_voice_engine_failure_does_not_lose_original(self):
        with tempfile.TemporaryDirectory(dir=sdk_media.ROOT) as tmp:
            with patch.object(sdk_media,'ROOT',Path(tmp)),patch.object(sdk_media,'resource_bytes',return_value=b'audio'),patch('app.video_audio.analyze',side_effect=RuntimeError('engine unavailable')):
                record=sdk_media.capture(None,uuid.uuid4().hex,{'kind':'audio','sdk_content':{'kind':'audio','audio':{'urls':['https://sf9-sign.douyinstatic.com/sample']}}})
            self.assertEqual(record['status'],'saved')
            self.assertEqual(record['transcript']['status'],'failed')
            self.assertFalse(record['transcript']['text'])

    def test_voice_cdn_validation_rejects_lookalikes(self):
        from app.media_capture import allowed_url
        self.assertTrue(allowed_url('https://sf9-sign.douyinstatic.com/douyin-user-audio-file/a'))
        for url in ('http://sf9-sign.douyinstatic.com/a','https://douyinstatic.com.evil.invalid/a','https://user@sf9-sign.douyinstatic.com/a','https://127.0.0.1/a'):
            self.assertFalse(allowed_url(url))

    def test_failed_resource_has_manifest(self):
        with tempfile.TemporaryDirectory(dir=sdk_media.ROOT) as tmp:
            with patch.object(sdk_media,'ROOT',Path(tmp)):
                record=sdk_media.capture(None,uuid.uuid4().hex,{'kind':'image','sdk_content':{'kind':'image'}})
                self.assertEqual(record['status'],'unavailable')
                self.assertTrue(Path(record['manifest_path']).exists())

if __name__=='__main__':unittest.main()
