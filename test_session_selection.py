import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import console_server as cs

class SessionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=cs.ROOT);self.root=Path(self.tmp.name)
        self.db=self.root/'state.sqlite';self.meta=self.root/'state.json';self.target=self.root/'target.json'
        self.good='11111111-1111-1111-1111-111111111111';self.other='22222222-2222-2222-2222-222222222222'
        db=sqlite3.connect(self.db)
        db.execute('CREATE TABLE threads(id TEXT,title TEXT,name TEXT,cwd TEXT,project_id TEXT,archived INTEGER,is_pinned INTEGER,updated_at_ms INTEGER)')
        db.executemany('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',[(self.good,'first sentence','真实标题',str(cs.PROJECT.parent),None,0,0,100),(self.other,'test','无关测试',str(cs.PROJECT.parent),None,0,0,200)])
        db.commit();db.close()
        self.meta.write_text(json.dumps({'local-projects':{'work':{'rootPaths':[str(cs.PROJECT.parent)]}},'thread-project-assignments':{self.good:{'projectId':'work'},self.other:{'projectId':'other'}}}),encoding='utf-8')
        self.target.write_text(json.dumps({'provider':'dsh','dsh':{'session_id':'keep'},'mode':'explicit','thread_id':self.other,'candidates':[self.other,self.good],'fallback_thread_id':self.other}),encoding='utf-8')
        self.patches=[patch.object(cs,'STATE_DB',self.db),patch.object(cs,'CODEX_APP_STATE',self.meta),patch.object(cs,'TARGET_FILE',self.target)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_real_title_and_membership_before_limit(self):
        items=cs.read_threads(limit=1)
        self.assertEqual([x['id'] for x in items],[self.good]);self.assertEqual(items[0]['title'],'真实标题')
    def test_switch_provider_and_thread_in_single_config_write(self):
        result=cs.set_agent('codex',self.good)
        config=json.loads(self.target.read_text(encoding='utf-8'))
        self.assertEqual(result['provider'],'codex');self.assertEqual(config['thread_id'],self.good)
        self.assertEqual(config['dsh']['session_id'],'keep');self.assertEqual(result['effective_title'],'真实标题')
    def test_outside_or_archived_selection_rejected_without_changes(self):
        before=self.target.read_bytes()
        with self.assertRaises(ValueError):cs.set_agent('codex',self.other)
        with self.assertRaises(ValueError):cs.set_target('candidate_add',self.other)
        db=sqlite3.connect(self.db);db.execute('UPDATE threads SET archived=1 WHERE id=?',(self.good,));db.commit();db.close()
        with self.assertRaises(ValueError):cs.set_agent('codex',self.good)
        self.assertEqual(before,self.target.read_bytes())
    def test_auto_keeps_only_work1_candidates_and_fallback(self):
        cs.set_target('auto');config=json.loads(self.target.read_text(encoding='utf-8'))
        self.assertEqual(config['candidates'],[self.good]);self.assertEqual(config['fallback_thread_id'],self.good)
        self.assertEqual(config['provider'],'dsh')
    def test_blank_name_falls_back_to_stored_title(self):
        self.assertEqual(cs._title({'name':' ','title':'原标题','first_user_message':'no'}),'原标题')
        self.assertEqual(cs._title({'first_user_message':'no'}),'(无标题)')

if __name__=='__main__':unittest.main()
