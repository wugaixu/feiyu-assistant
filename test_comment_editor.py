"""Isolated DOM regressions for the website's lazy-mounted comment editor."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from app.browser_publish import ROOT, _open_comment_editor


class CommentEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(dir=ROOT,prefix='comment-editor-test-')
        cls.env=patch.dict(os.environ,{'TEMP':cls.folder.name,'TMP':cls.folder.name})
        cls.env.start()
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch_persistent_context(
            str(Path(cls.folder.name)/'profile'),channel='msedge',headless=True,
            viewport={'width':1280,'height':900})

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop();cls.env.stop();cls.folder.cleanup()

    def setUp(self):
        self.page=self.browser.new_page();self.addCleanup(self.page.close)

    def test_lazy_editor_opens_after_comment_tab_and_placeholder(self):
        self.page.set_content('''<button style="position:absolute;left:1000px"
          onclick="document.querySelector('section').hidden=false">评论(12)</button>
          <section hidden><div class="comment-input-inner-container">
          <span onclick="this.outerHTML='<div class=public-DraftEditor-content contenteditable=true></div>'">留下你的精彩评论吧</span>
          </div></section>''')
        editor=_open_comment_editor(self.page)
        editor.fill('隔离预览，不发送')
        self.assertEqual(editor.inner_text(),'隔离预览，不发送')

    def test_visible_placeholder_does_not_toggle_tab_or_click_comment_decoy(self):
        self.page.set_content('''<button onclick="throw Error('do not toggle')">评论</button>
          <p onclick="window.decoy=true">留下你的精彩评论吧</p>
          <div class="comment-input-inner-container"><span
          onclick="this.outerHTML='<div class=public-DraftEditor-content contenteditable=true></div>'">留下你的精彩评论吧</span></div>''')
        self.assertTrue(_open_comment_editor(self.page).is_visible())
        self.assertIsNone(self.page.evaluate('window.decoy'))

    def test_existing_draft_is_returned_intact(self):
        self.page.set_content('<div class="public-DraftEditor-content" contenteditable="true">已有草稿</div>')
        self.assertEqual(_open_comment_editor(self.page).inner_text(),'已有草稿')

    def test_hidden_prerender_does_not_hide_late_interactive_tab(self):
        self.page.set_content('''<div data-e2e="comment-list" hidden>评论</div>
          <button hidden style="position:absolute;left:1000px"
          onclick="document.querySelector('section').hidden=false">评论(12)</button>
          <section hidden><div class="comment-input-inner-container"><span
          onclick="this.outerHTML='<div class=public-DraftEditor-content contenteditable=true></div>'">留下你的精彩评论吧</span></div></section>
          <script>setTimeout(()=>document.querySelector('button').hidden=false,300)</script>''')
        self.assertTrue(_open_comment_editor(self.page).is_visible())

    def test_multiple_visible_editors_are_refused(self):
        self.page.set_content('<div class="public-DraftEditor-content">A</div><div class="public-DraftEditor-content">B</div>')
        with self.assertRaisesRegex(ValueError,'Ambiguous'):_open_comment_editor(self.page)


if __name__=='__main__':unittest.main()
