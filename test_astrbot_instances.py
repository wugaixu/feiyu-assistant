import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import astrbot_instances as ai,astrbot_client as ac,astrbot_worker as worker


class InstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=r'__TEMP_PARENT__',ignore_cleanup_errors=True);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.addCleanup(patch.stopall)
        patch.object(ai,'ROOT',self.root).start();patch.object(ac,'ROOT',self.root).start()
        (self.root/'astrbot-instances.json').write_text(json.dumps({'local8k':{'title':'本地 8K','port':6186}}))
        self.sid='local-session'
        (self.root/'astrbot-sessions.json').write_text(json.dumps([{'id':'mai','title':'麦麦'},
            {'id':self.sid,'title':'本地 8K','instance_id':'local8k','config_id':'default','context_tokens':8192}]))
        for key in ('default','local8k'):
            info=ai.info(key);info['root'].mkdir(parents=True,exist_ok=True)
            path=self.root/'astrbot-bridge.json' if key=='default' else info['root']/'bridge.json'
            path.write_text(json.dumps({'api_key':'abk_'+key,'username':'douyin-owner-__OWNER_UID__'}))

    def test_separate_directories_and_ports(self):
        self.assertNotEqual(ai.info('default')['root'],ai.info('local8k')['root'])
        self.assertEqual(ai.info('local8k')['url'],'http://127.0.0.1:6186/')

    def test_unknown_instance_never_falls_back(self):
        with self.assertRaises(ValueError):
            with ai.scope('missing'):pass
        self.assertEqual(ai.CURRENT.get(),'default')

    def test_scope_restores_after_failure(self):
        with self.assertRaises(RuntimeError):
            with ai.session_scope(self.sid):
                self.assertEqual(ai.CURRENT.get(),'local8k');raise RuntimeError()
        self.assertEqual(ai.CURRENT.get(),'default')

    def test_credentials_and_api_endpoint_follow_instance(self):
        with patch.object(ac.OPENER,'open') as opened:
            with ai.session_scope(self.sid):
                ac.open_request('/file',timeout=1)
            request=opened.call_args.args[0]
            self.assertTrue(request.full_url.startswith('http://127.0.0.1:6186/api/v1/'))
            self.assertEqual(request.headers['Authorization'],'Bearer abk_local8k')
        self.assertEqual(ac.config()['api_key'],'abk_default')

    def test_sessions_and_configs_preserve_instance_binding(self):
        def request(path,**kwargs):
            if path=='/chat/configs':return [{'id':'default','name':'default'}]
            return {'sessions':[{'session_id':self.sid if ai.CURRENT.get()=='local8k' else 'mai'}]}
        with patch.object(ac,'request',side_effect=request):state=ac.state()
        self.assertEqual(len(state['sessions']),2)
        local=next(x for x in state['sessions'] if x['id']==self.sid)
        self.assertEqual(local['context_tokens'],8192);self.assertEqual(local['instance_id'],'local8k')

    def test_worker_and_attachments_remain_in_same_instance(self):
        with patch.object(worker,'_run',side_effect=lambda task,target:ai.CURRENT.get()):
            self.assertEqual(worker.run('task',{'session_id':self.sid}),'local8k')
        with patch.object(worker,'_finalize_parts',side_effect=lambda *args:ai.CURRENT.get()):
            self.assertEqual(worker.finalize_parts([{'type':'image','_astrbot_instance':'local8k'}],'task'),'local8k')
        self.assertEqual(ai.CURRENT.get(),'default')

    def test_chat_selects_instance_and_marks_attachment_origin(self):
        with patch.object(ac,'_chat',side_effect=lambda *args:[{'type':'image','attachment_id':ai.CURRENT.get()}]):
            result=ac.chat(self.sid,[])
        self.assertEqual(result,[{'type':'image','attachment_id':'local8k','_astrbot_instance':'local8k'}])

    def test_diary_does_not_follow_local_instance(self):
        from app import diary_proactive
        with patch('app.event_dispatch.target_config',return_value={'provider':'astrbot','astrbot':{'session_id':self.sid}}):
            self.assertIsNone(diary_proactive.target())

    def test_registry_rejects_external_or_invalid_endpoint(self):
        (self.root/'astrbot-instances.json').write_text(json.dumps({'local8k':{'port':443}}))
        with self.assertRaises(ValueError):ai.registry()

    def test_streaming_reads_only_selected_instance_profile(self):
        path=ai.info('local8k')['root']/'runtime/data/cmd_config.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'provider_settings':{'streaming_response':False}}))
        with ai.scope('local8k'),patch.object(ac,'request',return_value=[{'id':'default','path':str(path)}]):
            self.assertFalse(ac.streaming_enabled('default'))
