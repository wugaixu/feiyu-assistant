import unittest
from app.upstream_observation import compact, READER


class UpstreamCompatibilityTests(unittest.TestCase):
    def test_observation_keeps_evidence_but_bounds_model_payload(self):
        state = {'visibleText': 'x' * 9000, 'verificationRequired': True,
                 'video': {'id': '123', 'time': 12, 'duration': 80},
                 'searchResults': [{'index': i, 'id': str(i), 'title': 'y' * 1000} for i in range(40)]}
        result = compact(state)
        self.assertEqual(len(result['text']), 4000)
        self.assertEqual(len(result['cards']), 20)
        self.assertEqual(len(result['cards'][0]['text']), 400)
        self.assertEqual(result['video']['time'], 12)
        self.assertTrue(result['verification_required'])

    def test_vendored_function_is_standalone_expression(self):
        self.assertTrue(READER.startswith('function readPageState()'))
        self.assertNotIn('export ', READER)
        self.assertNotIn('fetch(', READER)


if __name__ == '__main__':
    unittest.main()
