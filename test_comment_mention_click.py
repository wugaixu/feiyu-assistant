import unittest
from unittest.mock import Mock
from playwright.sync_api import TimeoutError
from app.browser_publish import select_verified_owner,verified_owner_candidate,OWNER_UID,OWNER_SEC_UID


class MentionClickTests(unittest.TestCase):
    def page_candidates(self,links,avatars):
        page=Mock();rows=page.locator.return_value
        link_filter=Mock();avatar_filter=Mock();rows.filter.side_effect=[link_filter,avatar_filter]
        link_filter.filter.return_value.count.return_value=links
        avatar_filter.filter.return_value.count.return_value=avatars
        return page

    def owner(self):return {'uid':OWNER_UID,'sec_uid':OWNER_SEC_UID,'nickname':'主人','avatar':'https://example.com/key.jpeg'}

    def test_account_link_preferred_over_avatar(self):
        page=self.page_candidates(1,1)
        self.assertEqual(verified_owner_candidate(page,self.owner())[1],'account_link')

    def test_unique_avatar_requires_exact_name_and_verified_account(self):
        page=self.page_candidates(0,1)
        self.assertEqual(verified_owner_candidate(page,self.owner())[1],'verified_avatar_and_exact_name')
        page=self.page_candidates(0,2)
        with self.assertRaises(ValueError):verified_owner_candidate(page,self.owner())

    def test_unverified_uid_is_rejected_before_dom_actions(self):
        page=Mock();owner=self.owner();owner['uid']='wrong'
        with self.assertRaises(ValueError):verified_owner_candidate(page,owner)
        page.locator.assert_not_called()

    def test_timeout_after_entity_creation_does_not_click_twice(self):
        candidate=Mock();candidate.click.side_effect=TimeoutError('detached')
        editor=Mock();words=editor.locator.return_value
        words.count.return_value=1;words.inner_text.return_value='@主人'
        self.assertTrue(select_verified_owner(candidate,editor,'主人'))
        candidate.click.assert_called_once()

    def test_plain_text_or_wrong_entity_never_qualifies_as_verified(self):
        for count,text in [(0,'@主人'),(1,'@其他人'),(2,'@主人')]:
            candidate=Mock();candidate.click.side_effect=TimeoutError('detached')
            editor=Mock();editor.locator.return_value.count.return_value=count
            editor.locator.return_value.inner_text.return_value=text
            with self.assertRaises(TimeoutError):select_verified_owner(candidate,editor,'主人')
            candidate.click.assert_called_once()

    def test_verified_visible_hit_uses_one_pointer_click_and_waits_for_entity(self):
        candidate=Mock();candidate.evaluate.return_value={'ok':True,'x':100,'y':200}
        page=Mock();editor=Mock();words=editor.locator.return_value
        words.count.side_effect=[0,1];words.inner_text.return_value='@主人'
        self.assertFalse(select_verified_owner(candidate,editor,'主人',page=page))
        candidate.click.assert_not_called();page.mouse.click.assert_called_once_with(100,200)
        page.wait_for_timeout.assert_called_once_with(100)

    def test_obscured_target_never_clicks(self):
        candidate=Mock();candidate.evaluate.return_value={'ok':False,'x':100,'y':200}
        page=Mock();editor=Mock()
        with self.assertRaises(ValueError):select_verified_owner(candidate,editor,'主人',page=page)
        candidate.click.assert_not_called();page.mouse.click.assert_not_called()

    def test_click_without_entity_never_retries(self):
        candidate=Mock();candidate.evaluate.return_value={'ok':True,'x':100,'y':200}
        page=Mock();editor=Mock();editor.locator.return_value.count.return_value=0
        from unittest.mock import patch
        with patch('app.browser_publish.time.monotonic',side_effect=[0,0,4]):
            with self.assertRaises(ValueError):select_verified_owner(candidate,editor,'主人',page=page)
        page.mouse.click.assert_called_once();candidate.click.assert_not_called()

    def test_detached_candidate_is_resolved_again_before_any_input(self):
        from playwright.sync_api import Error
        candidate=Mock();candidate.evaluate.side_effect=[Error('Element is not attached to the DOM'),{'ok':True,'x':10,'y':20}]
        page=Mock();editor=Mock();editor.locator.return_value.count.return_value=1;editor.locator.return_value.inner_text.return_value='@主人'
        select_verified_owner(candidate,editor,'主人',page=page)
        page.mouse.click.assert_called_once_with(10,20);candidate.scroll_into_view_if_needed.assert_not_called()

    def test_clipped_owner_is_scrolled_then_hit_tested_again_before_click(self):
        candidate=Mock();candidate.evaluate.side_effect=[{'ok':False,'x':10,'y':1200},None,{'ok':True,'x':10,'y':200}]
        page=Mock();editor=Mock();editor.locator.return_value.count.return_value=1;editor.locator.return_value.inner_text.return_value='@主人'
        select_verified_owner(candidate,editor,'主人',page=page)
        page.mouse.click.assert_called_once_with(10,200)
        page.wait_for_timeout.assert_called_once_with(150)


if __name__=='__main__':unittest.main()
