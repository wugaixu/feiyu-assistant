import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app import voice_asset


class VoiceAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=voice_asset.ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(voice_asset, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def fixture(self, seconds=1):
        source = self.root / 'source.wav'
        with wave.open(str(source), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b'\x01\x00' * (16000 * seconds))
        return source

    def test_real_conversion_and_decode_do_not_claim_platform_compatibility(self):
        result = voice_asset.prepare(self.fixture())
        self.assertEqual(result['status'], 'prepared_offline')
        self.assertEqual(result['sample_rate'], 44100)
        self.assertTrue(Path(result['path']).is_file())
        self.assertIsNone(result['platform_resource_uri'])
        self.assertFalse(result['upload_verified'])
        self.assertFalse(result['sent'])

    def test_long_audio_is_rejected_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, 'duration budget'):
            voice_asset.prepare(self.fixture(2), max_seconds=1)
        self.assertFalse(list((self.root / 'outgoing-audio').glob('*/voice.m4a')))

    def test_invalid_audio_is_rejected(self):
        source = self.root / 'invalid.bin'
        source.write_bytes(b'not audio')
        with self.assertRaises(ValueError):
            voice_asset.prepare(source)

    def test_invalid_budget_is_rejected_before_conversion(self):
        with self.assertRaises(ValueError):
            voice_asset.prepare(self.fixture(), max_seconds=True)


if __name__ == '__main__':
    unittest.main()
