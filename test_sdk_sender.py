import unittest

from app.sdk_sender import classify


class SdkSenderTests(unittest.TestCase):
    """A server verdict may be retried; no verdict never is."""

    def test_accepted_reply(self):
        self.assertEqual(classify({'ok': True, 'statusCode': 0, 'serverMessageId': '7699'}), 'server_accepted')

    def test_explicit_rejection_is_confirmed_non_delivery(self):
        self.assertEqual(classify({'ok': False, 'statusCode': 0, 'checkCode': 8610}), 'platform_not_accepted')
        self.assertEqual(classify({'ok': False, 'response': {'status_code': 8101}}), 'outcome_uncertain')

    def test_missing_verdict_is_uncertain(self):
        self.assertEqual(classify({'ok': False, 'error': 'owner conversation not found'}), 'outcome_uncertain')
        self.assertEqual(classify(None), 'outcome_uncertain')
        self.assertEqual(classify({'ok': False, 'statusCode': None, 'checkCode': None}), 'outcome_uncertain')

    def test_check_code_overrides_transport_ok_without_assuming_rejection(self):
        for check in (8101,'8101',10502,8611,'malformed'):
            self.assertEqual(classify({'ok':True,'statusCode':0,'checkCode':check,'serverMessageId':'7699'}),'outcome_uncertain')

    def test_explicit_rejection_overrides_ok(self):
        self.assertEqual(classify({'ok':True,'statusCode':0,'checkCode':8610,'serverMessageId':'7699'}),'platform_not_accepted')
        self.assertEqual(classify({'ok':True,'statusCode':7,'serverMessageId':'7699'}),'platform_not_accepted')

    def test_missing_server_message_id_not_accepted(self):
        for identity in (None,'0','invalid'):
            self.assertEqual(classify({'ok':True,'statusCode':0,'serverMessageId':identity}),'outcome_uncertain')

    def test_zero_check_code_allows_submission_acknowledgement(self):
        self.assertEqual(classify({'ok':True,'statusCode':0,'checkCode':0,'serverMessageId':'7699'}),'server_accepted')


if __name__ == '__main__':
    unittest.main()
