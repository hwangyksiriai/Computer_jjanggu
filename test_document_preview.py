import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
from PIL import Image

from document_preview import render_document_preview


class DocumentPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_pdf(self, name='payroll.pdf', encrypted=False):
        path = self.root / name
        with fitz.open() as document:
            for color in ((1, 0, 0), (0, 0, 1)):
                page = document.new_page(width=600, height=800)
                page.draw_rect(page.rect, fill=color, color=color)
                page.insert_text((30, 40), 'Synthetic payroll fixture', fontsize=20)
            options = {'encryption': fitz.PDF_ENCRYPT_AES_256,
                       'user_pw': 'test-secret', 'owner_pw': 'test-owner'} if encrypted else {}
            document.save(path, **options)
        return path

    def test_pdf_renders_actual_page_pixels_and_clamps_page_numbers(self):
        path = self.make_pdf()
        first = render_document_preview({'path': str(path)}, page=-3, max_size=(300, 400))
        last = render_document_preview({'path': str(path)}, page=99, max_size=(300, 400))
        self.assertEqual((first.page, first.page_count, first.error), (0, 2, ''))
        self.assertEqual((last.page, last.page_count, last.error), (1, 2, ''))
        self.assertEqual(first.image.size, (300, 400))
        self.assertEqual(first.image.getpixel((150, 200)), (255, 0, 0))
        self.assertEqual(last.image.getpixel((150, 200)), (0, 0, 255))

    def test_pdf_document_is_closed_and_file_is_not_modified(self):
        path = self.make_pdf()
        before = hashlib.sha256(path.read_bytes()).digest()
        document = fitz.open(path)
        with patch('fitz.open', return_value=document):
            result = render_document_preview({'path': path})
        self.assertTrue(document.is_closed)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
        path.unlink()
        self.assertEqual(result.image.getpixel((200, 200)), (255, 0, 0))

    def test_huge_requested_resolution_is_bounded(self):
        result = render_document_preview({'path': self.make_pdf()}, max_size=(100_000, 100_000))
        self.assertLessEqual(result.image.width, 1600)
        self.assertLessEqual(result.image.height, 2200)

    def test_rotated_pdf_fits_available_preview_shape(self):
        path = self.root / 'rotated.pdf'
        with fitz.open() as document:
            page = document.new_page(width=300, height=600)
            page.set_rotation(90)
            document.save(path)
        result = render_document_preview({'path': path}, max_size=(240, 400))
        self.assertEqual(result.image.size, (240, 120))

    def test_encrypted_pdf_has_short_password_fallback(self):
        result = render_document_preview({'path': self.make_pdf(encrypted=True)})
        self.assertIsNone(result.image)
        self.assertIn('암호', result.error)

    def test_deleted_corrupt_and_unsupported_files_have_distinct_fallbacks(self):
        missing = render_document_preview({'path': self.root / 'gone.pdf'})
        corrupt = self.root / 'broken.pdf'; corrupt.write_bytes(b'not a pdf')
        broken = render_document_preview({'path': corrupt})
        text = self.root / 'notes.txt'; text.write_text('Synthetic data', encoding='utf-8')
        unsupported = render_document_preview({'path': text})
        self.assertIn('이동되거나 삭제', missing.error)
        self.assertIn('PDF', broken.error)
        self.assertIn('원본을 열어', unsupported.error)
        self.assertTrue(all(item.image is None for item in (missing, broken, unsupported)))

    def test_jpeg_exif_orientation_and_detached_pixels(self):
        path = self.root / 'portrait.jpg'
        image = Image.new('RGB', (80, 40), 'green')
        exif = Image.Exif(); exif[274] = 6
        image.save(path, exif=exif)
        result = render_document_preview({'path': path}, max_size=(100, 100))
        self.assertEqual(result.image.size, (40, 80))
        self.assertEqual((result.page, result.page_count, result.error), (0, 1, ''))
        path.unlink()
        self.assertGreater(result.image.getpixel((10, 10))[1], 100)

    def test_transparent_image_has_white_background_and_is_bounded(self):
        path = self.root / 'scan.png'
        Image.new('RGBA', (1000, 600), (0, 0, 0, 0)).save(path)
        result = render_document_preview({'path': path}, max_size=(200, 200))
        self.assertEqual(result.image.size, (200, 120))
        self.assertEqual(result.image.getpixel((10, 10)), (255, 255, 255))

    def test_corrupt_image_and_stale_thumbnail_do_not_fake_a_document(self):
        path = self.root / 'broken.png'; path.write_bytes(b'not an image')
        thumbnail = self.root / 'unrelated.png'; Image.new('RGB', (10, 10), 'red').save(thumbnail)
        result = render_document_preview({'path': path, 'thumbnail': thumbnail})
        self.assertIsNone(result.image)
        self.assertIn('사진', result.error)

    def test_invalid_optional_dimensions_and_page_use_safe_defaults(self):
        result = render_document_preview({'path': self.make_pdf()}, page=None, max_size=None)
        self.assertEqual(result.page, 0)
        self.assertLessEqual(result.image.width, 800)
        self.assertLessEqual(result.image.height, 1100)


if __name__ == '__main__':
    unittest.main()
