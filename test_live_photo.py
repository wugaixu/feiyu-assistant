import json,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import Future
from PIL import Image
import imageio_ffmpeg
from app import shared_video as shared,sdk_media
from app.douk_downloader_worker import collect_output,ROOT


class LivePhotoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)

    def fixture(self,index,prefix='a-实况-work',data=b'fake'):
        p=self.directory/f'{prefix}_{index}.mp4';p.write_bytes(data);return p

    def test_numbered_live_clips_keep_numeric_order_and_sdk_shape(self):
        for i in (4,2,1,3):self.fixture(i)
        value=collect_output(self.directory,set(),'1234567890123456')
        self.assertEqual(value['media_type'],'live_photo_collection');self.assertEqual(value['clip_count'],4)
        self.assertEqual([Path(x['path']).stem[-1] for x in value['files']],list('1234'))
        with patch('app.media_capture.validate_video') as verify:
            shared.verify_douk_output(value,self.directory)
        self.assertEqual(verify.call_count,4)
        with patch.object(sdk_media,'ROOT',self.directory),patch.object(shared,'download',return_value=value):
            record=sdk_media.capture(None,'task',{'kind':'video_card','sdk_content':{'share':{'itemId':'1234567890123456'}}})
        self.assertEqual(record['status'],'saved');self.assertEqual(len(record['files']),4)
        self.assertTrue(all(f['decode_verified'] for f in record['files']))

    def test_unrelated_mp4s_and_incomplete_sequences_rejected(self):
        self.fixture(1,prefix='plain');self.fixture(2,prefix='plain')
        with self.assertRaises(ValueError):collect_output(self.directory,set(),'id')
        self.temp.cleanup();self.directory.mkdir()
        self.fixture(1);self.fixture(3)
        with self.assertRaises(ValueError):collect_output(self.directory,set(),'id')

    def test_mixed_work_prefix_and_escaped_output_rejected(self):
        self.fixture(1);self.fixture(2,prefix='b-实况-other')
        with self.assertRaises(ValueError):collect_output(self.directory,set(),'id')
        with self.assertRaises(ValueError):shared.verify_douk_output({'status':'saved','media_type':'live_photo_collection','files':[{'path':str(self.directory/'a-实况-work_1.mp4')}]},self.directory/'subdir')

    def test_corrupt_clip_is_not_accepted(self):
        self.fixture(1)
        with self.assertRaises(ValueError):shared.verify_douk_output(collect_output(self.directory,set(),'id'),self.directory)

    def test_frames_job_uses_every_segment(self):
        value={'task_id':'live','media_type':'live_photo_collection','files':[{'status':'saved','decode_verified':True,'path':str(self.fixture(1))}]}
        with patch.object(shared,'_jobs',{}),patch.object(shared,'_pool') as pool:
            pool.submit.return_value=Future();shared.start_frames(value)
        self.assertIs(pool.submit.call_args.args[0],shared.build_collection_frames)

    def test_sdk_declared_missing_clips_not_marked_successful(self):
        value={'status':'saved','media_type':'live_photo_collection','files':[{'path':str(self.fixture(1))}],'clip_count':1}
        with patch.object(sdk_media,'ROOT',self.directory),patch.object(shared,'download',return_value=value):
            record=sdk_media.capture(None,'partial',{'kind':'video_card','sdk_content':{'kind':'share','share':{'itemId':'1234567890123456'},'value':{'is_live_photo':1,'image_count':4}}})
        self.assertEqual(record['status'],'unavailable');self.assertEqual(record['expected_clip_count'],4)
        self.assertEqual(len(record['files']),1)

    def test_real_portrait_and_landscape_clips_all_sampled_with_bounded_budget(self):
        for i,size in enumerate(('72x96','96x72','72x96','96x72'),1):
            p=self.directory/f'a-实况-work_{i}.mp4'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-f','lavfi','-i',f'color=c=red:s={size}:r=6','-t','0.6','-c:v','libx264','-pix_fmt','yuv420p',str(p)],capture_output=True,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        value=shared.verify_douk_output(collect_output(self.directory,set(),'id'),self.directory)
        with patch('app.resource_storage.resource_dir',return_value=self.directory/'analysis'):
            result=shared.build_collection_frames(value['files'],None)
        self.assertEqual(result['clip_count'],4);self.assertEqual(len(result['frames']),9)
        self.assertEqual([len(s['frames']) for s in result['segments']],[3,2,2,2])
        self.assertTrue(all(Path(p).is_file() for p in result['frames']))
        with Image.open(result['contact_sheet']) as sheet:self.assertEqual(sheet.size,(1440,978))


if __name__=='__main__':unittest.main()
