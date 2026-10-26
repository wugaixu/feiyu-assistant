import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from PIL import Image
from app.douk_downloader_worker import collect_output,ROOT
from app.shared_video import download
from app import sdk_media

class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
    def picture(self,name):
        p=self.directory/name;Image.new('RGB',(12,8)).save(p);return p
    def test_single_picture_passes_adapter_and_sdk_capture(self):
        p=self.picture('test-图集-test_1.jpeg')
        result=collect_output(self.directory,set(),'1234567890123456')
        self.assertEqual(result['media_type'],'image_gallery');self.assertEqual(result['image_count'],1)
        with patch('app.shared_video.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(result))):
            verified=download(None,{'video_id':'1234567890123456'},self.directory,backend='douk')
        self.assertEqual(verified['status'],'saved');self.assertNotIn('decode_verified',verified['files'][0])
        with patch.object(sdk_media,'ROOT',self.directory),patch('app.shared_video.download',return_value=verified):
            record=sdk_media.capture(None,'task',{'kind':'video_card','sdk_content':{'share':{'itemId':'1234567890123456'}}})
        self.assertEqual(record['media_type'],'image_gallery');self.assertEqual(record['files'][0]['path'],str(p))
    def test_multiple_pictures_keep_numeric_order(self):
        for i in (10,2,1):self.picture(f'test-图集-test_{i}.jpeg')
        result=collect_output(self.directory,set(),'id')
        self.assertEqual([Path(f['path']).stem.rsplit('_',1)[1] for f in result['files']],['1','2','10'])
    def test_cover_is_not_a_gallery_and_invalid_image_rejected(self):
        self.picture('test-视频-cover.jpeg')
        with self.assertRaises(ValueError):collect_output(self.directory,set(),'id')
        (self.directory/'test-图集-test_1.jpeg').write_bytes(b'not an image')
        with self.assertRaises(Exception):collect_output(self.directory,set(),'id')
    def test_video_keeps_video_route(self):
        (self.directory/'video.mp4').write_bytes(b'fixture')
        self.assertEqual(collect_output(self.directory,set(),'id')['media_type'],'video')

    def test_partial_gallery_is_kept_and_states_the_shortfall(self):
        self.picture('test-图集-test_1.jpeg')
        result=collect_output(self.directory,set(),'1234567890123456',expected_images=2)
        self.assertEqual(result['media_type'],'image_gallery')
        self.assertEqual(result['image_count'],1)
        self.assertEqual(result['expected_images'],2)
        self.assertTrue(result['partial'])
        self.assertIn('1 of 2',result['completeness'])

    def test_complete_gallery_is_not_partial(self):
        self.picture('test-图集-test_1.jpeg')
        self.picture('test-图集-test_2.jpeg')
        result=collect_output(self.directory,set(),'1234567890123456',expected_images=2)
        self.assertFalse(result['partial'])
        self.assertEqual(result['image_count'],2)

    def test_timeout_salvage_keeps_downloaded_files(self):
        from app.douk_downloader_worker import salvage_timeout
        self.picture('test-图集-test_1.jpeg')
        result=salvage_timeout(self.directory,set(),'1234567890123456',2)
        self.assertTrue(result['douk_timeout'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['files'][0]['status'],'saved')

    def test_sdk_capture_records_partial_gallery(self):
        p=self.picture('test-图集-test_1.jpeg')
        partial={'status':'saved','media_type':'image_gallery','files':[{'status':'saved','path':str(p)}],
                 'image_count':1,'expected_images':2,'partial':True,'completeness':'Downloaded 1 of 2 images','douk_timeout':True}
        with patch.object(sdk_media,'ROOT',self.directory),patch('app.shared_video.download',return_value=partial):
            record=sdk_media.capture(None,'task',{'kind':'video_card','sdk_content':{
                'share':{'itemId':'1234567890123456'},'value':{'image_count':2}}})
        self.assertTrue(record['partial'])
        self.assertEqual(record['expected_images'],2)
        self.assertEqual(record['status'],'saved')
        self.assertEqual(len(record['files']),1)

if __name__=='__main__':unittest.main()
