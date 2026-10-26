import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import local_images as li
from app.image_sender import validate_image
from app.live_service import ROOT


class LocalImageFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        (self.root / 'ComfyUI/output').mkdir(parents=True)
        (self.root / 'outgoing-images').mkdir()
        Image.new('RGB', (64, 48), (10, 120, 200)).save(self.root / 'ComfyUI/output/img.png', 'PNG')

    def tearDown(self):
        self.temp.cleanup()

    def saved(self, fmt):
        item = {'outputs': {'8': {'images': [{'type': 'output', 'filename': 'img.png', 'subfolder': ''}]}}}
        with patch.object(li, 'BASE', self.root), patch.object(li, 'ROOT', self.root):
            return li._saved_output(item, 'a' * 32, fmt)

    def test_every_supported_format_is_written_and_sendable(self):
        for fmt, suffix, expected in [('png', '.png', 'PNG'), ('jpeg', '.jpg', 'JPEG'),
                                      ('jpg', '.jpg', 'JPEG'), ('webp', '.webp', 'WEBP'),
                                      ('gif', '.gif', 'GIF')]:
            path = self.saved(fmt)
            self.assertEqual(path.suffix, suffix, fmt)
            with Image.open(path) as image:
                self.assertEqual(image.format, expected, fmt)
            with patch('app.resource_storage.resource_dirs', return_value=[]), \
                 patch('app.image_sender.ROOT', self.root):
                resolved, digest = validate_image(path)
            self.assertEqual(resolved, path.resolve())
            self.assertEqual(len(digest), 64)

    def test_rejects_formats_the_chat_cannot_carry(self):
        with self.assertRaises(ValueError):
            self.saved('bmp')
        with self.assertRaises(ValueError):
            self.saved('tiff')

    def test_batch_format_normalizes_and_bounds(self):
        self.assertEqual(li.batch_format(None), 'png')
        self.assertEqual(li.batch_format('PNG'), 'png')
        self.assertEqual(li.batch_format('JPG'), 'jpeg')
        self.assertEqual(li.batch_format(' webp '), 'webp')
        self.assertEqual(li.batch_format('gif'), 'gif')
        for value in ('bmp', 'tiff', 'avif', 'exe'):
            with self.assertRaises(ValueError):
                li.batch_format(value)

    def test_schema_adds_format_column(self):
        import sqlite3
        db = sqlite3.connect(self.root / 'test.sqlite3')
        try:
            li.schema(db)
            columns = {row[1] for row in db.execute('PRAGMA table_info(local_image_jobs)')}
            self.assertIn('format', columns)
            # A second run must not fail on the existing column.
            li.schema(db)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
