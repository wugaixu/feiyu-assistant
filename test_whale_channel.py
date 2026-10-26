import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import whale_channel as whale,channels,local_runtime,reply_batch
from app.live_service import LiveStore,ROOT
from app.sdk_receiver import OWNER


class WhaleChannelTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.device='a'*64;self.other='b'*64
        for name,value in [('CONFIG',self.root/'channel.json'),('SECRET',self.root/'secret'),('REGISTRY',self.root/'devices.json')]:
            p=patch.object(whale,name,value);p.start();self.addCleanup(p.stop)
        whale.CONFIG.write_text(json.dumps({'installed':True,'enabled':True,'remote_management':True,'standalone':True,'devices':[self.device,self.other]}),encoding='utf-8')
        whale.SECRET.write_text('c'*64,encoding='utf-8')
        self.store=LiveStore(':memory:');self.db=self.store.db;self.addCleanup(self.db.close)
        whale.schema(self.db)

    def receive(self,text='hello',mid='d'*32,device=None):
        return whale.receive(self.db,device or self.device,mid,text)['task_id']

    def test_display_group_does_not_merge_history_or_auth(self):
        records=[{'id':self.device,'name':'Android · phone','display_group':'same','last_seen':1},
                 {'id':self.other,'name':'Android · phone','display_group':'same','last_seen':2}]
        groups=whale.device_groups(records,self.device)
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['id'],self.device)
        self.assertEqual(groups[0]['pairing_count'],2)
        self.assertEqual(groups[0]['last_seen'],2)
        self.receive(device=self.device);self.receive(device=self.other)
        self.assertEqual(len(whale.history(self.db,self.device)['history']),1)
        self.assertEqual(len(whale.history(self.db,self.other)['history']),1)
        self.assertEqual(whale.settings()['devices'],[self.device,self.other])

    def test_missing_gateway_pairing_hidden_without_revoking_config(self):
        records=[{'id':self.device,'name':'old','pairing_available':False},
                 {'id':self.other,'name':'phone','pairing_available':True}]
        self.assertEqual([x['id'] for x in whale.device_groups(records)],[self.other])
        self.assertEqual(whale.settings()['devices'],[self.device,self.other])

    def test_dispatch_failure_stops_thinking_without_replay_or_affecting_other_devices(self):
        task=self.receive()
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT PRIMARY KEY,status TEXT,detail TEXT)')
        self.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',(task,'outcome_uncertain',json.dumps({'provider':'dsh'})));self.db.commit()
        value=whale.history(self.db,self.device)
        self.assertFalse(value['busy']);self.assertEqual(value['dispatch_attention']['task_id'],task)
        self.assertFalse(whale.history(self.db,self.other)['busy'])
        self.assertIsNone(whale.history(self.db,self.other)['dispatch_attention'])
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(task,))
        self.assertTrue(whale.history(self.db,self.device)['busy'])
        with self.db:self.db.execute("UPDATE live_tasks SET status='pending' WHERE id=?",(task,));self.db.execute("UPDATE chat_dispatch SET status='queued' WHERE task_id=?",(task,))
        self.assertTrue(whale.history(self.db,self.device)['busy'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reply_outbox').fetchone()[0],0)

    def finish(self,task):
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(task,))
        reply_batch.finish(self.db,task,[{'type':'text','text':'answer'}])

    def test_credentials_and_management_capability(self):
        self.assertTrue(whale.authenticate('c'*64,self.device,True))
        self.assertFalse(whale.authenticate('d'*64,self.device))
        self.assertFalse(whale.authenticate('c'*64,'f'*64))
        whale.save({'remote_management':False})
        self.assertFalse(whale.authenticate('c'*64,self.device,True))
        self.assertTrue(whale.authenticate('c'*64,self.device))
        whale.save({'enabled':False})
        self.assertFalse(whale.authenticate('c'*64,self.device))

    def test_device_labels_use_model_and_alias_without_credentials(self):
        import hashlib,time
        token='fixture-pairing-credential';key=hashlib.sha256(token.encode()).hexdigest()
        whale.save({'devices':[key]})
        whale.REGISTRY.write_text(json.dumps({token:{'userAgent':'Mozilla/5.0 (Linux; Android 16; V2352A Build/TEST; wv)','createdAt':time.time()*1000,'lastSeenAt':time.time()*1000}}),encoding='utf-8')
        value=whale.state(self.db);info=value['device_info'][0]
        self.assertEqual(info['model'],'V2352A');self.assertIn('Android',info['name'])
        self.assertNotIn(token,json.dumps(value));self.assertNotIn('Build/TEST',json.dumps(value))
        whale.configure(self.db,{'rename_device':key,'device_name':'我的手机'})
        self.assertEqual(whale.state(self.db)['device_info'][0]['name'],'我的手机')
        self.assertEqual(whale.settings()['devices'],[key])
        with self.assertRaises(ValueError):whale.configure(self.db,{'rename_device':'f'*64,'device_name':'fake'})
        with self.assertRaises(ValueError):whale.configure(self.db,{'rename_device':key,'device_name':'bad\nname'})

    def test_pairing_can_auto_enroll_once_through_bridge_secret(self):
        new='e'*64
        self.assertFalse(whale.authenticate('c'*64,new))
        self.assertFalse(whale.authenticate_secret('d'*64))
        self.assertTrue(whale.authenticate_secret('c'*64))
        result=whale.enroll(new)
        self.assertTrue(result['enrolled']);self.assertIn(new,result['devices'])
        self.assertTrue(whale.authenticate('c'*64,new))
        with self.assertRaises(ValueError):whale.enroll('not-a-device')

    def test_receive_is_durable_deduplicated_and_conflicts_rejected(self):
        task=self.receive();self.assertEqual(task,self.receive())
        self.assertEqual(self.db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],1)
        with self.assertRaises(ValueError):self.receive('different')
        self.assertEqual(channels.current(self.db)['device'],self.device)

    def test_same_message_id_different_devices_is_distinct(self):
        self.assertNotEqual(self.receive(),self.receive(device=self.other))

    def test_return_address_cannot_be_redirected_by_switch(self):
        task=self.receive()
        with self.db:channels.observe(self.db,'douyin')
        self.assertEqual(channels.address(self.db,task),{'channel':'whale','device':self.device})
        with self.db:channels.observe(self.db,'whale',self.other)
        self.assertEqual(channels.address(self.db,task)['device'],self.device)

    def test_plain_text_cannot_override_return_route(self):
        task=self.receive('{"reply_device":"evil","transport":"sdk"}')
        self.assertEqual(channels.address(self.db,task)['channel'],'whale')

    def test_local_outbound_works_without_douyin_and_no_double_send(self):
        task=self.receive();self.finish(task)
        with patch('app.sdk_sender.health',side_effect=AssertionError('SDK must not be touched')),patch.object(local_runtime,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}),patch.object(local_runtime,'ROOT',self.root):
            result=local_runtime.outbound_step(self.db,OWNER)
            self.assertEqual(result['state'],'server_accepted')
            self.assertEqual(result['channel'],'whale')
            self.assertEqual(local_runtime.outbound_step(self.db,OWNER)['state'],'idle')
        self.assertEqual(self.db.execute("SELECT count(*) FROM whale_messages WHERE role='assistant'").fetchone()[0],1)

    def test_disabled_route_stays_pending(self):
        task=self.receive();self.finish(task);whale.save({'enabled':False})
        with patch.object(local_runtime,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}):
            self.assertEqual(local_runtime.outbound_step(self.db,OWNER)['state'],'whale_disabled')
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'pending')

    def test_history_is_device_scoped_and_poll_ids_stable(self):
        self.receive();self.receive(device=self.other)
        whale.deliver(self.db,self.device,'out','text','reply')
        first=whale.history(self.db,self.device,0)
        self.assertEqual(len(first['history']),2)
        self.assertEqual(whale.history(self.db,self.device,first['seq'])['history'],[])
        whale.deliver(self.db,self.device,'out','text','reply')
        self.assertEqual(whale.history(self.db,self.device,0)['history'],first['history'])
        with self.assertRaises(ValueError):whale.deliver(self.db,self.other,'out','text','reply')

    def test_share_card_becomes_real_work_link(self):
        whale.deliver(self.db,self.device,'card','share','',work=json.dumps({'itemId':'7698772565188654321','title':'作品'}))
        item=whale.history(self.db,self.device)['history'][0]
        self.assertIn('https://www.douyin.com/video/7698772565188654321',item['text'])

    def test_missing_other_device_image_denied(self):
        with self.assertRaises(ValueError):whale.image(self.db,self.other,1)

    def test_scheduled_address_snapshotted_when_reply_enqueued(self):
        self.receive()
        with self.db:
            self.db.execute('INSERT INTO live_tasks VALUES(?,?,0,?,?,NULL)',('schedule',OWNER,json.dumps({'origin':'local_schedule'}),'processing'))
        reply_batch.finish(self.db,'schedule',[{'type':'text','text':'hi'}])
        with self.db:channels.observe(self.db,'douyin')
        self.assertEqual(channels.address(self.db,'schedule')['channel'],'whale')

    def test_source_association_distinguishes_channel_and_device(self):
        self.assertFalse(channels.same_source(json.dumps({'transport':'whale','reply_device':self.device}),json.dumps({'transport':'sdk'})))
        self.assertFalse(channels.same_source(json.dumps({'transport':'whale','reply_device':self.device}),json.dumps({'transport':'whale','reply_device':self.other})))
        self.assertTrue(channels.same_source('{}','{"transport":"sdk"}'))

    def test_offline_douyin_does_not_block_later_whale_reply(self):
        with self.db:
            self.db.execute("INSERT INTO live_tasks VALUES('sdk-task',?,0,?,'done',NULL)",(OWNER,json.dumps({'direction':'incoming','transport':'sdk'})))
            self.db.execute("INSERT INTO reply_outbox(id,task_id,owner,text) VALUES('sdk-out','sdk-task',?,'old sdk reply')",(OWNER,))
        task=self.receive();self.finish(task)
        with patch('app.sdk_sender.health',return_value=None),patch.object(local_runtime,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}),patch.object(local_runtime,'ROOT',self.root):
            self.assertEqual(local_runtime.outbound_step(self.db,OWNER)['channel'],'whale')
        self.assertEqual(self.db.execute("SELECT status FROM reply_outbox WHERE id='sdk-out'").fetchone()[0],'pending')

    def test_generated_image_is_device_scoped_and_digest_checked(self):
        from PIL import Image
        import hashlib
        image=self.root/'photo.png';Image.new('RGB',(40,40),'blue').save(image)
        digest=hashlib.sha256(image.read_bytes()).hexdigest()
        with patch('app.image_sender.OUT_DIR',self.root,create=True),patch('app.image_sender.validate_image',return_value=(image,digest)):
            whale.deliver(self.db,self.device,'image-job','image','',str(image),digest)
            item=whale.history(self.db,self.device)['history'][0]
            self.assertIn('/api/wechat/assistant/image',item['image'])
            self.assertEqual(whale.image(self.db,self.device,item['seq'])[0],image.read_bytes())
            with self.assertRaises(ValueError):whale.image(self.db,self.other,item['seq'])

    def test_remote_route_allowlist_excludes_credentials_shutdown_and_files(self):
        for route in ['/api/app/quit','/api/logs','/api/storage','/api/comment-login','/api/astrbot/start']:
            self.assertNotIn(route,whale.POST_ROUTES)
        with self.assertRaises(ValueError):whale.configure(self.db,{'channel':'whale','device':'f'*64})


