import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app import gpt_sovits as g


class GPTSoVITSTests(unittest.TestCase):
    def test_disabled_service_never_starts(self):
        spawn=Mock()
        with patch.object(g,'settings',return_value={'enabled':False}):self.assertFalse(g.ensure(spawn))
        spawn.assert_not_called()

    def test_online_service_is_reused(self):
        spawn=Mock()
        with patch.object(g,'settings',return_value={'enabled':True}),patch.object(g.socket,'create_connection'):
            self.assertFalse(g.ensure(spawn))
        spawn.assert_not_called()

    def _engine(self,folder):
        root=Path(folder)
        (root/'venv/Scripts').mkdir(parents=True);(root/'venv/Scripts/python.exe').touch()
        (root/'app/voices/chii').mkdir(parents=True)
        for name in ('chii-e10.ckpt','chii_e10_s1210.pth','ref_audio.wav','ref_text.txt'):
            (root/'app/voices/chii'/name).touch()
        return root

    def test_service_starts_only_with_fresh_demand(self):
        with tempfile.TemporaryDirectory(dir=g.ROOT) as folder:
            root=self._engine(folder);wake=root/'wake.json';spawn=Mock()
            with patch.object(g,'ENGINE',root),patch.object(g,'WAKE',wake),patch.object(g,'_launch_at',0),\
                 patch.object(g,'settings',return_value={'enabled':True,'device':'cpu','idle_minutes':5}),\
                 patch.object(g,'ROOT',root),\
                 patch.object(g.socket,'create_connection',side_effect=OSError):
                self.assertFalse(g.ensure(spawn));spawn.assert_not_called()
                self.assertTrue(g.wake('unit'))
                self.assertTrue(g.ensure(spawn));spawn.assert_called_once()
                self.assertFalse(wake.exists())

    def test_expired_or_missing_demand_does_not_start(self):
        with tempfile.TemporaryDirectory(dir=g.ROOT) as folder:
            root=self._engine(folder);wake=root/'wake.json';spawn=Mock()
            with patch.object(g,'ENGINE',root),patch.object(g,'WAKE',wake),patch.object(g,'_launch_at',0),\
                 patch.object(g,'settings',return_value={'enabled':True,'device':'cpu','idle_minutes':5}),\
                 patch.object(g.socket,'create_connection',side_effect=OSError):
                self.assertFalse(g.wake_due())
                wake.write_text(json.dumps({'at':time.time()-g.WAKE_SECONDS-1}),encoding='utf-8')
                self.assertFalse(g.wake_due())
                self.assertFalse(g.ensure(spawn));spawn.assert_not_called()
                wake.write_text('{broken',encoding='utf-8')
                self.assertFalse(g.wake_due())
                self.assertFalse(g.ensure(spawn));spawn.assert_not_called()

    def test_idle_minutes_is_bounded(self):
        with tempfile.TemporaryDirectory(dir=g.ROOT) as folder,patch.object(g,'ROOT',Path(folder)):
            for value,expected in [(5,5),(0,5),(61,5),('5',5),(True,5),(30,30),(-1,5)]:
                (Path(folder)/'gpt-sovits.json').write_text(json.dumps({'enabled':True,'idle_minutes':value}),encoding='utf-8')
                self.assertEqual(g.settings()['idle_minutes'],expected)

    def test_tts_uses_fixed_reference_and_languages_without_shell(self):
        with tempfile.TemporaryDirectory(dir=g.ROOT) as folder,patch.object(g,'ENGINE',Path(folder)):
            p=Path(folder)/'app/voices/chii';p.mkdir(parents=True);(p/'ref_text.txt').write_text('日文参考',encoding='utf-8')
            params=g.tts_params(' 中文内容 ','chii')
            self.assertEqual(params['text'],'中文内容');self.assertEqual(params['prompt_lang'],'ja')
            self.assertEqual(params['text_lang'],'zh');self.assertFalse(params['streaming_mode'])
            self.assertEqual(Path(params['ref_audio_path']),p/'ref_audio.wav')
            for bad in ['',None,'x'*401]:
                with self.assertRaises(ValueError):g.tts_params(bad)

    def test_dafeiyu_has_chinese_reference_and_nonstreaming_message_parameters(self):
        with tempfile.TemporaryDirectory(dir=g.ROOT) as folder,patch.object(g,'ENGINE',Path(folder)):
            p=Path(folder)/'app/voices/dafeiyu';p.mkdir(parents=True)
            (p/'ref_text.txt').write_text('中文参考',encoding='utf-8')
            params=g.tts_params('你好呀，今天过得怎么样？','dafeiyu')
            self.assertEqual(params['prompt_lang'],'zh');self.assertEqual(params['temperature'],.7)
            self.assertEqual(params['top_p'],.9);self.assertEqual(params['speed_factor'],1.0)
            self.assertFalse(params['streaming_mode']);self.assertEqual(params['media_type'],'wav')
            self.assertEqual(Path(g.voice_profile('dafeiyu')['gpt']).name,'s1v3.ckpt')

    def test_voice_selection_rejects_unknown_paths_and_bad_configuration(self):
        for bad in ['../chii','anything',[],False]:
            with self.assertRaises(ValueError):g.voice_profile(bad)
        with tempfile.TemporaryDirectory(dir=g.ROOT) as folder,patch.object(g,'ROOT',Path(folder)):
            (Path(folder)/'gpt-sovits.json').write_text(json.dumps({'voice_id':'dafeiyu'}),encoding='utf-8')
            self.assertEqual(g.voice_profile()['id'],'dafeiyu')

    def test_unknown_device_cannot_change_process_arguments(self):
        with tempfile.TemporaryDirectory(dir=g.ROOT) as folder,patch.object(g,'ROOT',Path(folder)):
            (Path(folder)/'gpt-sovits.json').write_text(json.dumps({'enabled':True,'device':'cuda;anything'}))
            self.assertEqual(g.settings(),{'enabled':True,'device':'cpu','idle_minutes':5})

    def test_only_whale_private_workflow_can_auto_speak(self):
        from app.voice_capabilities import text_only_workflow
        for text in ['公开作品简评任务。只输出JSON','这是主人授权的主动联系决策检查','这是已核验主人的私聊。本轮消息来源：抖音私聊']:
            self.assertTrue(text_only_workflow(text))
        self.assertFalse(text_only_workflow('这是已核验主人的私聊。\n[鲸聊语音许可:token]'))
        self.assertFalse(text_only_workflow('普通AstrBot手动聊天'))


if __name__=='__main__':unittest.main()
