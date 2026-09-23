"""Read-only comparison and synthetic Tk lifecycle checks; run GUI tests serially."""
import hashlib
import gc
from pathlib import Path
import queue
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import fitz
from PIL import Image

from file_compare_ui import (FileComparisonWindow, TEXT_LIMIT, _reader, _snapshot,
                             compare_message, comparison_candidates, load_preview,
                             show_comparison)
from pdf_runtime import PDF_LOCK
from final_versions import FinalVersions


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='file-comparison-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def text_file(self, name='문서.txt', text='첫째 내용\n둘째 내용', encoding='utf-8'):
        path = self.directory / name
        path.write_text(text, encoding=encoding)
        return self.row(path)

    def row(self, path, **values):
        stat = path.stat()
        return dict(path=str(path), name=path.name, mtime=stat.st_mtime,
                    size=stat.st_size, **values)

    def pdf(self, name):
        path = self.directory / name
        with PDF_LOCK, fitz.open() as doc:
            for color in ((1, 0, 0), (0, 0, 1)):
                page = doc.new_page(width=300, height=400)
                page.draw_rect(page.rect, fill=color, color=color)
            doc.save(path)
        return self.row(path)


class ComparisonReaderTests(Fixture):
    def test_candidates_exclude_selected_duplicates_and_keep_metadata_only(self):
        selected = self.text_file()
        other = self.text_file('다른.txt')
        supplied = dict(other, body='내용', widget=object())
        candidates = comparison_candidates(selected, [selected, supplied, supplied, {}, None])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['body'], '내용')
        self.assertNotIn('widget', candidates[0])
        self.assertIsNone(_snapshot({'path': ''}))

    def test_external_korean_text_is_capped_and_original_unchanged(self):
        row = self.text_file(text='급여 명세서\n' * 30_000, encoding='cp949')
        path = Path(row['path'])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        value = load_preview(dict(path=row['path']))
        self.assertIn('급여 명세서', value['text'])
        self.assertIn('앞부분만 표시', value['text'])
        self.assertLess(len(value['text']), TEXT_LIMIT + 150)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_utf16_text_and_binary_txt_have_explicit_results(self):
        row = self.text_file(text='한글 문서', encoding='utf-16')
        self.assertIn('한글 문서', load_preview(row)['text'])
        Path(row['path']).write_bytes(b'\x00binary\x00')
        self.assertIn('글자로 표시할 수 없는', load_preview(row)['text'])

    def test_current_indexed_body_is_used_but_stale_body_is_not(self):
        row = self.text_file('계약서.docx', text='synthetic unsupported document')
        row['body'] = '문서에서 읽어 둔 계약 내용'
        self.assertIn('계약 내용', load_preview(row)['text'])
        Path(row['path']).write_text('changed bytes and length', encoding='utf-8')
        value = load_preview(row)
        self.assertNotIn('계약 내용', value['text'])
        self.assertIn('파일이 바뀌어', value['text'])

    def test_pdf_and_image_are_actual_pixels_not_indexed_thumbnails(self):
        row = self.pdf('두 장.pdf')
        result = load_preview(row, 1)['preview']
        self.assertEqual((result.page, result.page_count), (1, 2))
        self.assertEqual(result.image.getpixel((20, 20)), (0, 0, 255))
        path = self.directory / '초록.png'
        Image.new('RGB', (20, 30), 'green').save(path)
        result = load_preview(dict(path=str(path)))['preview']
        self.assertEqual(result.image.getpixel((10, 10)), (0, 128, 0))

    def test_missing_file_does_not_show_cached_body(self):
        row = self.text_file()
        row['body'] = '오래된 비공개 내용'
        Path(row['path']).unlink()
        result = load_preview(row)
        self.assertNotIn(row['body'], result['text'])
        self.assertIn('이동되었거나', result['text'])

    def test_content_labels_are_verified_and_do_not_choose_a_final_version(self):
        left = self.text_file('원본.txt', 'same')
        right = self.text_file('최종.txt', 'same')
        self.assertIn('같아요', compare_message(left, right, lambda: False))
        Path(right['path']).write_text('different', encoding='utf-8')
        self.assertIn('완전히 같지는 않아요', compare_message(left, right, lambda: False))
        Path(right['path']).unlink()
        self.assertIn('확인하지 못한', compare_message(left, right, lambda: False))

    def test_reader_drops_result_after_close_and_retains_no_window(self):
        requests, responses = queue.Queue(), queue.Queue()
        stop, cancel, started, release = (threading.Event() for _ in range(4))
        row = self.text_file()

        def delayed(*args):
            started.set()
            release.wait(3)
            return {'preview': None}

        with patch('file_compare_ui.load_preview', side_effect=delayed):
            worker = threading.Thread(target=_reader, args=(requests, responses, stop))
            worker.start()
            requests.put(('preview', (1, 0, 1), (row, 0), cancel))
            self.assertTrue(started.wait(2))
            stop.set()
            cancel.set()
            requests.put(None)
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(responses.empty())


