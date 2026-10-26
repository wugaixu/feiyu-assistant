import unittest
from app import media_formats as formats


class MediaFormatContractTests(unittest.TestCase):
    def test_outbound_is_a_subset_of_inbound(self):
        for fmt in formats.OUTBOUND_IMAGE:
            self.assertIn(fmt, formats.INBOUND_IMAGE)
        self.assertEqual(set(formats.OUTBOUND_IMAGE), {'PNG', 'JPEG', 'GIF', 'WEBP'})
        self.assertEqual(set(formats.INBOUND_IMAGE), {'PNG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'TIFF'})

    def test_storage_suffix_matches_outbound_extensions(self):
        for fmt, suffix in formats.SUFFIX.items():
            self.assertIn(suffix, formats.OUTBOUND_EXTENSIONS)
            self.assertEqual(formats.save_suffix(fmt), suffix)
            self.assertEqual(formats.outbound_suffix(fmt), suffix)
        for fmt in ('BMP', 'TIFF'):
            self.assertEqual(formats.save_suffix(fmt), '.png')
            self.assertFalse(formats.stored_as_is(fmt))
            self.assertEqual(formats.outbound_suffix(fmt), '')
        self.assertEqual(formats.save_suffix('AVIF'), '')

    def test_extension_helpers(self):
        for suffix in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
            self.assertIn(suffix, formats.OUTBOUND_EXTENSIONS)
            self.assertIn(suffix, formats.INBOUND_EXTENSIONS)
        for suffix in ('.bmp', '.tif', '.tiff'):
            self.assertIn(suffix, formats.INBOUND_EXTENSIONS)
            self.assertNotIn(suffix, formats.OUTBOUND_EXTENSIONS)
        self.assertTrue(formats.outbound_extension_ok('x.GIF'))
        self.assertFalse(formats.outbound_extension_ok('x.bmp'))


if __name__ == '__main__':
    unittest.main()
