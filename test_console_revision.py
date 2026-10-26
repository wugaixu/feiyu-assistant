import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import console_server


class ConsoleRevisionTests(unittest.TestCase):
    def test_document_and_ping_use_same_content_revision(self):
        with tempfile.TemporaryDirectory(dir=console_server.ROOT) as folder:
            html = Path(folder) / 'console.html'
            html.write_text("const revision='__UI_REVISION__'", encoding='utf-8')
            with patch.object(console_server, 'HTML_FILE', html):
                page, revision = console_server.ui_document()
                self.assertIn(revision, page)
                self.assertNotIn('__UI_REVISION__', page)
                self.assertEqual(console_server.ui_document()[1], revision)
                html.write_text('changed __UI_REVISION__', encoding='utf-8')
                self.assertNotEqual(console_server.ui_document()[1], revision)


if __name__ == '__main__':
    unittest.main()
