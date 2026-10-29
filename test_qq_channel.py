import base64
import hashlib
import hmac
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import qq_channel as qq, channels, local_runtime, task_context
from app.live_service import LiveStore


class QQTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=r'__TEMP_PARENT__',ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(qq,'ROOT',self.root).start()
        self.config={'enabled':True,'owner_id':'123456789','account_id':'987654321','port':8796,'enabled_since':time.time()-10}
        qq.save(self.config)
        (self.root/'qq-onebot-secret.txt').write_text('a'*64)
        self.store=LiveStore(':memory:');self.db=self.store.db;self.addCleanup(self.db.close)
        self.now=time.time()

    def event(self,**patches):
        return {'self_id':987654321,'user_id':123456789,'post_type':'message','message_type':'private',
                'message_id':-123,'time':int(self.now),'message':[{'type':'text','data':{'text':'你好'}}],**patches}

    def test_hmac_raw_bytes_and_missing_or_wrong_secret(self):
        raw='{"你好": 1}'.encode();sig='sha1='+hmac.new(b'a'*64,raw,hashlib.sha1).hexdigest()
        self.assertTrue(qq.signature_ok(raw,sig));self.assertFalse(qq.signature_ok(raw+b' ',sig))
        self.assertFalse(qq.signature_ok(raw,None));self.assertFalse(qq.signature_ok(raw,'Bearer '+sig))

    def test_only_allowlisted_owner_private_new_events(self):
        for changes in ({'user_id':123456},{'self_id':777777},{'message_type':'group'},
                        {'post_type':'message_sent'},{'time':int(self.now)-600}):
            self.assertIn('ignored',qq.receive(self.db,self.event(**changes)))
        self.assertEqual(self.db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],0)

    def test_negative_message_id_is_stable_and_deduplicated(self):
        one=qq.receive(self.db,self.event());two=qq.receive(self.db,self.event())
        self.assertEqual(one['task_ids'],two['task_ids']);self.assertTrue(two['duplicate'])
        self.assertEqual(channels.address(self.db,one['task_ids'][0]),{'channel':'qq','device':qq.route()})
        self.assertEqual(channels.current(self.db)['channel'],'qq')

    def test_segment_order_and_media_deferred_to_shared_pipeline(self):
        parts=[{'type':'text','data':{'text':'第一句'}},{'type':'image','data':{'file':'opaque','url':'https://gchat.qpic.cn/a'}},
               {'type':'text','data':{'text':'最后一句'}},{'type':'record','data':{'file':'opaque2'}}]
        value=qq.receive(self.db,self.event(message=parts))
        rows=self.db.execute('SELECT status,raw_json FROM live_tasks ORDER BY rowid').fetchall()
        self.assertEqual(len(value['task_ids']),4)
        self.assertEqual([r[0] for r in rows],['pending','media_pending','pending','media_pending'])
        self.assertEqual([json.loads(r[1])['kind'] for r in rows],['text','image','text','audio'])
        self.assertTrue(all(json.loads(r[1])['reply_device']==qq.route() for r in rows))

    def test_reply_route_does_not_follow_later_channel_switch(self):
        task=qq.receive(self.db,self.event())['task_ids'][0]
        with self.db:channels.observe(self.db,'whale','b'*64)
        self.assertEqual(channels.address(self.db,task)['channel'],'qq')

    def test_standalone_voice_reaches_pending_after_transcription_and_quiet_window(self):
        from app import media_pipeline
        from app.sdk_receiver import OWNER
        task=qq.receive(self.db,self.event(message=[{'type':'record','data':{'file':'opaque'}}]))['task_ids'][0]
        record={'task_id':task,'kind':'audio','status':'saved','files':[],
                'manifest_path':str(self.root/'manifest.json'),
                'transcript':{'status':'transcribed','text':'语音验收'}}
        with patch.object(qq,'capture',return_value=record):local_runtime.media_step(self.db,OWNER)
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'resource_saved')
        message=json.loads(self.db.execute('SELECT raw_json FROM live_tasks').fetchone()[0])
        self.assertEqual(message['association_policy'],'media_10s_v1')
        with patch('app.media_pipeline.time.time',return_value=message['association_received_at']+9):
            media_pipeline.dispatch_standalone_shared(self.db,OWNER)
        self.assertEqual(self.db.execute('SELECT status FROM live_tasks').fetchone()[0],'resource_saved')
        with patch('app.media_pipeline.time.time',return_value=message['association_received_at']+11):
            media_pipeline.dispatch_standalone_shared(self.db,OWNER)
            media_pipeline.dispatch_standalone_shared(self.db,OWNER)
        raw,status=self.db.execute('SELECT raw_json,status FROM live_tasks').fetchone()
        self.assertEqual(status,'pending');self.assertIn('[QQ语音自动转写',json.loads(raw)['raw_text'])
        self.assertIn('语音验收',json.loads(raw)['raw_text'])
        self.assertEqual(channels.address(self.db,task)['channel'],'qq')
        self.assertEqual(self.db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],1)

    def test_new_qq_image_and_file_have_standalone_association_policy(self):
        for index,kind in enumerate(('image','file')):
            with self.subTest(kind=kind):
                task=qq.receive(self.db,self.event(message_id=100+index,
                    message=[{'type':kind,'data':{'file':'opaque'}}]))['task_ids'][0]
                raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(task,)).fetchone()[0])
                self.assertEqual(raw['association_policy'],'media_10s_v1')
                self.assertGreater(raw['association_received_at'],0)

    def test_account_change_or_pause_blocks_old_address(self):
        old=qq.route();qq.save({**self.config,'account_id':'99999999'})
        self.assertFalse(qq.permitted(old))
        qq.save({**self.config,'enabled':False});self.assertFalse(qq.permitted(old))

    def test_binding_main_account_is_rejected(self):
        with patch.object(qq,'health',return_value={'online':True,'account_id':'123456789'}):
            with self.assertRaises(ValueError):qq.configure(self.db,{'action':'bind'})

    def test_state_does_not_expose_tokens(self):
        with patch.object(qq,'health',return_value={'online':False}):value=qq.state()
        self.assertNotIn('a'*64,json.dumps(value))
        self.assertNotIn('token=',value['login_url'])

    def test_denies_untrusted_downloads(self):
        for url in ('http://127.0.0.1/a','file:///C:/secret','https://qq.com.evil.org/a',
                    'https://user@qpic.cn/a','https://qpic.cn:8793/a'):
            with self.assertRaises(ValueError):qq.fetch_media(url)

    def test_cq_code_text_stays_literal(self):
        with patch.object(qq,'health',return_value={'online':True,'bound':True}),patch.object(qq,'api',return_value={'message_id':8}) as api:
            result=qq.deliver(qq.route(),'text','[CQ:at,qq=all]')
        self.assertEqual(api.call_args.args[1]['message'],[{'type':'text','data':{'text':'[CQ:at,qq=all]'}}])
        self.assertEqual(result['result'],'server_accepted');self.assertFalse(result['recipient_delivery_confirmed'])

    def test_no_duplicate_send_after_network_failure(self):
        task=qq.receive(self.db,self.event())['task_ids'][0]
        self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(task,));self.db.commit()
        from app.reply_batch import finish
        finish(self.db,task,[{'type':'text','text':'回复'}])
        with patch.object(local_runtime,'ROOT',self.root),patch.object(local_runtime,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}),\
             patch('app.sdk_sender.health',side_effect=AssertionError('QQ must not depend on Douyin SDK')),\
             patch.object(qq,'health',return_value={'online':True,'bound':True}),patch.object(qq,'deliver',side_effect=TimeoutError) as deliver:
            result=local_runtime.outbound_step(self.db,__import__('app.sdk_receiver',fromlist=['OWNER']).OWNER)
            self.assertEqual(result['state'],'outcome_uncertain')
            local_runtime.outbound_step(self.db,__import__('app.sdk_receiver',fromlist=['OWNER']).OWNER)
            self.assertEqual(deliver.call_count,1)

    def test_qq_packet_label_and_transcript(self):
        with patch.object(task_context,'packet_path',return_value=self.root/'context.md'):
            packet=task_context.request('a'*32,'owner',{'transport':'qq','raw_text':'你好'},[],write=False)
        self.assertTrue(packet.startswith('[QQ私信]'))
        text='[QQ语音自动转写，可能有误]\n你好'
        self.assertEqual(task_context.summary({'kind':'audio','original_kind':'audio','raw_text':text}),text)

    def test_downloaded_audio_base64_uses_shared_capture(self):
        media={'kind':'audio','transport':'qq','qq_media':{'type':'record','data':{'file':'opaque'}}}
        captured=[]
        def shared(task,message):
            captured.append(message);return {'source':'whale_upload','files':[{'source':'whale_upload'}]}
        with patch.object(qq,'api',return_value={'base64':base64.b64encode(b'RIFFtest').decode(),'url':'C:/QQ/cache/audio.wav'}),\
             patch('app.resource_storage.resource_dir',return_value=self.root),patch('app.whale_upload.capture',side_effect=shared):
            record=qq.capture('a'*32,media)
        self.assertEqual(record['source'],'qq_upload')
        self.assertEqual(Path(captured[0]['upload']['path']).read_bytes(),b'RIFFtest')

    def test_arbitrary_local_attachment_path_is_rejected(self):
        secret=self.root/'secret.txt';secret.write_text('private')
        qq.save({**self.config,'napcat_dir':str(self.root/'napcat')})
        with patch.object(qq,'api',return_value={'file':str(secret),'url':str(secret)}):
            with self.assertRaises(ValueError):qq.capture('a'*32,{'kind':'file','qq_media':{'type':'file','data':{'file':'opaque'}}})

    def test_share_card_falls_back_to_canonical_link(self):
        with patch.object(qq,'health',return_value={'online':True,'bound':True}),patch.object(qq,'api',return_value={'message_id':10}) as api:
            qq.deliver(qq.route(),'share','',work=json.dumps({'itemId':'7695415840661035365'}))
        self.assertIn('https://www.douyin.com/video/7695415840661035365',api.call_args.args[1]['message'][0]['data']['text'])

    def test_qq_text_and_multiple_attachments_merge_as_one_turn(self):
        from app import event_dispatch as ed
        from app.sdk_receiver import OWNER
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT PRIMARY KEY,status TEXT)');self.db.commit()
        one=qq.receive(self.db,self.event(message=[{'type':'text','data':{'text':'看这些'}},
            {'type':'image','data':{'file':'opaque1'}},{'type':'image','data':{'file':'opaque2'}}]))
        tasks=one['task_ids']
        self.db.execute("UPDATE live_tasks SET status='resource_saved' WHERE status='media_pending'")
        for task in tasks[1:]:
            self.db.execute('INSERT INTO media_records(id,owner,received,kind,record_json) VALUES(?,?,?,?,?)',
                (task,OWNER,self.now,'image',json.dumps({'status':'saved','files':[]})))
        self.db.commit()
        row=self.db.execute('SELECT id,owner,raw_json,received FROM live_tasks WHERE id=?',(tasks[0],)).fetchone()
        latest=self.db.execute('SELECT max(received) FROM live_tasks').fetchone()[0]
        merged=ed._merge_burst(self.db,row,'qq',qq.route(),latest)
        self.assertIsNotNone(merged)
        self.assertEqual(len(json.loads(merged[2])['merged_messages']),3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM live_tasks WHERE status='merged'").fetchone()[0],2)

    def test_signed_callback_and_browser_requests_are_separate(self):
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        from app import console_server
        database=self.root/'events.sqlite3';store=LiveStore(database);store.db.close()
        server=ThreadingHTTPServer(('127.0.0.1',0),console_server.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with patch.object(console_server,'ASSISTANT_DB',database):
                raw=json.dumps(self.event()).encode();url=f'http://127.0.0.1:{server.server_port}/api/qq-events'
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url,data=raw),timeout=2)
                self.assertEqual(error.exception.code,403)
                error.exception.close()
                sig='sha1='+hmac.new(b'a'*64,raw,hashlib.sha1).hexdigest()
                req=urllib.request.Request(url,data=raw,headers={'X-Signature':sig})
                with urllib.request.urlopen(req,timeout=2) as response:result=json.load(response)
                self.assertEqual(len(result['task_ids']),1)
                import http.client
                # Match NapCat's native Node http.request stream: no length header.
                client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
                try:
                    client.request('POST','/api/qq-events',body=iter([raw[:20],raw[20:]]),
                        headers={'X-Signature':sig,'Content-Type':'application/json'},encode_chunked=True)
                    response=client.getresponse()
                    self.assertEqual(response.status,200)
                    self.assertTrue(json.loads(response.read())['duplicate'])
                finally:client.close()
        finally:
            server.shutdown();server.server_close();worker.join(timeout=2)

    def test_chunked_body_rejects_oversized_or_ambiguous_input(self):
        from types import SimpleNamespace
        for headers,body in (({'Transfer-Encoding':'chunked','Content-Length':'2'},b'0\r\n\r\n'),
            ({'Transfer-Encoding':'chunked'},b'100001\r\n'),
            ({'Transfer-Encoding':'chunked'},b'bad-size\r\n'),
            ({'Transfer-Encoding':'chunked'},b'2\r\na'),
            ({'Content-Length':'2'},b'a')):
            with self.assertRaises(ValueError):qq.read_event(SimpleNamespace(headers=headers,rfile=io.BytesIO(body)))
