import io,json,unittest,wave
from unittest.mock import patch
from app import whale_speech


class IndependentWhaleSpeechTests(unittest.TestCase):
    def wav(self,rate=16000):
        data=io.BytesIO()
        with wave.open(data,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes(b'\0\0'*rate)
        return data.getvalue()

    def test_uses_project_sensevoice_worker_without_dsh(self):
        result=type('Result',(),{'returncode':0,'stdout':json.dumps({'ok':True,'clips':[{'segments':[{'text':'你好'}]}]})})()
        with patch.object(whale_speech.subprocess,'run',return_value=result) as run:
            value=whale_speech.transcribe(self.wav())
        self.assertEqual(value['text'],'你好');self.assertEqual(value['durationMs'],1000)
        args=run.call_args.args[0];self.assertIn('sensevoice_worker.cjs',args[1])
        self.assertNotIn('DSH',json.dumps(args));self.assertNotIn('DSH',run.call_args.kwargs['input'])

    def test_invalid_audio_is_rejected_before_inference(self):
        with patch.object(whale_speech.subprocess,'run') as run:
            with self.assertRaises(ValueError):whale_speech.transcribe(b'not audio')
            with self.assertRaises(ValueError):whale_speech.transcribe(self.wav(8000))
            run.assert_not_called()

    def test_empty_transcription_remains_empty(self):
        result=type('Result',(),{'returncode':0,'stdout':json.dumps({'ok':True,'clips':[{'segments':[]}]})})()
        with patch.object(whale_speech.subprocess,'run',return_value=result):self.assertEqual(whale_speech.transcribe(self.wav())['text'],'')


if __name__=='__main__':unittest.main()
