import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import event_dispatch as ed


class CodexLocateTests(unittest.TestCase):
    """codex.exe must resolve without a Codex-injected PATH."""

    def test_prefers_configured_exe(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'codex.exe'
            exe.write_text('x', encoding='utf-8')
            with patch.object(ed, 'target_config', return_value={'codex_exe': str(exe)}), \
                    patch.dict(os.environ, {'DOUYIN_CODEX_EXE': ''}, clear=False):
                self.assertEqual(ed.codex_executable(), str(exe))

    def test_falls_back_to_known_install_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            bin_dir = Path(folder) / 'OpenAI' / 'Codex' / 'bin' / 'abc'
            bin_dir.mkdir(parents=True)
            (bin_dir / 'codex.exe').write_text('x', encoding='utf-8')
            with patch.object(ed, 'target_config', return_value={}), \
                    patch.object(ed.shutil, 'which', return_value=None), \
                    patch.dict(os.environ, {'LOCALAPPDATA': folder, 'USERPROFILE': '',
                                            'DOUYIN_CODEX_EXE': ''}, clear=False):
                located = ed.codex_executable()
        self.assertIsNotNone(located)
        self.assertTrue(located.endswith('codex.exe'))

    def test_shell_launcher_still_rejected(self):
        from app.live_service import LiveStore
        store = LiveStore(':memory:')
        self.addCleanup(store.db.close)
        with store.db:
            store.db.execute('INSERT INTO live_tasks(id,owner,received,raw_json) VALUES(?,?,?,?)',
                             ('2' * 32, 'owner', 1, '{"kind":"text","raw_text":"x"}'))
        with patch.object(ed,'target_config',return_value={'provider':'codex'}), self.assertRaises(ValueError):
            ed.dispatch_one(store.db, executable='C:/codex.cmd')


if __name__ == '__main__':
    unittest.main()
