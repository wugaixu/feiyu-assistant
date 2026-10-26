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


if __name__ == '__main__':
    unittest.main()
