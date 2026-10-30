import json,os,tempfile,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from app import resource_storage as s
from app.live_service import LiveStore

class StorageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=s.TEMP_PARENT);self.addCleanup(temp.cleanup)
        self.parent=Path(temp.name);self.root=self.parent/'runtime';self.root.mkdir()
        for key,value in {'RUNTIME':self.root,'TEMP_PARENT':self.parent,'CONFIG':self.root/'resource-storage.json','STATE':self.root/'state.json','PREVIEW':self.root/'preview.json'}.items():
            p=patch.object(s,key,value);p.start();self.addCleanup(p.stop)
        self.db=LiveStore(self.root/'assistant.sqlite3').db;self.addCleanup(self.db.close)
        self.task=uuid.uuid4().hex;self.old=time.time()-40*86400

    def fixture(self,status='done',task=None,analysis=None):
        task=task or self.task
        media=self.root/'media'/task;media.mkdir(parents=True)
        frames=analysis or self.root/'shared-video-analysis'/('a'*16)/'timeline-v2';frames.mkdir(parents=True,exist_ok=True)
        content=self.root/'tool-content'/'7695415840661035365';content.mkdir(parents=True,exist_ok=True)
        context=self.root/'task-context'/task;context.mkdir(parents=True)
        record={'task_id':task,'kind':'video_card','status':'saved','files':[{'path':str(media/'video.mp4'),'status':'saved'}],
            'manifest_path':str(media/'manifest.json'),'video':{'video_id':'7695415840661035365'},
            'shared_video_analysis':{'status':'done','contact_sheet':str(frames/'sheet.jpg')}}
        for p in (media/'video.mp4',frames/'sheet.jpg',content/'comments.json',context/'context.md'):p.write_bytes(b'test')
        (media/'manifest.json').write_text(json.dumps(record),encoding='utf-8')
        for base in (media,frames.parent,content,context):
            for p in list(base.rglob('*'))+[base]:os.utime(p,(self.old,self.old))
        with self.db:
            self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',(task,'owner',self.old,'{}',status))
            self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',(task,'owner',self.old,'video_card',json.dumps(record)))
        return media,frames.parent,content,context

    def test_default_does_not_enable_or_delete(self):
        paths=self.fixture();value=s.plan(self.db)
        self.assertFalse(s.config()['cleanup_enabled']);self.assertEqual(value['directories'],4)
        self.assertTrue(all(p.exists() for p in paths))

    def test_save_changes_new_roots_and_keeps_old(self):
        old=self.fixture();base=self.parent/'new-resources'
        value=s.save({'base':str(base)})
        self.assertEqual(Path(value['paths']['media']),base/'media')
        self.assertIn(self.root/'media',s.resource_dirs('media'));self.assertTrue(old[0].exists())
        self.assertTrue(s.media_output_allowed(base/'media'/'new'))
        self.assertFalse(s.media_output_allowed(self.parent/'other'))

    def test_reject_unsafe_base_and_cleanup_without_confirmation(self):
        for base in (self.parent,self.root/'tools',self.root.parent.parent):
            with self.assertRaises(ValueError):s.save({'base':str(base)})
        with self.assertRaises(ValueError):s.save({'cleanup_enabled':True})
        with self.assertRaises(ValueError):s.save({'retention_days':0})
        with self.assertRaises(ValueError):s.save({'interval_hours':True})

    def test_durable_runtime_outside_temporary_workspace_remains_valid(self):
        with patch.object(s,'TEMP_PARENT',self.parent/'temporary'):
            value=s.save({'base':str(self.root)})
            self.assertEqual(Path(value['config']['base']),self.root)
            self.assertEqual(s.validate_base(str(self.root)),self.root)

    def test_enable_is_scheduled_not_immediate(self):
        value=s.save({'cleanup_enabled':True,'interval_hours':3},confirm_cleanup=True)
        self.assertGreater(value['config']['next_run_at'],time.time()+2*3600)

    def test_deletes_only_previewed_completed_resources_and_marks_db(self):
        paths=self.fixture();value=s.plan(self.db);result=s.execute(value,self.db)
        self.assertEqual(len(result['deleted']),4);self.assertTrue(all(not p.exists() for p in paths))
        raw=self.db.execute('SELECT record_json FROM media_records').fetchone()[0]
        self.assertEqual(json.loads(raw)['status'],'cleaned')
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'done')

    def test_active_and_shared_analysis_protected(self):
        paths=self.fixture();other=uuid.uuid4().hex;self.fixture('processing',other,paths[1]/'timeline-v2')
        value=s.plan(self.db);self.assertNotIn(str(paths[1]),[p['path'] for p in value['items']])
        self.assertEqual(s.execute(value,self.db)['state'],'deferred_busy');self.assertTrue(paths[0].exists())

    def test_pending_outbound_protects_completed_task(self):
        self.fixture()
        with self.db:self.db.execute('INSERT INTO reply_outbox(id,task_id,owner,text) VALUES(?,?,?,?)',('reply',self.task,'owner','text'))
        self.assertEqual(s.plan(self.db)['directories'],0)

    def test_unknown_directory_and_credentials_never_selected(self):
        for name in ('media/'+('b'*32),'sdk-data','edge-profile','tools','backups'):
            p=self.root/name;p.mkdir(parents=True);(p/'file').write_bytes(b'test');os.utime(p,(self.old,self.old))
        self.assertEqual(s.plan(self.db)['directories'],0)

    def test_modified_tree_skipped(self):
        paths=self.fixture();value=s.plan(self.db)
        (paths[0]/'new-file').write_bytes(b'changed')
        result=s.execute(value,self.db)
        self.assertTrue(paths[0].exists());self.assertEqual(len(result['skipped']),1)

    def test_locked_resource_skipped(self):
        paths=self.fixture();value=s.plan(self.db)
        with patch.object(s.shutil,'rmtree',side_effect=PermissionError()):result=s.execute(value,self.db)
        self.assertEqual(len(result['skipped']),4);self.assertTrue(paths[0].exists())

    def test_preview_token_expiry_and_config_change(self):
        s.atomic(s.PREVIEW,{'token':'abc','created':time.time()-601,'items':[]})
        with self.assertRaises(ValueError):s.run_preview('abc')
        s.atomic(s.PREVIEW,{'token':'abc','created':time.time(),'config':{},'items':[]})
        with self.assertRaises(ValueError):s.run_preview('abc')

    def test_reparse_tree_is_not_followed(self):
        media,*_=self.fixture();outside=self.parent/'outside';outside.mkdir();(outside/'keep').write_bytes(b'keep')
        try:os.symlink(outside,media/'link',target_is_directory=True)
        except OSError:self.skipTest('Symlink privilege unavailable')
        with self.assertRaises(ValueError):s.tree_info(media)
        self.assertTrue((outside/'keep').exists())

    def test_only_selected_category_is_planned(self):
        self.fixture();s.save({'categories':['context']})
        self.assertEqual([i['kind'] for i in s.plan(self.db)['items']],['context'])

    def test_old_packet_lookup_after_changing_storage(self):
        from app import task_context
        self.fixture();s.save({'base':str(self.parent/'new')})
        with patch.object(task_context,'PACKETS',self.root/'task-context'):
            self.assertEqual(task_context.packet_path(self.task),self.root/'task-context'/self.task/'context.md')
            self.assertEqual(task_context.packet_path('c'*32),self.parent/'new/task-context'/('c'*32)/'context.md')

    def test_cleaned_analysis_is_not_automatically_rebuilt(self):
        from app.media_pipeline import update_shared_analysis
        self.fixture();s.save({'categories':['analysis']});s.execute(s.plan(self.db),self.db)
        with patch('app.shared_video.start_frames') as start:update_shared_analysis(self.db,'owner')
        start.assert_not_called()

    def test_outgoing_images_cleanup_keeps_pending_and_ignores_manual_files(self):
        out=s.outgoing_dir();out.mkdir(parents=True,exist_ok=True)
        old=out/'astrbot-old.png';old.write_bytes(b'old')
        pending=out/'astrbot-pending.png';pending.write_bytes(b'pending')
        staged=out/'resource-staged.png';staged.write_bytes(b'staged')
        manual=out/'manual-note.png';manual.write_bytes(b'manual')
        for path in (old,pending,staged,manual):os.utime(path,(self.old,self.old))
        from app.image_sender import schema
        schema(self.db)
        with self.db:
            self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('t'*32,'owner',self.old,'{}','done'))
            self.db.execute("INSERT INTO reply_outbox(id,task_id,owner,text,kind,path,sha256,status) VALUES(?,?,?,?,?,?,?,?)",('img','t'*32,'owner','','image',str(pending),'sha','pending'))
        s.save({'categories':['outgoing']})
        value=s.plan(self.db)
        self.assertEqual([Path(i['path']).name for i in value['items']],['astrbot-old.png','resource-staged.png'])
        self.assertTrue(pending.exists() and manual.exists())

    def test_outgoing_images_are_deleted_after_retention(self):
        out=s.outgoing_dir();out.mkdir(parents=True,exist_ok=True)
        old=out/'astrbot-old.png';old.write_bytes(b'old');os.utime(old,(self.old,self.old))
        s.save({'categories':['outgoing']})
        result=s.execute(s.plan(self.db),self.db)
        self.assertEqual(result['deleted'],[str(old)])
        self.assertFalse(old.exists())

    def test_due_schedule_runs_once_and_advances(self):
        s.save({'cleanup_enabled':True},confirm_cleanup=True)
        cfg=s.config();cfg['next_run_at']=time.time()-1;s.atomic(s.CONFIG,cfg)
        class Once:
            stopped=False
            def is_set(self):return self.stopped
            def wait(self,_):self.stopped=True
        s.cleanup_loop(Once())
        self.assertEqual(s.read(s.STATE,{})['state'],'completed')
        self.assertGreater(s.config()['next_run_at'],time.time()+23*3600)

    def test_junction_flag_is_refused(self):
        media,*_=self.fixture()
        with patch.object(Path,'is_junction',return_value=True):
            with self.assertRaises(ValueError):s.tree_info(media)

if __name__=='__main__':unittest.main()
