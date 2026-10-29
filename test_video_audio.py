import json,tempfile,unittest,wave,sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import Future
from app import video_audio as audio,event_dispatch as ed
from app.live_service import LiveStore
from app.media_pipeline import update_shared_analysis,dispatch_standalone_shared


class VideoAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=audio.ROOT);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'clip.mp4';self.source.write_bytes(b'fixture')
        self.record={'task_id':'audio','status':'saved','manifest_path':str(self.root/'manifest.json'),'files':[{'path':str(self.source),'status':'saved','decode_verified':True}]}

    def runner(self,argv,**kw):
        if str(argv[-1]).endswith('.wav'):
            with wave.open(argv[-1],'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\x01\x00'*16000)
            return SimpleNamespace(returncode=0,stderr=b'')
        request=json.loads(kw['input'])
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'clips':[{'file':p,'segments':[{'start':0,'end':1,'text':'你好','language':'zh','emotion':'NEUTRAL','event':'Speech'}]} for p in request['files']]}))

    def test_audio_text_labels_and_cache_reused_without_inference(self):
        with patch.object(audio.subprocess,'run',side_effect=self.runner) as runner,patch('app.resource_storage.resource_dir',return_value=self.root/'cache'):
            first=audio.analyze(self.record);second=audio.analyze(self.record)
        self.assertEqual(first['status'],'done');self.assertIn('你好',first['text'])
        self.assertTrue(second['clips'][0]['cache_hit']);self.assertEqual(runner.call_count,3)
        self.assertTrue(Path(first['text_path']).is_file());self.assertEqual(first['clips'][0]['segments'][0]['event'],'Speech')

    def test_no_audio_distinct_from_decode_failure(self):
        for error,status in [(b'Stream map matches no streams','no_audio'),(b'Invalid data','failed')]:
            with patch.object(audio.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr=error)):
                self.assertEqual(audio.analyze(self.record)['status'],status)

    def test_foreign_source_rejected_before_decode(self):
        directory=self.root/'sub';directory.mkdir()
        self.record['manifest_path']=str(directory/'manifest.json')
        with patch.object(audio.subprocess,'run') as runner:
            with self.assertRaises(ValueError):audio.analyze(self.record)
        runner.assert_not_called()

    def test_failed_job_is_terminal_and_still_gallery_is_not_applicable(self):
        future=Future();future.set_exception(RuntimeError('inference failed'))
        with patch.object(audio,'_jobs',{'audio':future}):
            self.assertEqual(audio.start(self.record)['status'],'failed')
        self.assertEqual(audio.start({'media_type':'image_gallery'})['status'],'not_applicable')

    def test_dispatch_waits_for_audio_even_when_frames_done(self):
        store=LiveStore(':memory:');self.addCleanup(store.db.close);db=store.db
        r={**self.record,'shared_video_analysis':{'status':'done'},'audio_analysis':{'status':'processing'}}
        db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('text','owner',2,'{"kind":"text"}','pending'))
        db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('audio','owner',1,'{"kind":"shared_media","original_kind":"video"}','resource_saved'))
        db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('audio','owner',1,'video',json.dumps(r)));db.commit()
        with patch.object(ed,'codex_executable',side_effect=AssertionError('must wait')):
            self.assertIsNone(ed.dispatch_one(db))

    def test_audio_completion_persists_and_legacy_records_not_reprocessed(self):
        store=LiveStore(':memory:');self.addCleanup(store.db.close);db=store.db
        r={**self.record,'shared_video_analysis':{'status':'done'},'audio_analysis':{'status':'processing'}}
        db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('audio','owner',1,'video',json.dumps(r)))
        db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('old','owner',1,'video',json.dumps({**self.record,'shared_video_analysis':{'status':'done'}})));db.commit()
        with patch.object(audio,'start',return_value={'status':'done','text':'你好'}) as start:
            update_shared_analysis(db,'owner')
        start.assert_called_once();self.assertEqual(json.loads(db.execute("SELECT record_json FROM media_records WHERE id='audio'").fetchone()[0])['audio_analysis']['text'],'你好')

    def test_standalone_video_waits_for_audio(self):
        store=LiveStore(':memory:');self.addCleanup(store.db.close);db=store.db
        db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('audio','owner',1,json.dumps({'kind':'video','association_policy':'media_10s_v1'}),'resource_saved'))
        db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',('audio','owner',1,'video',json.dumps({'kind':'video','shared_video_analysis':{'status':'done'},'audio_analysis':{'status':'processing'}})));db.commit()
        dispatch_standalone_shared(db,'owner')
        self.assertEqual(db.execute('SELECT status FROM live_tasks').fetchone()[0],'resource_saved')


if __name__=='__main__':unittest.main()
