import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import manual_images as manual


class ManualImageTests(unittest.TestCase):
    def test_install_reuses_models_without_touching_vendor_or_outbox(self):
        with tempfile.TemporaryDirectory(dir=manual.local_images.ROOT) as folder:
            root = Path(folder)
            site = root / 'venv/Lib/site-packages/comfyui_frontend_package/static'
            (site / 'scripts').mkdir(parents=True)
            vendor = site / 'scripts/app.js'
            vendor.write_text('vendor sentinel', encoding='utf-8')
            with patch.object(manual.local_images, 'BASE', root):
                target = manual.install_assets()
                prompt = json.loads((target / 'workflow.json').read_text(encoding='utf-8'))
                manual.install_assets()  # Repeatable without altering vendor files.
            self.assertEqual(vendor.read_text(encoding='utf-8'), 'vendor sentinel')
            self.assertEqual(prompt['8']['inputs']['filename_prefix'], 'manual-qwen')
            self.assertEqual(prompt['1']['inputs']['unet_name'], 'qwen_image_2.1-Q4_K.gguf')
            self.assertEqual(prompt['5']['inputs']['batch_size'], 1)
            self.assertEqual(prompt['6']['inputs']['steps'], 8)
            self.assertFalse((root / 'assistant.sqlite3').exists())
            self.assertFalse((root / 'outgoing-images').exists())

    def test_missing_frontend_fails_without_creating_fake_install(self):
        with tempfile.TemporaryDirectory(dir=manual.local_images.ROOT) as folder:
            with patch.object(manual.local_images, 'BASE', Path(folder)):
                with self.assertRaisesRegex(RuntimeError, 'frontend is missing'):
                    manual.install_assets()
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_open_page_does_not_wake_or_require_model(self):
        with patch('sys.argv',['manual_images']),patch.object(manual,'ensure_portal') as portal,patch.object(manual,'open_app') as browser,patch.object(manual,'install_assets') as install,patch.object(manual.local_images,'ensure_backend') as backend:
            manual.main()
        portal.assert_called_once();browser.assert_called_once();install.assert_not_called();backend.assert_not_called()

    def test_no_browser_starts_only_portal(self):
        with patch('sys.argv',['manual_images','--no-browser']),patch.object(manual,'ensure_portal') as portal,patch.object(manual,'open_app') as browser,patch.object(manual.local_images,'ensure_backend') as backend:
            manual.main()
        portal.assert_called_once();browser.assert_not_called();backend.assert_not_called()

    def test_manager_portal_open_is_local_only_and_does_not_wake_model(self):
        import io
        from unittest.mock import Mock
        from app.console_server import Handler
        for origin,expected in [('https://untrusted.invalid',403),('http://127.0.0.1:8793',200)]:
            h=object.__new__(Handler);h.path='/api/manual-image/open';h.headers={'Host':'127.0.0.1:8793','Origin':origin,'Content-Type':'application/json','Content-Length':'2'};h.rfile=io.BytesIO(b'{}');h._send=Mock()
            with patch.object(manual,'ensure_portal') as portal,patch.object(manual.local_images,'ensure_backend') as backend:h.do_POST()
            self.assertEqual(h._send.call_args.args[0],expected)
            self.assertEqual(portal.call_count,1 if expected==200 else 0);backend.assert_not_called()


if __name__ == '__main__':
    unittest.main()
