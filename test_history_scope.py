"""Current source guard remains after removal of history injection."""
import json,unittest
from app.live_service import LiveStore
class SourceScopeTests(unittest.TestCase):
    def test_system_guidance_requires_current_permit_and_real_task_scope(self):
        import time
        from app import local_vision as lv
        from app.sdk_receiver import OWNER
        db=LiveStore(':memory:').db
        self.addCleanup(db.close)
        lv.schema(db)
        db.execute('CREATE TABLE chat_dispatch(task_id TEXT,status TEXT,detail TEXT)')
        db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',('task',OWNER,time.time(),json.dumps({'is_group':True}),'processing'))
        db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',('task','queued',json.dumps({'provider':'astrbot','session_id':'session'})))
        db.execute('INSERT INTO local_vision_permits VALUES(?,?,?,?,?,?)',('token','task','session',time.time()+100,'[]','[]'));db.commit()
        self.assertIn('当前是群聊',lv.scope_guidance(db,'token'))
        db.execute("UPDATE live_tasks SET raw_json='{}'");db.commit()
        self.assertIn('当前是已核验主人私聊',lv.scope_guidance(db,'token'))
        db.execute("UPDATE live_tasks SET status='done'");db.commit()
        with self.assertRaises(ValueError):lv.scope_guidance(db,'token')
        with self.assertRaises(ValueError):lv.scope_guidance(db,'fake')


if __name__=='__main__':unittest.main()
