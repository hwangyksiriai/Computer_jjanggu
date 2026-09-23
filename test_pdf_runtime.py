"""Native PDF callers share one guard, while slow OCR runs outside it."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fitz
import pymupdf

import core
from document_preview import render_document_preview
from knowledge import Knowledge
from pdf_runtime import PDF_LOCK


class PdfRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / 'synthetic-scan.pdf'
        with PDF_LOCK, pymupdf.open() as document:
            page = document.new_page(width=180, height=240)
            page.draw_rect(page.rect, fill=(1, 0, 0), color=(1, 0, 0))
            document.save(self.path)
        self.row = {'path': str(self.path), 'name': self.path.name, 'mtime': 123,
                    'size': self.path.stat().st_size, 'body': ''}

    def test_preview_thumbnail_and_indexing_share_guard_and_close_documents(self):
        library = Knowledge(self.directory / 'catalogue')
        original_open = pymupdf.open
        opened_documents = []
        native_guard_states = []
        ocr_guard_states = []
        start = threading.Barrier(3)

        def guarded_open(*args, **kwargs):
            # Test the boundary at the actual native API, including the fitz alias.
            held = PDF_LOCK._is_owned()
            native_guard_states.append(held)
            if not held:
                raise AssertionError('Native PDF API called without the shared guard')
            document = original_open(*args, **kwargs)
            opened_documents.append(document)
            return document

        def recognize(*args, **kwargs):
            ocr_guard_states.append(PDF_LOCK._is_owned())
            return SimpleNamespace(returncode=0, stdout='급여명세서 테스트 OCR'.encode('utf-8'))

        def together(operation):
            start.wait(timeout=10)
            return operation()

        with patch('pymupdf.open', side_effect=guarded_open), patch('fitz.open', side_effect=guarded_open), \
             patch('core.DATA', self.directory / 'state'), patch('core.subprocess.run', side_effect=recognize):
            with ThreadPoolExecutor(max_workers=3) as pool:
                preview = pool.submit(together, lambda: render_document_preview(self.row))
                thumbnail = pool.submit(together, lambda: library.thumbnail(self.row))
                extraction = pool.submit(together, lambda: core.extract(self.path))
                image = preview.result(timeout=15)
                thumbnail_path = thumbnail.result(timeout=15)
                text, status = extraction.result(timeout=15)
        self.assertIsNotNone(image.image, image.error)
        self.assertTrue(Path(thumbnail_path).is_file())
        self.assertIn('급여명세서 테스트 OCR', text, status)
        self.assertGreaterEqual(len(opened_documents), 3)
        self.assertTrue(all(native_guard_states))
        self.assertTrue(all(document.is_closed for document in opened_documents))
        self.assertTrue(ocr_guard_states)
        self.assertFalse(any(ocr_guard_states), 'OCR must not block document previews')
        self.assertEqual(list((self.directory / 'state/ocr-pages').glob('*.png')), [])

    def test_render_failure_closes_document_and_releases_guard_for_other_threads(self):
        with PDF_LOCK:
            document = pymupdf.open(self.path)
        with patch('fitz.open', return_value=document), \
             patch.object(document, 'load_page', side_effect=RuntimeError('synthetic render failure')):
            result = render_document_preview(self.row)
        self.assertIsNone(result.image)
        self.assertTrue(result.error)
        self.assertTrue(document.is_closed)

        def can_acquire():
            acquired = PDF_LOCK.acquire(timeout=2)
            if acquired:
                PDF_LOCK.release()
            return acquired

        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertTrue(pool.submit(can_acquire).result(timeout=3))


if __name__ == '__main__':
    unittest.main()
