import base64,json,sqlite3,time,unittest
from pathlib import Path
from app import local_vision
from app.sdk_receiver import OWNER
import test_visual_delivery as fixtures


class LocalVisionTests(unittest.TestCase):
    image=fixtures.VisualDeliveryTests.image
    def setUp(self):
        fixtures.VisualDeliveryTests.setUp(self)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE live_tasks(id TEXT,owner TEXT,status TEXT);
          CREATE TABLE chat_dispatch(task_id TEXT,status TEXT,detail TEXT);''')
        self.db.execute('INSERT INTO live_tasks VALUES(?,?,?)',('task',OWNER,'processing'))
        self.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',('task','attempted',json.dumps({'provider':'astrbot','session_id':'session'})))

    def test_read_image_is_bound_to_current_task_hash_and_dispatch(self):
        record=self.image();path=record['files'][0]['path']
        token=local_vision.permit(self.db,'task','session',[record])
        self.assertEqual(local_vision.extract_token('[本轮看图许可:'+token+']'),token)
        result=local_vision.read(self.db,token,path)
        self.assertEqual(result['mimeType'],'image/png')
        self.assertEqual(base64.b64decode(result['data']),Path(path).read_bytes())
        with self.assertRaises(ValueError):local_vision.read(self.db,token,__file__)
        Path(path).write_bytes(b'changed')
        with self.assertRaises(ValueError):local_vision.read(self.db,token,path)

    def test_done_expired_or_changed_destination_cannot_read(self):
        record=self.image();path=record['files'][0]['path']
        token=local_vision.permit(self.db,'task','session',[record])
        for sql,args in [
            ("UPDATE live_tasks SET status=?",('done',)),
            ("UPDATE chat_dispatch SET detail=?",(json.dumps({'provider':'dsh','session_id':'session'}),)),
            ("UPDATE local_vision_permits SET expires=?",(time.time()-1,))]:
            self.db.execute('SAVEPOINT test');self.db.execute(sql,args)
            with self.assertRaises(ValueError):local_vision.read(self.db,token,path)
            self.db.execute('ROLLBACK TO test');self.db.execute('RELEASE test')

    def test_no_images_no_permission_and_unclaimed_rejected(self):
        self.assertEqual(local_vision.permit(self.db,'task','session',[]),'')
        self.db.execute("UPDATE live_tasks SET status='done'")
        with self.assertRaises(ValueError):local_vision.permit(self.db,'task','session',[])


if __name__=='__main__':unittest.main()
