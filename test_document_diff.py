from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import fitz
from PIL import Image
import document_diff as diff
from pdf_runtime import PDF_LOCK


class DocumentDiffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='document-diff-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def text(self, name, value, encoding='utf-8'):
        path = self.base / name
        path.write_text(value, encoding=encoding)
        return {'path': str(path)}

    def pdf(self, name, texts):
        path = self.base / name
        with PDF_LOCK, fitz.open() as document:
            for text in texts:
                document.new_page().insert_text((40, 60), text)
            document.save(path)
        return {'path': str(path)}

    def docx(self, name, texts):
        path = self.base / name
        body = ''.join(f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>' for text in texts)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        return {'path': str(path)}

    def test_replacements_additions_and_deletions_are_aligned_without_writing_originals(self):
        left = self.text('left.txt', 'Agreement\nOld amount 10\nDeleted item\nEnd')
        right = self.text('right.txt', 'Agreement\nNew amount 20\nEnd\nNew item')
        originals = {row['path']: Path(row['path']).read_bytes() for row in (left, right)}
        result = diff.compare_documents(left, right)
        self.assertEqual(result.state, 'ready')
        self.assertGreater(result.added, 0)
        self.assertGreater(result.removed, 0)
        self.assertTrue(any(row['left'] == 'Old amount 10' and row['kind'] != 'context' for row in result.rows))
        self.assertTrue(any(row['right'] == 'New item' for row in result.rows))
        self.assertEqual({path: Path(path).read_bytes() for path in originals}, originals)

    def test_unicode_and_whitespace_normalization_keeps_case_numbers_and_punctuation_meaningful(self):
        left = self.text('left.txt', 'A  10\n가')
        right = self.text('right.txt', 'A\t10\n가')
        self.assertEqual(diff.compare_documents(left, right).rows, [])
        right = self.text('right.txt', 'a 11!\n가')
        self.assertTrue(diff.compare_documents(left, right).rows)

    def test_equal_text_never_claims_equal_formatting_or_binary_files(self):
        left = self.text('left.txt', 'Equal content')
        right = self.text('right.txt', 'Equal content')
        result = diff.compare_documents(left, right)
        self.assertIn('읽은 글자', result.summary)
        self.assertIn('서식', result.summary)
        self.assertNotIn('두 파일이 같', result.summary)

    def test_unsupported_images_cannot_use_indexed_ocr_snippets_to_claim_no_changes(self):
        path = self.base / 'scan.png'
        Image.new('RGB', (30, 30), 'white').save(path)
        result = diff.compare_documents({'path': str(path), 'body': 'same OCR'}, {'path': str(path), 'body': 'same OCR'})
        self.assertEqual(result.state, 'unavailable')
        self.assertIn('판단할 수 없', result.summary)

    def test_missing_and_empty_documents_are_explicitly_unavailable(self):
        empty = self.text('empty.txt', '')
        for other in (empty, {'path': str(self.base / 'missing.txt')}):
            self.assertEqual(diff.compare_documents(empty, other).state, 'unavailable')

    def test_scanned_pdf_without_text_does_not_claim_equality(self):
        row = self.pdf('scan.pdf', [''])
        result = diff.compare_documents(row, row)
        self.assertEqual(result.state, 'unavailable')
        self.assertTrue(any('스캔' in note for note in result.notes))

    def test_pdf_mixed_readable_and_empty_pages_explains_partial_coverage(self):
        row = self.pdf('mixed.pdf', ['A long enough page of readable agreement text.', ''])
        result = diff.compare_documents(row, row)
        self.assertTrue(result.partial)
        self.assertIn('일부', result.summary)
        self.assertIn('확인하지 못한', result.summary)

    def test_docx_reads_fresh_body_and_warns_about_omitted_layout(self):
        left = self.docx('left.docx', ['Agreement', 'Amount 10'])
        right = self.docx('right.docx', ['Agreement', 'Amount 20'])
        result = diff.compare_documents(left, right)
        self.assertEqual((result.removed, result.added), (1, 1))
        self.assertTrue(any('머리글' in note for note in result.notes))

    def test_cached_body_is_ignored_when_fresh_text_has_changed(self):
        left = self.text('left.txt', 'new left'); left['body'] = 'old equal'
        right = self.text('right.txt', 'new right'); right['body'] = 'old equal'
        self.assertTrue(diff.compare_documents(left, right).rows)

    def test_utf16_and_cp949_are_read_correctly(self):
        left = self.text('left.txt', '급여 명세서 내용', 'utf-16')
        right = self.text('right.txt', '급여 명세서 내용', 'cp949')
        result = diff.compare_documents(left, right)
        self.assertEqual(result.rows, [])
        self.assertFalse(result.partial)

    def test_binary_txt_is_unavailable(self):
        path = self.base / 'binary.txt'; path.write_bytes(b'\0data\0')
        self.assertFalse(diff.read_document_text(path).readable)

    def test_byte_and_character_limits_are_disclosed_even_when_prefixes_match(self):
        row = self.text('long.txt', '한글 문장입니다. ' * 30_000)
        with patch.object(diff, 'MAX_TEXT_BYTES', 101):
            result = diff.compare_documents(row, row)
        self.assertEqual(result.state, 'ready')
        self.assertTrue(result.partial)
        self.assertIn('일부', result.summary)

    def test_huge_file_is_rejected_before_open(self):
        row = self.text('large.txt', 'synthetic long enough')
        with patch.object(diff, 'MAX_FILE_BYTES', 2), patch.object(Path, 'open', side_effect=AssertionError):
            result = diff.read_document_text(row)
        self.assertFalse(result.readable)
        self.assertIn('큰 파일', result.notice)

    def test_many_changes_bound_screen_output_and_disclose_omitted_changes(self):
        left = self.text('left.txt', '\n'.join(f'Old {index}' for index in range(900)))
        right = self.text('right.txt', '\n'.join(f'New {index}' for index in range(900)))
        result = diff.compare_documents(left, right)
        self.assertLessEqual(len(result.rows), diff.MAX_DISPLAY_ROWS)
        self.assertTrue(result.partial)
        self.assertTrue(any('앞부분만 표시' in note for note in result.notes))

    def test_excess_segment_count_is_bounded(self):
        text = '\n'.join(f'line {index}' for index in range(2000))
        segments, partial = diff._segments(text)
        self.assertEqual(len(segments), diff.MAX_SEGMENTS)
        self.assertTrue(partial)

    def test_already_cancelled_never_opens_a_file(self):
        with patch.object(Path, 'stat', side_effect=AssertionError):
            result = diff.compare_documents({'path': 'not read'}, {'path': 'not read'}, lambda: True)
        self.assertEqual(result.state, 'cancelled')

    def test_mutation_during_read_discards_extracted_text(self):
        row = self.text('changing.txt', 'original text')
        original = diff._text_file
        def changing(path, cancelled):
            value = original(path, cancelled)
            path.write_text('changed synthetic text', encoding='utf-8')
            return value
        with patch.object(diff, '_text_file', side_effect=changing):
            result = diff.read_document_text(row)
        self.assertFalse(result.readable)
        self.assertIn('바뀌었어요', result.notice)


if __name__ == '__main__':
    unittest.main()
