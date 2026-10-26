import json, tempfile, time, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from app import local_images as li


class LocalImageIdleTests(unittest.TestCase):
    def test_idle_minutes_defaults_and_bounds(self):
        with tempfile.TemporaryDirectory(dir=li.ROOT) as folder:
            root = Path(folder)
            with patch.object(li, 'ROOT', root):
                self.assertEqual(li.idle_minutes(), 10)
                for value, expected in [(10, 10), (0, 10), (61, 10), ('10', 10), (True, 10), (30, 30)]:
                    (root / 'local-image.json').write_text(json.dumps({'idle_minutes': value}), encoding='utf-8')
                    self.assertEqual(li.idle_minutes(), expected)

    def test_idle_expiry_uses_recorded_activity(self):
        with tempfile.TemporaryDirectory(dir=li.ROOT) as folder:
            root = Path(folder)
            with patch.object(li, 'ROOT', root), patch.object(li, 'ACTIVITY', root / 'activity.json'):
                self.assertFalse(li.idle_expired())
                li.touch('unit')
                self.assertFalse(li.idle_expired())
                self.assertFalse(li.idle_expired(minutes=60))
                (root / 'activity.json').write_text(json.dumps({'at': time.time() - 11 * 60}), encoding='utf-8')
                self.assertTrue(li.idle_expired())
                self.assertFalse(li.idle_expired(minutes=60))
                (root / 'activity.json').write_text('{broken', encoding='utf-8')
                self.assertFalse(li.idle_expired())

    def test_stop_backend_only_ends_the_owned_process(self):
        with tempfile.TemporaryDirectory(dir=li.ROOT) as folder:
            root = Path(folder)
            process = Mock(); process.poll.return_value = None
            with patch.object(li, 'BASE', root), patch.object(li, '_process', process), patch.object(li, '_log', None):
                self.assertTrue(li.stop_backend('unit'))
                process.terminate.assert_called_once()
            self.assertIsNone(li._process)
            self.assertFalse(li.stop_backend('again'))
            self.assertTrue((root / 'backend-idle-exit.log').is_file())

    def test_backend_can_restart_after_idle_exit(self):
        with tempfile.TemporaryDirectory(dir=li.ROOT) as folder:
            root = Path(folder)
            (root / 'venv/Scripts').mkdir(parents=True)
            (root / 'venv/Scripts/pythonw.exe').touch()
            (root / 'ComfyUI').mkdir()
            (root / 'ComfyUI/main.py').touch()
            process = Mock(); process.poll.return_value = None
            fake = Mock()
            with patch.object(li, 'BASE', root), patch.object(li, '_process', process), patch.object(li, '_log', None), \
                 patch.object(li, 'api', side_effect=ConnectionError('offline')), \
                 patch.object(li.subprocess, 'Popen', return_value=fake) as popen:
                self.assertTrue(li.stop_backend('unit'))
                self.assertFalse(li.ensure_backend())
                popen.assert_called_once()
                self.assertIs(li._process, fake)
                li.stop_backend('cleanup')


if __name__ == '__main__':
    unittest.main()
