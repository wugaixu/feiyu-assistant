import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import manual_portal as portal


class PortalTests(unittest.TestCase):
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