class ComparisonWindowTests(Fixture):
    def setUp(self):
        super().setUp()
        gc.collect()
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda kind, value, trace: self.errors.append(value)
        self.app = SimpleNamespace(root=self.root, settings={'text_scale': 1.0}, open_file=Mock())
        self.windows = []

    def tearDown(self):
        for view in self.windows:
            if not view.closed:
                view.win.destroy()
            view.worker.join(3)
            self.assertFalse(view.worker.is_alive())
            self.assertTrue(view.closed)
            self.assertTrue(all(pane['photo'] is None for pane in view.panes))
        self.assertEqual(self.errors, [])
        self.root.destroy()
        self.root.report_callback_exception = None
        self.windows.clear()
        self.app = self.root = None
        gc.collect()

    def window(self, left, rows):
        view = FileComparisonWindow(self.app, left, rows)
        self.windows.append(view)
        return view

    def wait_for(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(.005)
        self.fail('Comparison window did not settle')

    @staticmethod
    def ready(view):
        return all(pane['result'] is not None for pane in view.panes)

    def test_pdf_pages_are_independent_and_original_open_uses_correct_side(self):
        left, right = self.pdf('왼쪽.pdf'), self.pdf('오른쪽.pdf')
        identities = []
        original = load_preview

        def observed(*args):
            identities.append(threading.get_ident())
            return original(*args)

        with patch('file_compare_ui.load_preview', side_effect=observed):
            view = self.window(left, [right])
            self.wait_for(lambda: self.ready(view))
            self.assertTrue(all(identity != threading.get_ident() for identity in identities))
            view.panes[0]['next'].invoke()
            self.wait_for(lambda: view.panes[0]['result'].page == 1)
            self.assertEqual(view.panes[1]['result'].page, 0)
            self.assertEqual(view.panes[0]['result'].image.getpixel((20, 20)), (0, 0, 255))
            view.panes[0]['open'].invoke()
            view.panes[1]['open'].invoke()
            self.assertEqual([call.args[0] for call in self.app.open_file.call_args_list],
                             [left['path'], right['path']])

    def test_candidate_switch_replaces_right_metadata_and_text(self):
        left = self.text_file('left.txt', '왼쪽')
        first = self.text_file('first.txt', '첫 번째')
        second = self.text_file('second.txt', '두 번째')
        view = self.window(left, [first, second])
        self.wait_for(lambda: self.ready(view))
        view.selector.current(1)
        view.selector.event_generate('<<ComboboxSelected>>')
        self.wait_for(lambda: self.ready(view) and '두 번째' in view.panes[1]['text'].get('1.0', 'end'))
        self.assertEqual(view.panes[1]['row']['path'], second['path'])
        self.assertEqual(view.panes[0]['row']['path'], left['path'])
        self.assertIn('second.txt', view.panes[1]['title'].cget('text'))

    def test_empty_candidates_offer_file_selection_and_same_file_is_rejected(self):
        left = self.text_file('left.txt')
        right = self.text_file('right.txt')
        view = self.window(left, [])
        self.assertEqual(str(view.panes[1]['open'].cget('state')), 'disabled')
        with patch('file_compare_ui.filedialog.askopenfilename', return_value=left['path']):
            view.choose_button.invoke()
        self.assertIsNone(view.other)
        self.assertIn('다른 파일', view.status.cget('text'))
        with patch('file_compare_ui.filedialog.askopenfilename', return_value=right['path']):
            view.choose_button.invoke()
        self.wait_for(lambda: self.ready(view))
        self.assertEqual(view.other['path'], right['path'])

    def test_close_during_read_cancels_jobs_and_releases_images_on_main_thread(self):
        left, right = self.pdf('left.pdf'), self.pdf('right.pdf')
        started, release = threading.Event(), threading.Event()
        original = load_preview

        def delayed(*args):
            started.set()
            release.wait(3)
            return original(*args)

        with patch('file_compare_ui.load_preview', side_effect=delayed):
            view = self.window(left, [right])
            try:
                self.wait_for(started.is_set)
                view.win.destroy()
                self.assertTrue(view._stopped.is_set())
                self.assertIsNone(view._poll_timer)
                self.assertIsNone(view._resize_timer)
            finally:
                release.set()
                view.worker.join(3)
        self.assertTrue(view._responses.empty())

    def test_780_by_560_large_text_keeps_both_open_actions_reachable(self):
        self.app.settings['text_scale'] = 1.3
        view = self.window(self.text_file('급여명세서 검토용.txt'), [self.text_file('급여명세서 수정본.txt')])
        view.win.geometry('780x560')
        self.wait_for(lambda: self.ready(view))
        self.root.update_idletasks()
        top = view.win.winfo_rooty()
        bottom = top + view.win.winfo_height()
        for pane in view.panes:
            button = pane['open']
            self.assertTrue(button.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_rooty(), top)
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), bottom)
            self.assertGreater(pane['text'].winfo_height(), 50)
            button.invoke()
        self.assertEqual(self.app.open_file.call_count, 2)

    def test_public_function_returns_toplevel(self):
        window = show_comparison(self.app, self.text_file('left.txt'), [])
        self.assertIsInstance(window, tk.Toplevel)
        self.windows.append(window.comparison)

    def test_missing_original_disables_its_open_button_after_preview(self):
        left = self.text_file('left.txt')
        right = self.text_file('right.txt')
        Path(right['path']).unlink()
        view = self.window(left, [right])
        self.wait_for(lambda: self.ready(view))
        self.assertEqual(str(view.panes[1]['open'].cget('state')), 'disabled')
        self.assertEqual(str(view.panes[0]['open'].cget('state')), 'normal')
        self.assertIn('이동되었거나', view.panes[1]['text'].get('1.0', 'end'))

    def test_text_changes_are_highlighted_and_original_previews_remain_available(self):
        left = self.text_file('left.txt', '계약 내용\n기존 금액 10원\n끝')
        right = self.text_file('right.txt', '계약 내용\n새 금액 20원\n끝')
        view = self.window(left, [right])
        self.wait_for(lambda: self.ready(view))
        view.changes_button.invoke()
        self.wait_for(lambda: view.diff_result is not None)
        self.assertTrue(view.diff_mode)
        self.assertTrue(view.panes[0]['text'].tag_ranges('removed'))
        self.assertTrue(view.panes[1]['text'].tag_ranges('added'))
        self.assertIn('기존 금액', view.panes[0]['text'].get('1.0', 'end'))
        self.assertIn('새 금액', view.panes[1]['text'].get('1.0', 'end'))
        view.panes[0]['open'].invoke()
        self.app.open_file.assert_called_once_with(left['path'])
        view.changes_button.invoke()
        self.assertFalse(view.diff_mode)
        self.assertIn('문서에서 읽은 내용', view.panes[0]['text'].get('1.0', 'end'))
        self.assertFalse(view.panes[0]['text'].tag_ranges('removed'))

    def test_scanned_pdf_changes_view_explains_unknown_instead_of_no_difference(self):
        view = self.window(self.pdf('left.pdf'), [self.pdf('right.pdf')])
        self.wait_for(lambda: self.ready(view))
        view.toggle_changes()
        self.wait_for(lambda: view.diff_result is not None)
        self.assertEqual(view.diff_result.state, 'unavailable')
        self.assertIn('판단할 수 없', view.status.cget('text'))
        view.toggle_changes()
        self.assertTrue(view.panes[0]['canvas'].winfo_manager())
        self.assertEqual(view.panes[0]['count'], 2)

    def test_final_mark_persists_switches_same_family_and_preserves_unrelated_picker_file(self):
        self.app.final_versions = FinalVersions(self.directory / 'state')
        self.app.final_versions_changed = Mock()
        first, second = self.text_file('계약_v1.txt'), self.text_file('계약_v2.txt')
        unrelated = self.text_file('다른 문서.txt')
        self.app.final_versions.mark(unrelated['path'])
        view = self.window(first, [second, unrelated])
        self.wait_for(lambda: all(pane['final_state'] is not None for pane in view.panes))
        view.panes[0]['final_button'].invoke()
        self.wait_for(lambda: view.panes[0]['final_state']['state'] == 'final')
        self.assertEqual(FinalVersions(self.directory / 'state').describe(first['path'])['state'], 'final')
        view.panes[1]['final_button'].invoke()
        self.wait_for(lambda: view.panes[1]['final_state']['state'] == 'final')
        self.assertEqual(self.app.final_versions.describe(first['path'])['state'], 'none')
        self.assertEqual(self.app.final_versions.describe(unrelated['path'])['state'], 'final')
        view.panes[1]['final_button'].invoke()
        self.wait_for(lambda: view.panes[1]['final_state']['state'] == 'none')
        self.assertEqual(self.app.final_versions_changed.call_count, 3)

    def test_changed_mark_is_explicit_and_remains_clearable(self):
        self.app.final_versions = FinalVersions(self.directory / 'state')
        row = self.text_file('final.txt')
        self.app.final_versions.mark(row['path'])
        Path(row['path']).write_text('changed synthetic file', encoding='utf-8')
        view = self.window(row, [])
        self.wait_for(lambda: view.panes[0]['final_state'] is not None)
        self.assertEqual(view.panes[0]['final_state']['state'], 'changed')
        self.assertIn('표시 후 변경됨', view.panes[0]['final_label'].cget('text'))
        self.assertEqual(view.panes[0]['final_button'].cget('text'), '최종본 표시 해제')
        view.panes[0]['final_button'].invoke()
        self.wait_for(lambda: view.panes[0]['final_state']['state'] == 'none')

    def test_failed_final_mark_preserves_previous_state_and_can_retry(self):
        self.app.final_versions = FinalVersions(self.directory / 'state')
        view = self.window(self.text_file('left.txt'), [])
        self.wait_for(lambda: view.panes[0]['final_state'] is not None)
        with patch('file_compare_ui.FinalVersions.mark', side_effect=OSError('synthetic')):
            view.panes[0]['final_button'].invoke()
            self.wait_for(lambda: not view._final_busy)
            self.assertIn('못했어요', view.status.cget('text'))
        self.assertEqual(view.panes[0]['final_state']['state'], 'none')
        self.assertEqual(str(view.panes[0]['final_button'].cget('state')), 'normal')

    def test_enhanced_actions_fit_small_window_with_large_text(self):
        self.app.final_versions = FinalVersions(self.directory / 'state')
        self.app.settings['text_scale'] = 1.3
        view = self.window(self.text_file('left_v1.txt'), [self.text_file('left_v2.txt')])
        view.win.geometry('780x560')
        self.wait_for(lambda: all(pane['final_state'] is not None for pane in view.panes))
        self.root.update_idletasks()
        bottom = view.win.winfo_rooty() + view.win.winfo_height()
        for pane in view.panes:
            for key in ('open', 'final_button'):
                control = pane[key]
                self.assertTrue(control.winfo_ismapped())
                self.assertLessEqual(control.winfo_rooty() + control.winfo_height(), bottom)
            self.assertGreater(pane['text'].winfo_height(), 50)

    def test_close_during_diff_discards_late_response(self):
        view = self.window(self.text_file('left.txt'), [self.text_file('right.txt')])
        self.wait_for(lambda: self.ready(view))
        started, release = threading.Event(), threading.Event()
        from document_diff import DocumentDiff
        def delayed(*args):
            started.set()
            release.wait(3)
            return DocumentDiff(state='ready', summary='late')
        with patch('file_compare_ui.compare_documents', side_effect=delayed):
            view.toggle_changes()
            self.wait_for(started.is_set)
            view.win.destroy()
            release.set()
            view.worker.join(3)
        self.assertTrue(view._responses.empty())


if __name__ == '__main__':
    unittest.main(verbosity=2)
