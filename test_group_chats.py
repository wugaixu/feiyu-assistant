import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app import group_chats as g,channels,reply_batch,local_runtime as lr,task_context as tc,event_dispatch as ed
from app.live_service import LiveStore,ROOT
from app.sdk_receiver import ingest,OWNER,OWNER_UID,ACCOUNT_UID

GID='50001';OTHER_GROUP='50002';UID='123456789';OTHER='234567890'


class GroupChatTests(unittest.TestCase):
    def setUp(self):
        self.db=LiveStore(':memory:').db;self.addCleanup(self.db.close)
        channels.schema(self.db)
        def sdk(gid=None):
            info=lambda x:{'group_id':x,'name':'测试群'+x,'is_participant':True}
            if gid is None:return {'ok':True,'groups':[info(GID),info(OTHER_GROUP)]}
            return {'ok':True,'group':info(gid),'members':[{'uid':UID,'name':'甲'},{'uid':OTHER,'name':'乙'},{'uid':OWNER_UID,'name':'主人账号'}]}
        self.sdk=patch.object(g,'sdk_read',side_effect=sdk);self.sdk.start();self.addCleanup(self.sdk.stop)
        self.save()

    def save(self,gid=GID,members=None,enabled=True):
        g.schema(self.db)
        row=self.db.execute('SELECT revision FROM group_reply_policy WHERE group_id=?',(gid,)).fetchone()
        return g.save(self.db,{'group_id':gid,'members':members if members is not None else [UID],'enabled':enabled,'revision':row[0] if row else 0})

    def event(self,mid='message1',seq=1,uid=UID,gid=GID,kind='text'):
        return {'seq':seq,'message_id':mid,'sender_uid':uid,'account_uid':ACCOUNT_UID,
            'timestamp':time.time()+1,'is_group':True,'group_id':gid,'thread_id':gid,'content':{'kind':kind},'text':'群员说你好'}

    def receive(self,**kwargs):
        event=self.event(**kwargs);self.assertEqual(ingest(self.db,[event]),1)
        return self.db.execute('SELECT task_id FROM sdk_inbound_ids WHERE message_id=?',('group:'+event['group_id']+':'+event['message_id'],)).fetchone()[0]

    def queue(self):
        task=self.receive()
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(task,))
        reply_batch.finish(self.db,task,[{'type':'text','text':'你好呀！'}])
        return task

    def test_only_selected_group_and_member(self):
        self.assertEqual(ingest(self.db,[self.event(uid=OTHER)]),0)
        self.assertEqual(ingest(self.db,[self.event(seq=2,gid=OTHER_GROUP)]),0)
        task=self.receive(mid='allowed',seq=3)
        raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(task,)).fetchone()[0])
        self.assertEqual(raw['sender_name'],'甲');self.assertEqual(raw['group_name'],'测试群'+GID)
        self.assertEqual(raw['reply_device'],'group:'+GID+':'+UID)

    def test_wrong_account_thread_old_event_self_and_private_stranger_rejected(self):
        events=[]
        for i,change in enumerate([{'account_uid':'wrong'},{'thread_id':'999'},{'timestamp':1},{'sender_uid':ACCOUNT_UID},{'is_group':False}],1):
            events.append({**self.event(seq=i),**change})
        self.assertEqual(ingest(self.db,events),0)
        self.assertEqual(self.db.execute('SELECT seq FROM sdk_inbound_cursor').fetchone()[0],5)

    def test_duplicate_and_equal_ids_in_different_groups_do_not_collide(self):
        self.receive();self.assertEqual(ingest(self.db,[self.event()]),0)
        self.save(OTHER_GROUP);self.receive(gid=OTHER_GROUP,seq=2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],2)

    def test_group_does_not_change_owner_proactive_or_private_route(self):
        with self.db:channels.observe(self.db,'whale','a'*64)
        task=self.receive()
        self.assertEqual(channels.address(self.db,task),{'channel':'douyin','device':None,'group_id':GID})
        self.assertEqual(channels.current(self.db),{'channel':'whale','device':'a'*64})
        own={**self.event(seq=2,mid='owner'), 'is_group':False,'sender_uid':OWNER_UID};own.pop('group_id')
        self.assertEqual(ingest(self.db,[own]),1)

    def test_group_media_uses_common_pipeline_with_distinct_source(self):
        task=self.receive(kind='image')
        raw,status=self.db.execute('SELECT raw_json,status FROM live_tasks WHERE id=?',(task,)).fetchone()
        self.assertEqual(status,'media_pending')
        self.assertFalse(channels.same_source(raw,json.dumps({'transport':'sdk'})))
        self.assertFalse(channels.same_source(raw,json.dumps({'transport':'sdk','reply_device':'group:'+OTHER_GROUP+':'+UID})))
        self.assertFalse(channels.same_source(raw,json.dumps({'transport':'sdk','reply_device':'group:'+GID+':'+OTHER})))

    def test_save_verifies_sdk_members_and_optimistic_version(self):
        for members in (['unknown'],[ACCOUNT_UID],[]):
            with self.assertRaises(ValueError):self.save(members=members)
        with self.assertRaises(ValueError):g.save(self.db,{'group_id':GID,'members':[UID],'enabled':True,'revision':0})
        self.save(members=[UID,OTHER]);self.assertEqual(len(g.state(self.db)['groups'][0]['members']),2)

    def test_member_enable_does_not_replay_prior_messages(self):
        old=time.time()-10;self.save(members=[UID,OTHER])
        self.assertFalse(g.allowed(self.db,GID,OTHER,old))
        self.assertIsNotNone(g.allowed(self.db,GID,OTHER,time.time()+1))

    def test_pause_is_local_and_cancels_pending_not_attempted(self):
        task=self.queue();revision=g.state(self.db)['groups'][0]['revision']
        with patch.object(g,'sdk_read',side_effect=AssertionError('Pause must work offline')):
            g.action(self.db,{'action':'pause','group_id':GID,'revision':revision})
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'cancelled')
        self.assertEqual(ingest(self.db,[self.event(seq=2,mid='after')]),0)
        self.assertFalse(g.task_allowed(self.db,json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(task,)).fetchone()[0])))

    def test_member_change_cancels_queued_replies(self):
        self.queue();self.save(members=[OTHER])
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'cancelled')

    def test_settings_do_not_rearm_attempted(self):
        self.queue()
        with self.db:self.db.execute("UPDATE reply_outbox SET status='attempted'")
        self.save(enabled=False)
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'attempted')

    def test_finish_routes_automatic_reply_to_group_without_approval(self):
        task=self.queue()
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'pending')
        with tempfile.TemporaryDirectory(dir=ROOT) as folder,patch.object(lr,'ROOT',Path(folder)),\
             patch.object(lr,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0,'retry_rejected':True,'retry_max':10}),\
             patch('app.sdk_sender.health',return_value={'online':True,'uid':ACCOUNT_UID,'capabilities':{'group_chat':1}}),\
             patch('app.sdk_sender.post',return_value={'result':'platform_not_accepted'}) as send,patch('app.sdk_sender.send') as private:
            lr.outbound_step(self.db,OWNER);lr.outbound_step(self.db,OWNER)
        send.assert_called_once_with('/group-send',{'outbox_id':task+':batch:0'});private.assert_not_called()

    def test_old_sdk_holds_group_reply_pending(self):
        self.queue()
        with patch.object(lr,'dm_policy',return_value={'enabled':True,'min_send_gap_seconds':0}),\
             patch('app.sdk_sender.health',return_value={'online':True,'uid':ACCOUNT_UID}),patch('app.sdk_sender.post') as send:
            result=lr.outbound_step(self.db,OWNER)
        self.assertEqual(result['state'],'sdk_group_unsupported');send.assert_not_called()
        self.assertEqual(self.db.execute('SELECT status FROM reply_outbox').fetchone()[0],'pending')

    def test_packet_and_envelope_identify_actual_group_and_sender(self):
        task=self.receive();raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks').fetchone()[0])
        with tempfile.TemporaryDirectory(dir=ROOT) as folder,patch.object(tc,'PACKETS',Path(folder)):
            envelope=tc.request(task,OWNER,raw,[]);packet=tc.packet_path(task)
            self.assertIn('[抖音群聊]',envelope);self.assertIn('sender_uid',envelope)
            self.assertIn('测试群'+GID,packet.read_text(encoding='utf-8'));self.assertIn('甲',packet.read_text(encoding='utf-8'))
            data=json.loads(packet.with_name('data.json').read_text(encoding='utf-8'))
            self.assertIsNone(data['sender_profile']);self.assertEqual(data['source']['sender_uid'],UID)
            self.assertNotIn('workflow_delegate',envelope)

    def test_group_cannot_comment_or_get_image_permits(self):
        from app import astrbot_worker as aw,shared_comments,local_images
        task=self.queue();raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks').fetchone()[0])
        with self.db:self.db.execute("UPDATE reply_outbox SET status='server_accepted'")
        record={'kind':'video_card','source':'sdk','video':{'source':'sdk_shared_work'},'received_at':time.time()+1}
        self.assertFalse(shared_comments.eligible(raw,record,{'enabled':True,'enabled_since':0}))
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(task,))
        self.assertEqual(local_images.permit(self.db,task,'session'), '')

    def test_dispatch_same_agent_and_session_and_private_priority(self):
        group_task=self.receive()
        own={**self.event(seq=2,mid='owner'), 'is_group':False,'sender_uid':OWNER_UID,'text':'主人新消息'};own.pop('group_id')
        ingest(self.db,[own])
        with self.db:self.db.execute('UPDATE live_tasks SET received=?',(time.time()-20,))
        with tempfile.TemporaryDirectory(dir=ROOT) as folder,patch.object(tc,'PACKETS',Path(folder)),\
             patch.object(ed,'target_config',return_value={'provider':'codex','mode':'explicit','thread_id':ed.DEFAULT_THREAD_ID}):
            calls=[]
            def runner(args,**kwargs):
                calls.append(args);return SimpleNamespace(returncode=0,stderr='',stdout='Queued message 11111111-1111-1111-1111-111111111111 for thread '+ed.DEFAULT_THREAD_ID+'.\n')
            ed.dispatch_one(self.db,runner,'C:/codex.exe');self.assertIn('主人新消息',calls[-1][-1])
            result=ed.dispatch_one(self.db,runner,'C:/codex.exe');self.assertEqual(result['task_id'],group_task)
            self.assertIn('[抖音群聊]',calls[-1][-1]);self.assertIn(ed.DEFAULT_THREAD_ID,calls[-1])

    def test_busy_group_does_not_block_ready_private_message(self):
        self.receive()
        own={**self.event(seq=2,mid='owner'), 'is_group':False,'sender_uid':OWNER_UID};own.pop('group_id')
        ingest(self.db,[own])
        with self.db:self.db.execute("UPDATE live_tasks SET received=? WHERE json_extract(raw_json,'$.is_group') IS NULL",(time.time()-20,))
        with tempfile.TemporaryDirectory(dir=ROOT) as folder,patch.object(tc,'PACKETS',Path(folder)),\
             patch.object(ed,'target_config',return_value={'provider':'codex','mode':'explicit','thread_id':ed.DEFAULT_THREAD_ID}):
            result=ed.dispatch_one(self.db,lambda *a,**k:SimpleNamespace(returncode=0,stderr='',stdout='Queued message 11111111-1111-1111-1111-111111111111 for thread '+ed.DEFAULT_THREAD_ID+'.\n'),'C:/codex.exe')
        self.assertEqual(json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(result['task_id'],)).fetchone()[0]).get('is_group'),None)

    def test_group_management_csrf_remote_auth_and_removed_delegation_api(self):
        from app import console_server as cs
        from http.server import ThreadingHTTPServer
        import threading,urllib.request,urllib.error
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            path=Path(folder)/'isolated.sqlite3';target=sqlite3.connect(path);self.db.backup(target);target.close()
            with patch.object(cs,'ASSISTANT_DB',path),patch('app.whale_channel.authenticate',return_value=False):
                server=ThreadingHTTPServer(('127.0.0.1',0),cs.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                def call(route='/api/groups',headers=None,body=None):
                    return urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+route,
                        data=json.dumps(body).encode() if body is not None else None,headers={'Host':'127.0.0.1:8793','Content-Type':'application/json',**(headers or {})}),timeout=3)
                try:
                    for headers in ({'Origin':'https://other.invalid'},{'X-Whale-Bridge':'wrong','X-Whale-Device':'b'*64}):
                        with self.assertRaises(urllib.error.HTTPError) as raised:call(headers=headers,body={'action':'list'})
                        self.assertEqual(raised.exception.code,403);raised.exception.close()
                    with self.assertRaises(urllib.error.HTTPError) as raised:call(route='/api/delegated')
                    self.assertEqual(raised.exception.code,404);raised.exception.close()
                    with call(body={'action':'list'}) as response:self.assertEqual(len(json.load(response)['groups']),2)
                finally:server.shutdown();server.server_close();thread.join(timeout=2)

    def test_retirement_keeps_history_and_disarms_old_feature(self):
        self.db.executescript("CREATE TABLE delegated_contacts(enabled INTEGER);INSERT INTO delegated_contacts VALUES(1); CREATE TABLE delegated_drafts(status TEXT); INSERT INTO delegated_drafts VALUES('awaiting_approval');")
        with self.db:
            self.db.execute("INSERT INTO live_tasks(id,owner,received,raw_json,status) VALUES('old',?,0,?,'pending')",(OWNER,json.dumps({'origin':'delegate_inbound'})))
            self.db.execute("INSERT INTO reply_outbox(id,owner,text) VALUES('delegate:old',?,'old')",(OWNER,))
        g.schema(self.db)
        self.assertEqual(self.db.execute('SELECT enabled FROM delegated_contacts').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT status FROM delegated_drafts').fetchone()[0],'cancelled')
        self.assertEqual(self.db.execute("SELECT status FROM live_tasks WHERE id='old'").fetchone()[0],'cancelled')
        self.assertEqual(self.db.execute("SELECT status FROM reply_outbox WHERE id='delegate:old'").fetchone()[0],'cancelled')


if __name__=='__main__':unittest.main()
