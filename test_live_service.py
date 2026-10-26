import tempfile
import unittest
from unittest.mock import MagicMock,patch
from pathlib import Path
from app.live_service import LiveStore, prefix_additions, follow_latest, retry_decision


def msg(text, direction='unverified'):
    return {'kind': 'text', 'raw_text': text, 'direction': direction}


class LiveTests(unittest.TestCase):
    def setUp(self):
        policy=patch('app.live_service.dm_policy',return_value={'acknowledgements':True})
        policy.start();self.addCleanup(policy.stop)
        root = Path(r'__RUNTIME_ROOT__\tests')
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.path = Path(self.temp.name) / 'live.sqlite3'
        self.store = LiveStore(self.path)
        self.base = [msg(x) for x in ('old1', 'old2', 'old3', 'old4')]

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_baseline_and_duplicates_and_reopen(self):
        self.assertEqual(self.store.observe('owner', self.base)['accepted'], 0)
        new = [msg('hello')] + self.base
        self.assertEqual(self.store.observe('owner', new)['accepted'], 1)
        self.assertEqual(self.store.observe('owner', new)['accepted'], 0)
        self.store.db.close()
        self.store = LiveStore(self.path)
        self.assertEqual(self.store.observe('owner', new)['accepted'], 0)
        self.assertEqual(self.store.observe('owner', [msg('hello')] + new)['accepted'], 1)
        self.assertEqual(self.store.db.execute('select count(*) from reply_outbox').fetchone()[0], 2)

    def test_outgoing_history_and_reorder(self):
        self.store.observe('owner', self.base)
        self.assertEqual(self.store.observe('owner', [msg('reply', 'outgoing')] + self.base)['accepted'], 0)
        self.assertEqual(self.store.observe('owner', list(reversed(self.base)))['state'], 'ambiguous_sequence')
        self.assertEqual(self.store.db.execute('select count(*) from live_tasks').fetchone()[0], 0)

    def test_virtual_window_and_no_overlap(self):
        self.assertEqual(prefix_additions(['a','b','c','d'], ['n','a','b','c']), 1)
        self.assertEqual(prefix_additions(['a','b','c','d'], ['a','b','c']), 0)
        self.assertIsNone(prefix_additions(['a','b','c'], ['x','y','z']))

    def test_follow_unread_before_reading_virtual_history(self):
        page = MagicMock()
        unread = page.locator.return_value.get_by_text.return_value
        unread.count.return_value = 1
        unread.is_visible.return_value = True
        self.assertTrue(follow_latest(page))
        unread.click.assert_called_once_with(timeout=3000, no_wait_after=True)
        unread.reset_mock()
        unread.count.return_value = 2
        self.assertFalse(follow_latest(page))
        unread.click.assert_not_called()

    def test_modal_url_change_does_not_block_new_message(self):
        base = [dict(m, links=[{'text':'https://example.com/video','href':'https://www.douyin.com/?modal_id=1#'}]) for m in self.base]
        self.store.observe('owner', base)
        changed = [dict(m, links=[{'text':'https://example.com/video','href':'https://www.douyin.com/?modal_id=2#'}]) for m in base]
        self.assertEqual(self.store.observe('owner', [msg('new')] + changed)['accepted'], 1)
        self.assertEqual(self.store.observe('owner', [msg('new')] + changed)['accepted'], 0)


class RetryPolicyTests(unittest.TestCase):
    """A confirmed rejection may retry once; an uncertain send never does."""

    POLICY = {'retry_rejected': True, 'retry_max': 1, 'retry_delay_seconds': 180}

    def test_explicit_rejection_gets_one_bounded_delayed_retry(self):
        first = retry_decision(0, 'platform_not_accepted', self.POLICY, now=1000)
        self.assertTrue(first['retry'])
        self.assertEqual(first['attempts'], 1)
        self.assertEqual(first['not_before'], 1180)
        second = retry_decision(first['attempts'], 'platform_not_accepted', self.POLICY, now=2000)
        self.assertFalse(second['retry'])
        self.assertEqual(second['attempts'], 1)

    def test_uncertain_outcome_is_never_retried(self):
        for attempts in (0, 1, 5):
            self.assertFalse(retry_decision(attempts, 'outcome_uncertain', self.POLICY, now=0)['retry'])

    def test_delivered_message_is_terminal(self):
        self.assertFalse(retry_decision(0, 'server_accepted', self.POLICY, now=0)['retry'])

    def test_retry_can_be_disabled_by_policy(self):
        self.assertFalse(retry_decision(0, 'platform_not_accepted', {}, now=0)['retry'])
        self.assertFalse(retry_decision(0, 'platform_not_accepted',
                                        {'retry_rejected': False}, now=0)['retry'])


if __name__ == '__main__':
    unittest.main()
