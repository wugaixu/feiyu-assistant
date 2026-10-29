import hashlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import resource_storage as storage,task_context,visual_delivery,media_capture,whale_channel,whale_upload
from app.event_dispatch import envelope
import test_whale_channel as fixtures

class VisualDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=storage.RUNTIME);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for obj,name,value in [(storage,'RUNTIME',self.root),(storage,'CONFIG',self.root/'resource-storage.json'),(task_context,'PACKETS',self.root/'task-context')]:
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
    def image(self):
        folder=self.root/'media'/('b'*32);folder.mkdir(parents=True)
        path=folder/'one.png';Image.new('RGB',(8,9),'blue').save(path)
        return {'kind':'image','status':'saved','files':[{'path':str(path),'status':'saved','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]}
    def test_packet_and_envelope_directly_require_real_image_reading(self):
        record=self.image();push=envelope('a'*32,'owner',json.dumps({'kind':'text','raw_text':'看这张图'}),[record])
        payload=json.loads(push.splitlines()[1]);path=record['files'][0]['path']
        self.assertNotIn('visual_files',payload)
        self.assertIn('实际看图',Path(payload['instructions_file']).read_text(encoding='utf-8'))
        text=Path(payload['context_file']).read_text(encoding='utf-8')
        self.assertIn('必须阅读的图片',text);self.assertIn(path,text);self.assertIn('不代表已经看过',text)
        Path(path).write_bytes(b'changed');self.assertEqual(visual_delivery.inputs([record]),[])
    def test_unavailable_or_outside_resources_does_not_become_visual_input(self):
        path=self.root/'outside.png';Image.new('RGB',(3,3)).save(path)
        record={'kind':'image','status':'unavailable','files':[{'path':str(path),'status':'saved'}]}
        self.assertEqual(visual_delivery.inputs([record]),[])
    def test_heic_is_detected_by_bytes_and_canonicalized_to_png(self):
        from pillow_heif import register_heif_opener
        register_heif_opener(thumbnails=False)
        data=io.BytesIO();Image.new('RGB',(16,20),'red').save(data,format='HEIF')
        record=media_capture.save_image(data.getvalue(),self.root,'mislabelled-jpeg')
        self.assertEqual(record['format'],'HEIF');self.assertEqual(Path(record['path']).suffix,'.png')
        with Image.open(record['path']) as image:self.assertEqual(image.format,'PNG');self.assertEqual(image.size,(16,20))

class WhaleHeicTests(unittest.TestCase):
    def test_phone_upload_is_image_and_preview_is_browser_readable(self):
        fixtures.WhaleChannelTests.setUp(self)
        from pillow_heif import register_heif_opener
        register_heif_opener(thumbnails=False)
        output=io.BytesIO();Image.new('RGB',(16,16),'green').save(output,format='HEIF');raw=output.getvalue()
        with patch.object(whale_channel,'ROOT',self.root),patch.object(storage,'RUNTIME',self.root),patch.object(storage,'CONFIG',self.root/'storage.json'):
            folder=self.root/'whale-data/uploads'/self.device;folder.mkdir(parents=True)
            mid='c'*32;(folder/(mid+'.upload')).write_bytes(raw)
            result=whale_upload.receive(self.db,self.device,mid,'phone.heic',hashlib.sha256(raw).hexdigest())
            self.assertEqual(result['kind'],'image')
            seq=self.db.execute('SELECT seq FROM whale_messages').fetchone()[0]
            data,mime=whale_upload.attachment(self.db,self.device,seq)
            self.assertEqual(mime,'image/png');self.assertTrue(data.startswith(b'\x89PNG'))

if __name__=='__main__':unittest.main()