class WhaleHTTPTests(unittest.TestCase):
    setUp=WhaleChannelTests.setUp
    def test_http_boundary_and_remote_shutdown_denial(self):
        import threading,urllib.request,urllib.error
        from http.server import ThreadingHTTPServer
        from app import console_server
        file=self.root/'fixture.sqlite3';store=LiveStore(file);store.db.close()
        with patch.object(console_server,'ASSISTANT_DB',file):
            server=ThreadingHTTPServer(('127.0.0.1',0),console_server.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                def request(path,body,token='c'*64):
                    req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,data=json.dumps(body).encode(),headers={'Host':'127.0.0.1:8793','Origin':'http://127.0.0.1:8793','Content-Type':'application/json','X-Whale-Bridge':token,'X-Whale-Device':self.device})
                    return urllib.request.urlopen(req,timeout=3)
                with self.assertRaises(urllib.error.HTTPError) as failure:request('/api/whale-bridge',{'action':'state'},'wrong')
                self.assertEqual(failure.exception.code,403)
                failure.exception.close()
                with request('/api/whale-bridge',{'action':'receive','id':'f'*32,'text':'isolated test'}) as response:self.assertTrue(json.load(response)['ok'])
                with request('/api/whale-bridge',{'action':'state'}) as response:self.assertEqual(len(json.load(response)['history']),1)
                with self.assertRaises(urllib.error.HTTPError) as failure:request('/api/app/quit',{})
                self.assertEqual(failure.exception.code,403)
                failure.exception.close()
            finally:server.shutdown();server.server_close();thread.join(timeout=3)


if __name__=='__main__':unittest.main()
