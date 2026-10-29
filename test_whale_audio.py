"""Isolated audio path, queue, permissions and SSE; no model or production sends."""
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from app import voice_captions, audio_sender as audio,astrbot_client as client,astrbot_worker as worker,whale_channel as whale,whale_voice,reply_batch,local_runtime,resource_storage
import test_whale_channel as fixtures
from app.sdk_receiver import OWNER


def wav():
    buf=io.BytesIO()
    with wave.open(buf,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(b'\x01\x01'*8000)
    return buf.getvalue()


class AudioTests(unittest.TestCase):
    setUp=fixtures.WhaleChannelTests.setUp
    receive=fixtures.WhaleChannelTests.receive

    def setup_audio(self):
        p=patch.object(audio,'ROOT',self.root);p.start();self.addCleanup(p.stop)
        return audio.prepare(wav(),'e'*32)

    def late_context(self):
        task=self.receive()
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(task,))
        token=whale_voice.permit(self.db,task,'music-session')
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT PRIMARY KEY,status TEXT,detail TEXT)')
        self.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',(task,'attempted',json.dumps({'provider':'astrbot','session_id':'music-session'})))
        self.db.commit()
        return task,token

    def qq_context(self):
        task=self.receive()
        raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(task,)).fetchone()[0])
        raw.update(transport='qq',reply_device='qq:987654321:123456789')
        with self.db:self.db.execute("UPDATE live_tasks SET raw_json=?,status='processing' WHERE id=?",(json.dumps(raw),task))
        token=whale_voice.permit(self.db,task,'qq-session')
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT PRIMARY KEY,status TEXT,detail TEXT)')
        self.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',(task,'attempted',json.dumps({'provider':'astrbot','session_id':'qq-session'})))
        self.db.commit()
        return task,token

    def test_qq_speech_caption_is_bound_and_queued_after_audio_once(self):
        self.setup_audio();task,token=self.qq_context();caption='这是合成的原文。'
        path=voice_captions.remember(self.db,token,wav(),caption)
        self.assertEqual(whale_voice.known_text(self.db,task,path),caption)
        from app import plugin_audio
        with self.assertRaises(ValueError):plugin_audio.authorized(self.db,token)  # Late music hook keeps its old scope.
        reply_batch.finish(self.db,task,[{'type':'audio','path':path,'text':caption}])
        self.assertEqual(self.db.execute('SELECT kind,text,seq FROM reply_outbox ORDER BY seq').fetchall(),
                         [('audio',caption,0),('text',caption,1)])
        with self.assertRaises(ValueError):reply_batch.finish(self.db,task,[{'type':'audio','path':path,'text':caption}])

    def test_qq_adjacent_original_text_is_not_sent_before_audio_or_twice(self):
        self.setup_audio();task,token=self.qq_context();caption='原文'
        path=voice_captions.remember(self.db,token,wav(),caption)
        reply_batch.finish(self.db,task,[{'type':'text','text':caption},{'type':'audio','path':path,'text':caption},{'type':'text','text':caption}])
        self.assertEqual(self.db.execute('SELECT kind,text FROM reply_outbox ORDER BY seq').fetchall(),[('audio',caption),('text',caption)])

    def test_qq_audio_without_known_original_does_not_invent_text(self):
        path=self.setup_audio();task,_=self.qq_context()
        reply_batch.finish(self.db,task,[{'type':'audio','path':path}])
        self.assertEqual(self.db.execute('SELECT kind FROM reply_outbox').fetchall(),[('audio',)])

    def test_qq_two_audio_parts_each_keep_their_following_original(self):
        self.setup_audio();task,token=self.qq_context();caption='相同原文'
        path=voice_captions.remember(self.db,token,wav(),caption)
        reply_batch.finish(self.db,task,[{'type':'audio','path':path,'text':caption},{'type':'audio','path':path,'text':caption}])
        self.assertEqual(self.db.execute('SELECT kind FROM reply_outbox ORDER BY seq').fetchall(),[('audio',),('text',),('audio',),('text',)])

    def test_qq_proactive_caption_requires_current_generating_job(self):
        from app import diary_proactive
        import time
        self.setup_audio();task,token=self.qq_context()
        raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(task,)).fetchone()[0])
        raw.update(direction='outgoing',origin='diary_proactive')
        diary_proactive.schema(self.db);dest={'session_id':'qq-session','config_id':'default'}
        with self.db:
            self.db.execute('UPDATE live_tasks SET raw_json=? WHERE id=?',(json.dumps(raw),task))
            self.db.execute('INSERT INTO diary_proactive_jobs(id,umo,created,expires,status,destination,route,decision) VALUES(?,?,?,?,?,?,?,?)',
                (task,'owner',time.time(),time.time()+100,'generating',json.dumps(dest),'{}','{}'))
        with patch.object(diary_proactive,'target',return_value=dest):
            path=voice_captions.remember(self.db,token,wav(),'主动语音原文')
            self.assertEqual(whale_voice.known_text(self.db,task,path),'主动语音原文')
        with patch.object(diary_proactive,'target',return_value=None):
            with self.assertRaises(ValueError):voice_captions.remember(self.db,token,wav(),'不能写入')

    def test_late_plugin_audio_commits_once_and_preserves_source(self):
        from app import plugin_audio
        self.setup_audio();task,token=self.late_context()
        reply_batch.finish(self.db,task,[{'type':'text','text':'正在处理'}])
        with self.db:
            self.db.execute("UPDATE chat_dispatch SET status='processed' WHERE task_id=?",(task,))
            channels=__import__('app.channels',fromlist=['observe'])
            channels.observe(self.db,'whale',self.other)
        self.assertTrue(plugin_audio.enqueue(self.db,token,wav()))
        self.assertTrue(plugin_audio.enqueue(self.db,token,wav()))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM reply_outbox WHERE kind='audio'").fetchone()[0],1)
        self.assertEqual(channels.address(self.db,task)['device'],self.device)
        with patch.object(local_runtime,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}):
            for _ in range(2):local_runtime.outbound_step(self.db,OWNER)
        self.assertEqual(len(whale.history(self.db,self.device)['history']),3)
        self.assertEqual(len(whale.history(self.db,self.other)['history']),0)

    def test_synthesized_text_is_bound_to_its_audio_and_whale_history(self):
        self.setup_audio();task,token=self.late_context()
        caption='你好呀，今天也想陪你聊聊天。'
        path=voice_captions.remember(self.db,token,wav(),caption)
        self.assertEqual(voice_captions.remember(self.db,token,wav(),caption),path)
        self.assertEqual(whale_voice.known_text(self.db,task,path),caption)
        self.assertEqual(whale_voice.known_text(self.db,'f'*32,path),'')
        with self.assertRaises(ValueError):voice_captions.remember(self.db,token,wav(),'错误文字')
        reply_batch.finish(self.db,task,[{'type':'audio','path':path,'text':caption}])
        with patch.object(local_runtime,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}):
            local_runtime.outbound_step(self.db,OWNER)
        items=whale.history(self.db,self.device)['history']
        self.assertEqual(items[-1]['kind'],'audio');self.assertEqual(items[-1]['text'],caption)
        with self.assertRaises(ValueError):voice_captions.remember(self.db,token,wav(),caption)

    def test_captions_require_valid_permission_and_do_not_guess_missing_text(self):
        self.setup_audio();task,token=self.late_context()
        path=audio.prepare(wav(),task)
        self.assertEqual(whale_voice.known_text(self.db,task,path),'')
        with self.db:self.db.execute('UPDATE whale_voice_permits SET expires=0 WHERE token=?',(token,))
        with self.assertRaises(ValueError):voice_captions.remember(self.db,token,wav(),'不能写入')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM whale_voice_captions').fetchone()[0],0)

    def test_caption_decoder_uses_same_bundled_ffmpeg_without_optional_package(self):
        self.setup_audio()
        bundled=self.root/'tools/gpt-sovits/ffmpeg-shared/ffmpeg.exe'
        bundled.parent.mkdir(parents=True);bundled.touch()
        with patch.dict('sys.modules',{'imageio_ffmpeg':None}):
            self.assertEqual(audio.ffmpeg_exe(),str(bundled))

    def test_late_plugin_audio_does_not_bypass_processing_or_expiry(self):
        from app import plugin_audio
        self.setup_audio();_,token=self.late_context()
        self.assertFalse(plugin_audio.enqueue(self.db,token,b'not decoded while live SSE owns it'))
        with self.db:self.db.execute('UPDATE whale_voice_permits SET expires=0 WHERE token=?',(token,))
        with self.assertRaises(ValueError):plugin_audio.enqueue(self.db,token,wav())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reply_outbox').fetchone()[0],0)

    def test_real_decode_and_malformed_inputs(self):
        path=self.setup_audio();_,digest,duration=audio.validate_audio(path)
        self.assertEqual(len(digest),64);self.assertAlmostEqual(duration,.5)
        with self.assertRaises(ValueError):audio.prepare(b'not audio','e'*32)
        with self.assertRaises(ValueError):audio.prepare(wav(),'../../escape')
        outside=self.root/'audio.wav';outside.write_bytes(wav())
        with self.assertRaises(ValueError):audio.validate_audio(outside)

    def test_compressed_audio_decoded_and_oversized_duration_rejected(self):
        self.setup_audio()
        import subprocess,imageio_ffmpeg
        source=self.root/'tone.mp3'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.2',str(source)],check=True,capture_output=True,creationflags=audio.subprocess.CREATE_NO_WINDOW)
        path=audio.prepare(source.read_bytes(),'f'*32)
        self.assertTrue(.1<audio.validate_audio(path)[2]<.5)
        with patch.object(audio,'MAX_SECONDS',.1):
            with self.assertRaises(ValueError):audio.prepare(wav(),'d'*32)

    def test_audio_http_requires_device_and_serves_real_bytes(self):
        import sqlite3,threading,urllib.request,urllib.error
        from http.server import ThreadingHTTPServer
        from app import console_server
        path=self.setup_audio();_,digest,_=audio.validate_audio(path)
        whale.deliver(self.db,self.device,'http-audio','audio','',path,digest)
        seq=whale.history(self.db,self.device)['history'][0]['seq']
        file=self.root/'http.sqlite3'
        from contextlib import closing
        with closing(sqlite3.connect(file)) as dest:self.db.backup(dest)
        with patch.object(console_server,'ASSISTANT_DB',file):
            server=ThreadingHTTPServer(('127.0.0.1',0),console_server.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                def request(device):
                    req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/whale-bridge',data=json.dumps({'action':'audio','seq':seq}).encode(),headers={'Host':'127.0.0.1:8793','Origin':'http://127.0.0.1:8793','Content-Type':'application/json','X-Whale-Bridge':'c'*64,'X-Whale-Device':device})
                    return urllib.request.urlopen(req,timeout=3)
                with request(self.device) as response:
                    self.assertEqual(response.headers['Content-Type'],'audio/wav');self.assertEqual(response.read(),Path(path).read_bytes())
                with self.assertRaises(urllib.error.HTTPError) as error:request(self.other)
                self.assertEqual(error.exception.code,400);error.exception.close()
            finally:server.shutdown();server.server_close();thread.join(timeout=3)

    def test_whale_audio_order_dedup_device_and_fingerprint(self):
        path=self.setup_audio();task=self.receive()
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(task,))
        reply_batch.finish(self.db,task,[{'type':'text','text':'听听这个'},{'type':'audio','path':path},{'type':'text','text':'说完啦'}])
        with patch('app.sdk_sender.health',side_effect=AssertionError('No SDK')),patch.object(local_runtime,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}),patch.object(local_runtime,'ROOT',self.root):
            for _ in range(3):self.assertEqual(local_runtime.outbound_step(self.db,OWNER)['state'],'server_accepted')
        items=whale.history(self.db,self.device)['history']
        self.assertEqual([x['kind'] for x in items],['text','text','audio','text'])
        item=items[2];self.assertIn('/assistant/audio?id=',item['audio'])
        self.assertNotIn('path',item)
        self.assertEqual(whale.audio(self.db,self.device,item['seq'])[1],'audio/wav')
        with self.assertRaises(ValueError):whale.audio(self.db,self.other,item['seq'])
        row=self.db.execute("SELECT id,sha256 FROM reply_outbox WHERE kind='audio'").fetchone()
        whale.deliver(self.db,self.device,row[0],'audio','',path,row[1])
        self.assertEqual(len(whale.history(self.db,self.device)['history']),4)
        Path(path).write_bytes(wav()+b'changed')
        with self.assertRaises(ValueError):whale.audio(self.db,self.device,item['seq'])

    def test_audio_cannot_queue_for_douyin(self):
        path=self.setup_audio();task=self.receive()
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing',raw_json=? WHERE id=?",(json.dumps({'direction':'incoming','transport':'sdk'}),task))
        with self.assertRaises(ValueError):reply_batch.finish(self.db,task,[{'type':'audio','path':path}])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reply_outbox').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'processing')

    def test_record_stream_and_incomplete_record(self):
        events=[{'type':'record','data':'[RECORD]private/path.wav'},{'type':'attachment_saved','data':{'type':'record','id':'a123'}},{'type':'end'}]
        lines=[('data:'+json.dumps(x)).encode() for x in events]
        self.assertEqual(client.parse_stream(lines),[{'type':'audio','attachment_id':'a123'}])
        with self.assertRaises(ConnectionError):client.parse_stream([lines[0],lines[-1]])

    def test_voice_attachment_fallback_and_actual_download(self):
        parts=[{'type':'audio','attachment_id':'att'}]
        with patch.object(client,'download_audio',return_value='canonical.wav') as download:
            self.assertEqual(worker.finalize_parts(parts,'e'*32,allow_audio=True),[{'type':'audio','path':'canonical.wav'}])
            download.assert_called_once()
        with patch.object(client,'download_audio',side_effect=AssertionError('Must not fetch')):
            self.assertEqual(worker.finalize_parts(parts,'e'*32)[0]['type'],'text')
        with patch.object(client,'download_audio',side_effect=ValueError('bad media')):
            self.assertIn('没能',worker.finalize_parts(parts,'e'*32,allow_audio=True)[0]['text'])

    def test_permit_claim_session_and_one_use(self):
        task=self.receive()
        with self.assertRaises(ValueError):whale_voice.permit(self.db,task,'sid')
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(task,))
        token=whale_voice.permit(self.db,task,'sid')
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT PRIMARY KEY,status TEXT,detail TEXT)');self.db.commit()
        with self.assertRaises(ValueError):whale_voice.synthesize(self.db,token,'hello')
        with self.db:self.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',(task,'attempted',json.dumps({'provider':'astrbot','session_id':'sid'})))
        with patch.object(whale_voice,'speak',return_value='test.wav') as speak:
            self.assertEqual(whale_voice.synthesize(self.db,token,'你好'),'test.wav')
            speak.assert_called_once_with('你好',task)
        with self.assertRaises(ValueError):whale_voice.synthesize(self.db,token,'again')

    def test_audio_history_protected_from_cleanup(self):
        path=self.setup_audio();_,digest,_=audio.validate_audio(path)
        whale.deliver(self.db,self.device,'voice','audio','',path,digest)
        with patch.object(resource_storage,'RUNTIME',self.root):
            self.assertNotIn(path,[x['path'] for x in resource_storage.candidates(self.db)])


if __name__=='__main__':unittest.main()
