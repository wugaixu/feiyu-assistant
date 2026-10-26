import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app.live_service import LiveStore
import app.image_sender as sender


class ImageSenderTests(unittest.TestCase):
    def test_outer_ok_does_not_hide_nonzero_business_result(self):
        body=b'\x08\x64\x22\x02OK\x32'+b'{"status_code":8101,"raw_check_code":1,"tips":""}'
        self.assertEqual(sender.business_result(body)['status_code'],8101)
        self.assertIsNone(sender.business_result(b'OK'))
        self.assertEqual(sender.business_result(b'{"status_code":0}')['status_code'],0)

    def test_transport_requires_both_upload_and_send_for_this_attempt(self):
        upload={'host':'tos-nc2-slb2.douyin.com','path':'/upload/v1/image','status':200}
        send={'host':'imapi.douyin.com','path':'/v1/message/send','status':200}
        self.assertFalse(sender.transport_confirmed([]))
        self.assertFalse(sender.transport_confirmed([upload]))
        self.assertFalse(sender.transport_confirmed([send]))
        self.assertFalse(sender.transport_confirmed([upload,dict(send,status=500)]))
        self.assertTrue(sender.transport_confirmed([upload,send]))

    def setUp(self):
        root=Path(r'__RUNTIME_ROOT__\tests')
        root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root)
        self.root=Path(self.tmp.name)
        self.patch=patch.object(sender,'ROOT',self.root)
        self.patch.start()
        images=self.root/'outgoing-images'
        images.mkdir()
        self.image=images/'test.png'
        Image.new('RGB',(20,20),'cyan').save(self.image)
        self.store=LiveStore(self.root/'test.sqlite3')
        self.task='1'*32
        with self.store.db:
            self.store.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                                  (self.task,'owner',1,'{}','processing'))

    def tearDown(self):
        self.store.db.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_enqueue_same_task_and_image_only_once(self):
        first=sender.enqueue(self.store.db,self.task,self.image)
        self.assertEqual(sender.enqueue(self.store.db,self.task,self.image),first)
        self.assertEqual(self.store.db.execute("select count(*) from reply_outbox WHERE kind='image'").fetchone()[0],1)
        self.assertEqual(self.store.db.execute("select owner from reply_outbox WHERE kind='image'").fetchone()[0],'owner')

    def test_unclaimed_task_cannot_send(self):
        with self.store.db:
            self.store.db.execute("UPDATE live_tasks SET status='pending'")
        with self.assertRaises(ValueError):
            sender.enqueue(self.store.db,self.task,self.image)

    def test_outside_paths_and_disguised_files_rejected(self):
        outside=self.root/'outside.png'
        Image.new('RGB',(5,5)).save(outside)
        with self.assertRaises(ValueError):
            sender.validate_image(outside)
        bad=self.root/'outgoing-images'/'bad.png'
        bad.write_text('not an image')
        with self.assertRaises(Exception):
            sender.validate_image(bad)


if __name__=='__main__':
    unittest.main()
