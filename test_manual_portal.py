import json
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import manual_portal as portal


class PortalTests(unittest.TestCase):
    def test_http_record_edit_requires_token_and_keeps_plugin_lookup_available(self):
        with tempfile.TemporaryDirectory(dir=portal.local_images.ROOT) as folder:
            server=ThreadingHTTPServer(('127.0.0.1',0),portal.Handler)
            port=server.server_address[1]
            jobs={'done':{'id':'done','state':'done'}}
            with patch.object(portal,'PORT',port),patch.object(portal,'STATE',Path(folder)),patch.object(portal,'JOBS',jobs):
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                def post(token):
                    request=urllib.request.Request(f'http://127.0.0.1:{port}/api/records',
                        data=b'{"action":"delete","ids":["done"]}',headers={'X-Manual-Token':token,'Content-Type':'application/json'})
                    return op.open(request,timeout=3)
                try:
                    with self.assertRaises(urllib.error.HTTPError) as error:post('wrong')
                    self.assertEqual(error.exception.code,403);error.exception.close();self.assertNotIn('deleted_at',jobs['done'])
                    with post(portal.TOKEN) as response:self.assertEqual(json.load(response)['changed'],1)
                    with op.open(f'http://127.0.0.1:{port}/api/jobs',timeout=3) as response:self.assertEqual(json.load(response)['jobs'],[])
                    with op.open(f'http://127.0.0.1:{port}/api/jobs?include_deleted=1',timeout=3) as response:self.assertEqual(json.load(response)['jobs'][0]['id'],'done')
                finally:
                    server.shutdown();server.server_close();thread.join(timeout=3)

    def test_delete_and_restore_preserve_image_files_and_completed_job_lookup(self):
        with tempfile.TemporaryDirectory(dir=portal.local_images.ROOT) as folder:
            root=Path(folder);image=root/'saved.png';image.write_bytes(b'original file')
            jobs={'done':{'id':'done','state':'done','files':['saved.png']}}
            with patch.object(portal,'STATE',root),patch.object(portal,'JOBS',jobs):
                result=portal.edit_records({'action':'delete','ids':['done']})
                self.assertEqual(result['changed'],1);self.assertEqual(portal.visible_jobs(),[])
                self.assertEqual(portal.visible_jobs(True)[0]['id'],'done')
                self.assertIn('deleted_at',json.loads((root/'jobs.json').read_text())['done'])
                self.assertEqual(image.read_bytes(),b'original file')
                portal.edit_records({'action':'restore','ids':['done']})
                self.assertEqual(portal.visible_jobs()[0]['id'],'done')
                self.assertNotIn('deleted_at',jobs['done'])

    def test_clear_skips_running_and_submitting_and_is_idempotent(self):
        jobs={state:{'id':state,'state':state} for state in ('running','submitting','done','failed','uncertain')}
        with patch.object(portal,'JOBS',jobs),patch.object(portal,'persist'):
            result=portal.edit_records({'action':'delete','all_finished':True})
            self.assertEqual(result['changed'],3);self.assertEqual(result['skipped'],2)
            self.assertEqual({x['state'] for x in portal.visible_jobs()},{'running','submitting'})
            self.assertEqual(portal.edit_records({'action':'delete','all_finished':True})['changed'],0)

    def test_delete_failure_rolls_back_in_memory(self):
        jobs={'done':{'id':'done','state':'done'}}
        with patch.object(portal,'JOBS',jobs),patch.object(portal,'persist',side_effect=OSError):
            with self.assertRaises(OSError):portal.edit_records({'action':'delete','ids':['done']})
            self.assertNotIn('deleted_at',jobs['done'])

    def test_record_actions_validate_ids_before_any_mutation(self):
        jobs={'done':{'id':'done','state':'done'}}
        with patch.object(portal,'JOBS',jobs),patch.object(portal,'persist') as persist:
            for data in ({'action':'delete','ids':['done','unknown']},{'action':'delete','ids':[]},
                         {'action':'delete','ids':['../file']},{'action':'restore','all_finished':True}):
                with self.assertRaises(ValueError):portal.edit_records(data)
            persist.assert_not_called();self.assertNotIn('deleted_at',jobs['done'])

    def test_visible_limit_counts_only_visible_records(self):
        jobs={str(i):{'id':str(i),'state':'done','deleted_at':1} for i in range(40)}
        jobs['0'].pop('deleted_at')
        with patch.object(portal,'JOBS',jobs):
            self.assertEqual([x['id'] for x in portal.visible_jobs()],['0'])
            self.assertEqual(len(portal.visible_jobs(True)),30)

    def test_text_and_multiple_references_use_native_qwen_latent(self):
        with patch.object(portal, 'UPLOADS', {'a.png', 'b.png'}):
            for refs in ([], ['a.png'], ['a.png', 'b.png']):
                graph = portal.graph_for({'prompt': '测试', 'images': refs})
                self.assertEqual(graph['6']['inputs']['latent_image'], ['4', 2])
                self.assertEqual(graph['8']['class_type'], 'PreviewImage')
                for i, name in enumerate(refs, 1):
                    self.assertEqual(graph[str(100+i)]['inputs']['image'], name)
                    self.assertEqual(graph['4']['inputs'][f'images.image_{i}'], [str(100+i), 0])

    def test_rejects_unuploaded_paths_and_invalid_inputs(self):
        for data in ({'prompt': ''}, {'prompt': 'x', 'images': ['../secret']},
                     {'prompt': 'x', 'steps': True}, {'prompt': 'x', 'seed': 2**40},
                     {'prompt': 'x', 'resolution': 800}):
            with self.assertRaises(ValueError):
                portal.graph_for(data)

    def test_background_save_is_idempotent_and_uses_requested_directory(self):
        with tempfile.TemporaryDirectory(dir=portal.local_images.ROOT) as folder:
            root = Path(folder)
            job = {'id': 'test', 'prompt_id': 'test', 'state': 'running'}
            item = {'outputs': {'8': {'images': [{'filename': 'x.png', 'type': 'temp', 'subfolder': ''}]}}, 'status': {'completed': True}}
            body = b'\x89PNG\r\n\x1a\nexample'
            with patch.object(portal, 'OUTPUT', root / 'final'), patch.object(portal, 'STATE', root), patch.object(portal.local_images, 'api', return_value={'test': item}), patch.object(portal, 'request_bytes', return_value=body) as download:
                portal.complete(job)
                portal.complete(job)
                self.assertEqual(download.call_count, 1)
                self.assertEqual(job['state'], 'done')
                self.assertEqual((root / 'final' / job['files'][0]).read_bytes(), body)

    def test_model_failure_is_reported_without_fake_output(self):
        job = {'prompt_id': 'test'}
        item = {'status': {'status_str': 'error', 'messages': [['execution_error', {'exception_message': 'out of memory'}]]}}
        with patch.object(portal.local_images, 'api', return_value={'test': item}):
            portal.complete(job)
        self.assertEqual(job['state'], 'failed')
        self.assertIn('out of memory', job['error'])
        self.assertNotIn('files', job)
