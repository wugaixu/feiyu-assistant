import tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from app import comment_login as login,comment_profile as profile


class FakeBrowser:
    def __init__(self,initial=None,later=None):
        self.initial=initial;self.later=later;self.shown=False;self.closed=False;self.page=Mock()
        self.page.is_closed.return_value=False
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def self_identity(self):
        if self.initial is None:raise ValueError('not logged in')
        return self.initial
    def read_identity(self):return self.later
    def show_for_login(self):self.shown=True


class LoginTests(unittest.TestCase):
    def test_valid_account_check_does_not_show_login_window(self):
        browser=FakeBrowser({'user_uid':login.ACCOUNT_UID,'nickname':'test'})
        login._run(threading.Event(),lambda:browser)
        self.assertEqual(login.status()['state'],'logged_in');self.assertFalse(browser.shown);self.assertTrue(browser.closed)

    def test_missing_login_shows_same_window_then_detects_user_login(self):
        browser=FakeBrowser(later={'user_uid':login.ACCOUNT_UID})
        stop=Mock();stop.wait.return_value=False
        login._run(stop,lambda:browser)
        self.assertTrue(browser.shown);self.assertTrue(browser.closed)
        self.assertEqual(login.status()['state'],'logged_in')

    def test_wrong_account_is_not_accepted_and_shutdown_closes_owned_window(self):
        browser=FakeBrowser({'user_uid':'wrong'})
        stop=Mock();stop.wait.return_value=True
        self.assertEqual(login.classify(browser.initial)['state'],'wrong_account')
        login._run(stop,lambda:browser)
        self.assertTrue(browser.shown);self.assertTrue(browser.closed)

    def test_busy_profile_never_opens_second_browser(self):
        browser=FakeBrowser()
        with patch.object(FakeBrowser,'__enter__',side_effect=profile.ProfileBusy('busy')):
            login._run(threading.Event(),lambda:browser)
        self.assertEqual(login.status()['state'],'busy');self.assertFalse(browser.shown)

    def test_repeated_click_rechecks_existing_worker(self):
        with patch.object(login,'_thread') as thread:
            thread.is_alive.return_value=True
            login._recheck.clear();login.start(threading.Event())
            self.assertTrue(login._recheck.is_set())
        login._recheck.clear()

    def test_profile_lease_is_exclusive_and_released(self):
        with tempfile.TemporaryDirectory(dir=profile.ROOT) as folder,patch.object(profile,'ROOT',Path(folder)):
            with profile.ProfileLease():
                with self.assertRaises(profile.ProfileBusy):
                    with profile.ProfileLease():pass
            with profile.ProfileLease():pass


if __name__=='__main__':unittest.main()
