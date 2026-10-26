import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import voice_capabilities as voice
from app.live_service import ROOT


class VoiceReadinessTests(unittest.TestCase):
    def _installed(self, folder, disabled=(), reference='configured.wav'):
        data=Path(folder)/'astrbot/runtime/data'
        (data/'plugins/astrbot_plugin_GPT_SoVITS').mkdir(parents=True)
        (data/'plugins/astrbot_plugin_GPT_SoVITS/main.py').touch()
        (data/'config').mkdir()
        (data/'config/astrbot_plugin_GPT_SoVITS_config.json').write_text(json.dumps({'enabled':True,'client':{'base_url':'http://127.0.0.1:9880'},'default_params':{'ref_audio_path':reference}}),encoding='utf-8-sig')
        if disabled:
            con=sqlite3.connect(data/'data_v4.db')
            try:
                con.execute('create table preferences (scope text, key text, value text)')
                con.execute('insert into preferences values (?,?,?)',('global','inactivated_plugins',json.dumps({'val':list(disabled)})))
                con.commit()
            finally:con.close()
        return data

    def test_installed_plugin_without_service_is_not_claimed_as_usable(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            self._installed(folder)
            with patch.object(voice,'ROOT',Path(folder)),patch.object(voice,'local_service',return_value='offline'):
                result=voice.state(cached=False)
            self.assertTrue(result['gsv_installed'])
            self.assertFalse(result['gsv_available'])
            # The service is on demand here: tools stay usable, but readiness
            # must still be reported honestly.
            self.assertTrue(voice.speech_available(result))
            self.assertIn('按需启动',voice.instructions(result))
            with patch.object(voice,'ROOT',Path(folder)),patch.object(voice,'local_service',return_value='reachable'):
                result=voice.state(cached=False)
            self.assertTrue(result['gsv_available'])
            self.assertIn('优先',voice.instructions(result))
        voice._cache=None

    def test_unconfigured_reference_keeps_the_offline_wording(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            self._installed(folder,reference='')
            with patch.object(voice,'ROOT',Path(folder)),patch.object(voice,'local_service',return_value='offline'):
                result=voice.state(cached=False)
            self.assertFalse(result['gsv_reference_configured'])
            self.assertFalse(voice.speech_available(result))
            self.assertIn('不偷偷替换',voice.instructions(result))
        voice._cache=None

    def test_plugin_disabled_in_astrbot_is_never_reported_available(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            self._installed(folder,disabled=['data.plugins.astrbot_plugin_GPT_SoVITS.main','data.plugins.astrbot_plugin_matsuko_cover.main'])
            with patch.object(voice,'ROOT',Path(folder)),patch.object(voice,'local_service',return_value='reachable'):
                result=voice.state(cached=False)
            self.assertTrue(result['gsv_plugin_disabled'])
            self.assertFalse(result['gsv_available'])
            self.assertFalse(result['gsv_enabled'])
            self.assertIn('停用',voice.instructions(result))
        voice._cache=None

    def test_missing_or_broken_state_database_keeps_plugin_enabled(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            data=self._installed(folder)
            (data/'data_v4.db').write_bytes(b'not a database')
            with patch.object(voice,'ROOT',Path(folder)),patch.object(voice,'local_service',return_value='reachable'):
                result=voice.state(cached=False)
            self.assertFalse(result['gsv_plugin_disabled'])
            self.assertTrue(result['gsv_available'])
        voice._cache=None

    def test_voice_label_is_display_only_and_tolerates_missing_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            (Path(folder)/'gpt-sovits.json').write_text(json.dumps({'voice':'小叽 / Chii'}),encoding='utf-8')
            with patch.object(voice,'ROOT',Path(folder)):
                self.assertEqual(voice.voice_name(),'小叽 / Chii')
                self._installed(folder)
                with patch.object(voice,'local_service',return_value='reachable'):
                    self.assertEqual(voice.state(cached=False)['gsv_voice'],'小叽 / Chii')
            (Path(folder)/'gpt-sovits.json').write_text('{broken',encoding='utf-8')
            with patch.object(voice,'ROOT',Path(folder)):self.assertEqual(voice.voice_name(),'')
        voice._cache=None

    def test_probe_is_loopback_only_and_rejects_credentialed_urls(self):
        with patch.object(voice.socket,'create_connection',side_effect=AssertionError('No network')):
            self.assertEqual(voice.local_service('https://voice.example/'),'remote_configured')
            self.assertEqual(voice.local_service('http://user:secret@localhost:9880/'),'invalid')
            self.assertEqual(voice.local_service('file:///private'),'invalid')


if __name__=='__main__':unittest.main()
