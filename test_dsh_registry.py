import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from app import dsh_registry as reg
from app.live_service import ROOT

PRESENT = ('- id: ui-chat\n'
           '  config:\n'
           '    transcriptView: standard\n'
           '- insert:\n'
           '    - id: douyin-local-session-bridge\n'
           '      name: "__PROJECT_URI__/dsh_bridge/index.mjs"\n')
OTHER = ('- id: ui-settings\n'
         '  config:\n'
         '    enabled: false\n')


class DSHRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.patch_file = self.root / 'cordis.patch.yml'
        self.log = self.root / 'dsh-registry.log'
        reg._checked = 0

    def tearDown(self):
        reg._checked = 0
        self.temp.cleanup()

    def ensure(self):
        with patch.object(reg, 'PATCH', self.patch_file), patch.object(reg, 'LOG', self.log):
            return reg.ensure()

    def test_present_registration_is_left_alone(self):
        self.patch_file.write_text(PRESENT, encoding='utf-8')
        result = self.ensure()
        self.assertEqual(result['state'], 'present')
        self.assertFalse(result['changed'])
        self.assertEqual(self.patch_file.read_text(encoding='utf-8'), PRESENT)

    def test_reset_file_is_restored_with_backup(self):
        self.patch_file.write_text('# comment\n[]\n', encoding='utf-8')
        result = self.ensure()
        self.assertTrue(result['changed'])
        self.assertEqual(result['state'], 'restored')
        text = self.patch_file.read_text(encoding='utf-8')
        self.assertIn('douyin-local-session-bridge', text)
        self.assertIn('# comment', text)
        self.assertEqual(text.count('douyin-local-session-bridge'), 1)
        self.assertTrue(Path(result['backup']).is_file())
        self.assertIn('restored', self.log.read_text(encoding='utf-8'))

    def test_empty_file_is_restored(self):
        self.patch_file.write_text('', encoding='utf-8')
        result = self.ensure()
        self.assertTrue(result['changed'])
        self.assertIn(reg.PLUGIN_ID, self.patch_file.read_text(encoding='utf-8'))

    def test_other_entries_are_kept_and_file_stays_valid_yaml(self):
        self.patch_file.write_text(OTHER, encoding='utf-8')
        result = self.ensure()
        self.assertTrue(result['changed'])
        text = self.patch_file.read_text(encoding='utf-8')
        self.assertIn('ui-settings', text)
        self.assertIn('douyin-local-session-bridge', text)
        import yaml
        self.assertIsInstance(yaml.safe_load(text), list)

    def test_second_check_is_skipped_within_window(self):
        self.patch_file.write_text('[]', encoding='utf-8')
        self.assertTrue(self.ensure()['changed'])
        second = self.ensure()
        self.assertEqual(second['state'], 'skipped')
        self.assertFalse(second['changed'])

    def test_missing_directory_is_not_created(self):
        with patch.object(reg, 'PATCH', self.root / 'missing' / 'cordis.patch.yml'):
            result = reg.inspect()
        self.assertEqual(result['state'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
