import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import image_backend as backend
from app import local_images as images


TEMP = Path(r'__TEMP_PARENT__')


class ImageBackendIdleTests(unittest.TestCase):
    def check(self, queue, now=1601, latest=1000, last_busy=1000):
        with patch.object(images, 'api', return_value=queue), \
             patch.object(images, 'last_activity', return_value=latest), \
             patch.object(images, 'idle_minutes', return_value=10):
            return backend.idle_check(1000, last_busy, now)

    def test_running_and_pending_protect_real_work(self):
        for key in ('queue_running', 'queue_pending'):
            queue = {'queue_running': [], 'queue_pending': []}
            queue[key] = [['any job, from any frontend']]
            self.assertEqual(self.check(queue), (False, 1601))

    def test_unknown_queue_never_allows_exit(self):
        for queue in ({}, None, {'queue_running': [], 'queue_pending': None}):
            with self.assertRaises(ValueError):
                self.check(queue)

    def test_shared_activity_and_last_busy_extend_idle_window(self):
        empty = {'queue_running': [], 'queue_pending': []}
        self.assertEqual(self.check(empty), (True, 1000))
        self.assertEqual(self.check(empty, latest=1550), (False, 1000))
        self.assertEqual(self.check(empty, last_busy=1550), (False, 1550))

    def test_clean_idle_exit_can_wake_again_but_crash_is_not_retried(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory(dir=TEMP) as folder:
            root = Path(folder)
            (root / 'venv/Scripts').mkdir(parents=True)
            (root / 'venv/Scripts/pythonw.exe').touch()
            (root / 'ComfyUI').mkdir()
            (root / 'ComfyUI/main.py').touch()
            old = Mock(returncode=0)
            old.poll.return_value = 0
            new = Mock()
            with patch.object(images, 'BASE', root), \
                 patch.object(images, '_process', old), \
                 patch.object(images, '_log', None), \
                 patch.object(images, 'touch'), \
                 patch.object(images, 'api', side_effect=ConnectionError), \
                 patch.object(images.subprocess, 'Popen', return_value=new) as launch:
                self.assertFalse(images.ensure_backend())
                self.assertIn('app.image_backend', launch.call_args.args[0])
                images._log.close()
            old.returncode = 1
            old.poll.return_value = 1
            with patch.object(images, '_process', old), \
                 patch.object(images, 'touch'), \
                 patch.object(images, 'api', side_effect=ConnectionError), \
                 patch.object(images.subprocess, 'Popen') as launch:
                with self.assertRaises(RuntimeError):
                    images.ensure_backend()
                launch.assert_not_called()

    def test_detached_backend_exits_and_lifetime_lock_blocks_duplicate(self):
        # Real lightweight subprocesses, a fake ComfyUI and no production API,
        # model loading, task database, message sending or setting changes.
        with tempfile.TemporaryDirectory(dir=TEMP) as folder:
            root = Path(folder)
            (root / 'ComfyUI').mkdir()
            (root / 'ComfyUI/app').mkdir()
            (root / 'ComfyUI/app/__init__.py').write_text('', 'utf-8')
            (root / 'ComfyUI/app/governance.py').write_text('ready=True', 'utf-8')
            (root / 'ComfyUI/main.py').write_text(
                "from pathlib import Path\nfrom app import governance\nimport time\n"
                "assert governance.ready\n"
                "Path('ready').touch()\nwhile True: time.sleep(.1)\n", 'utf-8')
            queue = root / 'queue.json'
            queue.write_text(json.dumps({'queue_running': [1], 'queue_pending': []}))
            script = root / 'run.py'
            script.write_text(
                'import sys,json,functools\nfrom pathlib import Path\n'
                f'sys.path.insert(0,{str(Path(__file__).resolve().parent)!r})\n'
                'from app import image_backend as b\n'
                f'b.local_images.BASE=Path({str(root)!r})\n'
                'b.local_images.touch=lambda *a:None\n'
                'b.local_images.last_activity=lambda:None\n'
                'b.local_images.idle_minutes=lambda:.01\n'
                f'queue=Path({str(queue)!r})\n'
                'def api(path,**kw):\n'
                " if path=='/system_stats':raise ConnectionError()\n"
                ' return json.loads(queue.read_text())\n'
                'b.local_images.api=api\n'
                'b.watch_idle=functools.partial(b.watch_idle,interval=.05)\n'
                'b.main()\n', 'utf-8')
            process = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', str(script)],
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 8
                while not (root / 'ComfyUI/ready').exists():
                    if time.monotonic() > deadline:
                        self.fail('fake backend did not start')
                    time.sleep(.05)
                second = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(script)],
                                        timeout=5, capture_output=True)
                self.assertEqual(second.returncode, 0, second.stderr)
                time.sleep(.8)
                self.assertIsNone(process.poll(), 'busy backend was interrupted')
                queue.write_text(json.dumps({'queue_running': [], 'queue_pending': []}))
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertIn('backend-self-idle', (root / 'backend-idle-exit.log').read_text())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
