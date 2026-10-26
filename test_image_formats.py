import io, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import astrbot_client as client
from app import image_sender as sender
from app.live_service import ROOT


def gif_bytes(frames=2):
    buf = io.BytesIO()
    images = [Image.new('RGB', (8, 8), (index * 60 + 10, index * 30 + 5, 20)) for index in range(frames)]
    images[0].save(buf, format='GIF', save_all=True, append_images=images[1:], duration=100, loop=0)
    data = buf.getvalue()
    with Image.open(io.BytesIO(data)) as check:
        assert check.n_frames == frames, check.n_frames
    return data


class InboundImageFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        (self.root / 'outgoing-images').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def save(self, raw):
        with patch.object(client, 'ROOT', self.root):
            return client.save_image(raw, 'a' * 32)

    def test_animated_gif_is_kept_byte_for_byte(self):
        raw = gif_bytes(2)
        path = Path(self.save(raw))
        self.assertEqual(path.suffix, '.gif')
        self.assertEqual(path.read_bytes(), raw)
        with Image.open(path) as image:
            self.assertEqual(image.n_frames, 2)

    def test_jpeg_webp_and_png_keep_their_containers(self):
        for fmt, suffix in (('JPEG', '.jpg'), ('WEBP', '.webp'), ('PNG', '.png')):
            buf = io.BytesIO()
            Image.new('RGB', (8, 8), (1, 2, 3)).save(buf, format=fmt)
            path = Path(self.save(buf.getvalue()))
            self.assertEqual(path.suffix, suffix)
            self.assertEqual(path.read_bytes(), buf.getvalue())

    def test_bmp_is_reencoded_to_png(self):
        buf = io.BytesIO()
        Image.new('RGB', (8, 8), (9, 9, 9)).save(buf, format='BMP')
        path = Path(self.save(buf.getvalue()))
        self.assertEqual(path.suffix, '.png')
        with Image.open(path) as image:
            self.assertEqual(image.format, 'PNG')

    def test_broken_or_oversized_bytes_are_rejected(self):
        with self.assertRaises(Exception):
            self.save(b'not-an-image')
        with self.assertRaises(ValueError):
            self.save(b'\x00' * (10 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            with patch.object(client, 'ROOT', self.root):
                client.save_image('text', 'a' * 32)


class OutboundImageFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        (self.root / 'outgoing-images').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_validate_image_accepts_gif_and_webp(self):
        gif = self.root / 'outgoing-images' / 'anim.gif'
        gif.write_bytes(gif_bytes(2))
        webp = self.root / 'outgoing-images' / 'pic.webp'
        buf = io.BytesIO()
        Image.new('RGB', (8, 8)).save(buf, format='WEBP')
        webp.write_bytes(buf.getvalue())
        with patch.object(sender, 'ROOT', self.root), \
             patch('app.resource_storage.resource_dirs', return_value=[]):
            for path in (gif, webp):
                resolved, digest = sender.validate_image(path)
                self.assertEqual(resolved, path.resolve())
                self.assertEqual(len(digest), 64)

    def test_disguised_suffix_is_still_rejected(self):
        fake = self.root / 'outgoing-images' / 'fake.gif'
        fake.write_text('not a gif')
        with patch.object(sender, 'ROOT', self.root), \
             patch('app.resource_storage.resource_dirs', return_value=[]):
            with self.assertRaises(Exception):
                sender.validate_image(fake)


if __name__ == '__main__':
    unittest.main()
