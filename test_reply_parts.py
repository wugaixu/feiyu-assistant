import unittest
from unittest.mock import patch
from app import reply_parts
from app.reply_limits import MAX_PARTS, MAX_TEXT, MAX_TOTAL_TEXT


class ReplyPartsTests(unittest.TestCase):
    def test_blank_lines_split_and_single_newlines_stay(self):
        parts = reply_parts.paragraphs('第一段\n第二行\n\n第二段\n\n\n第三段')
        self.assertEqual(parts, ['第一段\n第二行', '第二段', '第三段'])

    def test_code_fence_protects_blank_lines(self):
        text = '看代码：\n\n```\na = 1\n\nb = 2\n```\n\n结束'
        parts = reply_parts.paragraphs(text)
        self.assertEqual(len(parts), 3)
        self.assertIn('a = 1\n\nb = 2', parts[1])

    def test_long_paragraph_is_split_without_loss(self):
        warnings = []
        parts = reply_parts.text_parts('x' * (MAX_TEXT * 2 + 5), warnings.append)
        self.assertEqual([len(p['text']) for p in parts], [MAX_TEXT, MAX_TEXT, 5])
        self.assertTrue(warnings)
        self.assertEqual(''.join(p['text'] for p in parts), 'x' * (MAX_TEXT * 2 + 5))

    def test_finish_parts_respects_limits(self):
        paragraphs = '\n\n'.join(['段%d' % index for index in range(MAX_PARTS + 5)])
        warnings = []
        parts = reply_parts.finish_parts(paragraphs, warnings.append)
        self.assertEqual(len(parts), MAX_PARTS)
        self.assertTrue(any('段数' in warning for warning in warnings))

    def test_finish_parts_caps_total_text(self):
        block = 'y' * 1000
        warnings = []
        parts = reply_parts.finish_parts('\n\n'.join([block] * 10), warnings.append)
        self.assertLessEqual(sum(len(p['text']) for p in parts), MAX_TOTAL_TEXT)
        self.assertTrue(any('6000' in warning or '超过' in warning for warning in warnings))

    def test_empty_reply_yields_no_parts(self):
        self.assertEqual(reply_parts.paragraphs('  \n\n  '), [])
        self.assertEqual(reply_parts.finish_parts('   '), [])

    def test_leading_process_announcement_is_dropped(self):
        with patch.object(reply_parts, 'narration_filter_enabled', return_value=True):
            parts = reply_parts.drop_process_narration(
                reply_parts.finish_parts('我先按流程核对任务、读资料包。\n\n真正的回复在这里。'))
            self.assertEqual([p['text'] for p in parts], ['真正的回复在这里。'])
            parts = reply_parts.drop_process_narration(
                reply_parts.finish_parts('我先核对任务并读资料。\n\n看海这个我答应得比谁都快。'))
            self.assertEqual(len(parts), 1)

    def test_normal_first_paragraph_is_kept(self):
        with patch.object(reply_parts, 'narration_filter_enabled', return_value=True):
            for text in ('看海这个我答应得比谁都快。\n\n那就走吧。',
                         '我先说结论：这件事不行。\n\n理由有两个。'):
                parts = reply_parts.drop_process_narration(reply_parts.finish_parts(text))
                self.assertEqual(len(parts), 2, text)

    def test_single_narration_is_not_removed_into_nothing(self):
        with patch.object(reply_parts, 'narration_filter_enabled', return_value=True):
            parts = reply_parts.drop_process_narration(reply_parts.finish_parts('我先核对任务并读资料。'))
            self.assertEqual([p['text'] for p in parts], ['我先核对任务并读资料。'])

    def test_filter_is_off_by_default(self):
        parts = reply_parts.drop_process_narration(
            reply_parts.finish_parts('我先核对任务并读资料。\n\n真正的回复在这里。'))
        self.assertEqual(len(parts), 2)


if __name__ == '__main__':
    unittest.main()
