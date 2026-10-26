import io
import json
import tempfile
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from app.media_capture import allowed_url, save_image
from app.live_service import LiveStore
from app import event_dispatch
from app.event_dispatch import dispatch_one, thread_id


class MediaTests(unittest.TestCase):
    def setUp(self):
        root = Path(r'__RUNTIME_ROOT__\tests')
        root.mkdir(parents=True,exist_ok=True)
        # Unit tests must never inherit the currently selected live AI backend.
        route=patch.object(event_dispatch,'target_config',return_value={'provider':'codex'})
        route.start();self.addCleanup(route.stop)
        target=patch.object(event_dispatch,'choose_thread',return_value={'thread_id':'11111111-1111-1111-1111-111111111111','reason':'unit_test'})
        target.start();self.addCleanup(target.stop)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.db = LiveStore(Path(self.tmp.name)/'test.sqlite3').db

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_resource_origin_restrictions(self):
        self.assertTrue(allowed_url('https://p5.douyinpic.com/a'))
        for url in ('http://p5.douyinpic.com/a','https://127.0.0.1/a','file:///c:/a',
                    'https://douyinpic.com.attacker.example/a','https://a:p@p5.douyinpic.com/a'):
            self.assertFalse(allowed_url(url))

    def test_real_image_validation(self):
        data=io.BytesIO()
        Image.new('RGB',(12,17),'blue').save(data,format='PNG')
        record=save_image(data.getvalue(),Path(self.tmp.name),'sample')
        self.assertEqual(record['size'],[12,17])
        self.assertTrue(Path(record['path']).exists())
        with self.assertRaises(Exception):
            save_image(b'<html>not an image</html>',Path(self.tmp.name),'bad')

    def test_next_text_gets_media_paths_once(self):
        with self.db:
            self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',
                            ('a'*32,'owner',1,'image',json.dumps({'kind':'image','files':[{'path':'D:/media/test.png'}]})))
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                            ('b'*32,'owner',2,'{"kind":"text","raw_text":"分析图片"}'))
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0,stdout='Queued message 11111111-1111-1111-1111-111111111111 for thread '+thread_id()+'.\n',stderr='')
        self.assertEqual(dispatch_one(self.db,runner,'C:/codex.exe')['status'],'queued')
        packet=json.loads(calls[0][-1].splitlines()[1])
        self.assertIn('D:/media/test.png',Path(packet['context_file']).read_text(encoding='utf-8'))
        self.assertNotIn('D:/media/test.png',calls[0][-1])
        self.assertEqual(self.db.execute('select attached_to from media_records').fetchone()[0],'b'*32)
        self.assertIsNone(dispatch_one(self.db,runner,'C:/codex.exe'))

    def queued_message(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0,stdout='Queued message 11111111-1111-1111-1111-111111111111 for thread '+thread_id()+'.\n',stderr='')
        status=dispatch_one(self.db,runner,'C:/codex.exe')['status']
        return status, calls

    def test_stale_media_is_not_glued_to_a_later_text(self):
        """A record from hours ago must not ride along with an unrelated text."""
        with self.db:
            self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',
                            ('a'*32,'owner',1,'image',json.dumps({'kind':'image','files':[{'path':'D:/media/old.png'}]})))
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                            ('b'*32,'owner',1+event_dispatch.ATTACH_WINDOW_SECONDS+60,'{"kind":"text","raw_text":"收得到消息吗"}'))
        status,calls=self.queued_message()
        self.assertEqual(status,'queued')
        self.assertNotIn('D:/media/old.png',calls[0][-1])
        self.assertIsNone(self.db.execute('select attached_to from media_records').fetchone()[0])

    def test_diagnostic_sample_media_never_rides_along(self):
        """sample_only imports are for diagnosis and must stay out of live events."""
        with self.db:
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                            ('a'*32,'owner',1,'{"kind":"video_card","sample_only":true}','resource_saved'))
            self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',
                            ('a'*32,'owner',1,'video_card',json.dumps({'kind':'video_card','files':[{'path':'D:/media/sample.webm'}]})))
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                            ('b'*32,'owner',2,'{"kind":"text","raw_text":"收得到消息吗"}'))
        status,calls=self.queued_message()
        self.assertEqual(status,'queued')
        self.assertNotIn('D:/media/sample.webm',calls[0][-1])
        self.assertIsNone(self.db.execute('select attached_to from media_records').fetchone()[0])

    def test_pending_media_delays_text_dispatch(self):
        with self.db:
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                            ('a'*32,'owner',1,'{"kind":"image"}','media_pending'))
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                            ('b'*32,'owner',2,'{"kind":"text"}'))
        self.assertIsNone(dispatch_one(self.db,executable='C:/codex.exe'))

    def test_share_card_arriving_just_after_its_text_still_merges(self):
        """Cards often reach the SDK milliseconds after the accompanying text."""
        text_received = 100.000
        media_received = 100.004
        with self.db:
            self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',
                            ('a'*32,'owner',media_received,'video_card',
                             json.dumps({'kind':'video_card','files':[{'path':'D:/media/card.webm'}],
                                         'shared_video_analysis':{'status':'saved'}})))
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                            ('b'*32,'owner',text_received,'{"kind":"text","raw_text":"看这个"}'))
        status,calls=self.queued_message()
        self.assertEqual(status,'queued')
        self.assertEqual(self.db.execute('select attached_to from media_records').fetchone()[0],'b'*32)
        self.assertIn('D:/media/card.webm',Path(json.loads(calls[0][-1].splitlines()[1])['context_file']).read_text(encoding='utf-8'))

    def test_share_card_outside_forward_grace_is_not_glued(self):
        text_received = 200.000
        media_received = text_received + event_dispatch.ATTACH_FORWARD_SECONDS + 5
        with self.db:
            self.db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',
                            ('a'*32,'owner',media_received,'image',
                             json.dumps({'kind':'image','files':[{'path':'D:/media/late.png'}]})))
            self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                            ('b'*32,'owner',text_received,'{"kind":"text","raw_text":"无关的一句话"}'))
        status,calls=self.queued_message()
        self.assertEqual(status,'queued')
        self.assertIsNone(self.db.execute('select attached_to from media_records').fetchone()[0])
        self.assertNotIn('D:/media/late.png',calls[0][-1])

    def test_media_kind_upgrade_preserves_old_checkpoint(self):
        from app.live_service import signature
        original={'kind':'other','raw_text':'1"','direction':'unverified'}
        upgraded=dict(original,kind='audio')
        self.assertEqual(signature(original),signature(upgraded))
        transcribed=dict(upgraded,raw_text='1"\n你好',audio_duration='1"')
        self.assertEqual(signature(original),signature(transcribed))

    def test_transcription_failure_placeholder_is_not_a_command(self):
        from app.media_capture import transcript_from_visible
        self.assertEqual(transcript_from_visible('[未识别到文字]')['status'],'no_speech_recognized')
        result=transcript_from_visible('你好，这是一条测试语音')
        self.assertEqual(result['status'],'transcribed')
        self.assertEqual(result['text'],'你好，这是一条测试语音')

    def test_new_media_is_saved_before_dispatch(self):
        store=LiveStore(':memory:')
        try:
            baseline=[{'kind':'text','raw_text':x,'direction':'unverified'} for x in ('a','b','c')]
            store.observe('owner',baseline)
            current=[{'kind':'image','raw_text':'','direction':'unverified','images':[]}] + baseline
            self.assertEqual(store.observe('owner',current)['accepted'],1)
            self.assertEqual(store.db.execute('select status from live_tasks').fetchone()[0],'media_pending')
            self.assertEqual(store.observe('owner',current)['accepted'],0)
        finally:
            store.db.close()

    def test_audio_only_mp4_is_not_accepted_as_video(self):
        import imageio_ffmpeg
        from app.media_capture import validate_video
        audio=Path(self.tmp.name)/'audio-only.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=0.3','-c:a','aac',str(audio)],check=True,timeout=15,capture_output=True)
        with self.assertRaises(ValueError):
            validate_video(audio)

    def test_transcribed_voice_becomes_tagged_event_but_old_sample_does_not(self):
        import app.media_pipeline as pipeline
        for sample_only in (False,True):
            task_id=('c' if sample_only else 'd')*32
            message={'kind':'audio','raw_text':'3"','sample_only':sample_only}
            record={'task_id':task_id,'kind':'audio','status':'saved','files':[],
                    'manifest_path':str(Path(self.tmp.name)/'manifest.json'),
                    'transcript':{'status':'transcribed','text':'你好'}}
            with self.db:
                self.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES(?,?,?,?,?)',
                                (task_id,'owner',1,json.dumps(message),'media_pending'))
            with patch.object(pipeline,'ROOT',Path(self.tmp.name)),patch.object(pipeline,'capture',return_value=record):
                pipeline.process_one(self.db,None,'owner')
            raw,status=self.db.execute('select raw_json,status from live_tasks where id=?',(task_id,)).fetchone()
            self.assertEqual(status,'resource_saved' if sample_only else 'pending')
            if not sample_only:
                self.assertEqual(json.loads(raw)['kind'],'voice_transcript')
                self.assertIn('你好',json.loads(raw)['raw_text'])

    def test_normal_playback_hooks_preserve_native_calls_and_observe_web_audio(self):
        from app.sensevoice_local import NODE
        from app.media_capture import PLAY_HOOK
        if not NODE.exists():
            self.skipTest('Optional installed DSH Node unavailable')
        script='''
        const assert=require('node:assert/strict');let played=0,started=0;
        global.window={};
        global.HTMLMediaElement=class {constructor(){this.tagName='AUDIO';this._src='';}get src(){return this._src;}set src(v){this._src=v;}play(){played++;return 17;}};
        window.Audio=class extends HTMLMediaElement {};
        global.AudioBufferSourceNode=class {start(){started++;return 23;}};
        window.AudioBufferSourceNode=AudioBufferSourceNode;
        const a=new window.Audio(),bound=a.play.bind(a);
        (HOOK)();
        a.src='https://test.douyinaudio.com/voice';assert.equal(bound(),17);
        const b=new AudioBufferSourceNode();b.buffer={duration:2};assert.equal(b.start(),23);
        assert.equal(played,1);assert.equal(started,1);
        assert.ok(window.__localAssistantMedia.events.some(e=>e.url===a.src));
        assert.equal(window.__localAssistantMedia.buffers[0].buffer,b.buffer);
        '''.replace('HOOK',PLAY_HOOK)
        subprocess.run([str(NODE),'-e',script],check=True,capture_output=True,timeout=10)


if __name__ == '__main__':
    unittest.main()
