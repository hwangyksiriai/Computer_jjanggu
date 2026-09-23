"""Result-window regressions using synthetic documents and a real Tk event loop.

Run with the other UI tests, not in parallel with desktop interaction tests.
"""
from pathlib import Path
from contextlib import contextmanager
import gc
import hashlib
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
import weakref
from unittest.mock import Mock, patch

import fitz
from PIL import Image

from document_preview import DocumentPreview, render_document_preview
from result_browser import ResultBrowser


@contextmanager
def fake_tcl_clipboard(root):
    """Exercise native text-copy bindings without reading/writing the OS clipboard."""
    original = '::jjanggu_test_original_clipboard'
    log = '::jjanggu_test_clipboard_calls'
    root.tk.call('rename', 'clipboard', original)
    root.tk.call('set', log, '')
    root.tk.call('proc', 'clipboard', 'args', f'lappend {log} $args; return {{}}')
    try:
        yield lambda: root.tk.call('set', log)
    finally:
        root.tk.call('rename', 'clipboard', '')
        root.tk.call('rename', original, 'clipboard')
        root.tk.call('unset', log)


class ResultBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.browsers = []
        self.worker_done = []
        self.app = None
        self.addCleanup(self._release_ui)
        self.callback_errors = []
        self.root.report_callback_exception = lambda kind, value, trace: self.callback_errors.append(value)
        self.ime_patch = patch('ime_entry.attach', return_value=None)
        self.ime_patch.start()
        self.addCleanup(self.ime_patch.stop)
        original_worker = ResultBrowser._render_worker

        def tracked_worker(requests, responses):
            finished = threading.Event()
            self.worker_done.append(finished)
            try:
                original_worker(requests, responses)
            finally:
                finished.set()

        self.worker_patch = patch.object(ResultBrowser, '_render_worker', new=staticmethod(tracked_worker))
        self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)
        self.app = SimpleNamespace(
            root=self.root, settings={'text_scale': 1.0},
            query=tk.StringVar(master=self.root), last_submitted='급여명세서',
            busy=False, indexing=False, search_fx={}, result_cache=[],
            library=SimpleNamespace(search_coverage={'roots': [str(self.directory)],
                                                     'registered': 3, 'text': 3}),
            open_file=Mock(), reveal=Mock(), do_search=Mock(), copy_result_files=Mock(return_value=True),
            is_pinned=Mock(return_value=False), toggle_pinned=Mock(return_value=True),
        )

    def tearDown(self):
        for browser in self.browsers:
            if browser.win.winfo_exists():
                browser.win.destroy()
        self.wait_for(lambda: len(self.worker_done) == len(self.browsers) and all(event.is_set() for event in self.worker_done),
                      'Closing the results window did not stop its preview worker')
        self.assertEqual(self.callback_errors, [], 'Tk callback raised while rendering or closing')

    def _release_ui(self):
        """Dispose each Tcl interpreter on its creating thread before the next test.

        Mock side effects and the error/worker callbacks capture this test case.
        Merely destroying its root leaves app.query and the interpreter in those
        cycles, available for collection by the next test's render worker.
        """
        if self.root is None:
            return
        try:
            for browser in self.browsers:
                if not browser.closed:
                    browser.win.destroy()
            self.wait_for(lambda: all(event.is_set() for event in self.worker_done),
                          'Preview worker remained active during interpreter disposal')
        finally:
            self.root.report_callback_exception = None
            if self.app is not None:
                self.app.query = None
                self.app.do_search.reset_mock(return_value=True, side_effect=True)
                self.app.toggle_pinned.reset_mock(return_value=True, side_effect=True)
                self.app.is_pinned.reset_mock(return_value=True, side_effect=True)
            self.browsers.clear()
            self.app = None
            self.root.destroy()
            self.root = None
            # All this fixture's workers are stopped and its Tcl references are
            # released; collect remaining widget cycles on this same UI thread.
            gc.collect()

    def row(self, name, candidate=False):
        path = self.directory / name
        with fitz.open() as document:
            for color in ((1, 0, 0), (0, 0, 1)):
                page = document.new_page(width=300, height=400)
                page.draw_rect(page.rect, fill=color, color=color)
                page.insert_text((10, 24), 'Synthetic result fixture', fontsize=10)
            document.save(path)
        return {'path': str(path), 'name': name, 'mtime': 1_700_000_000,
                'size': path.stat().st_size, 'body': '급여명세서 테스트 자료',
                'group': '관련 후보' if candidate else '일치하는 파일',
                'reason': '테스트 문서 내용에서 찾았어요.'}

    def browser(self, rows):
        browser = ResultBrowser(self.app, rows)
        self.browsers.append(browser)
        return browser

    def wait_for(self, predicate, message='UI did not settle', timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.005)
        self.fail(message)

    def pump_for(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)

    @staticmethod
    def preview_ready(browser):
        return browser.preview_result is not None and browser.preview_result.image is not None

    @staticmethod
    def list_text(browser):
        return '\n'.join(str(widget.cget('text')) for widget in browser.list_frame.winfo_children()
                         if isinstance(widget, (tk.Label, tk.Button)))

    def test_actual_pdf_renders_off_ui_thread_and_page_buttons_show_requested_page(self):
        row = self.row('급여 자료.pdf')
        render_threads = []

        def render(*args, **kwargs):
            render_threads.append(threading.get_ident())
            return render_document_preview(*args, **kwargs)

        with patch('result_browser.render_document_preview', side_effect=render):
            browser = self.browser([row])
            self.wait_for(lambda: self.preview_ready(browser), 'First PDF page never appeared')
            self.assertTrue(render_threads)
            self.assertTrue(all(identity != threading.get_ident() for identity in render_threads))
            self.assertEqual((browser.page_index, browser.page_count), (0, 2))
            self.assertEqual(browser.preview_result.image.getpixel((100, 100)), (255, 0, 0))
            self.assertIsNotNone(browser.photo)
            self.assertEqual(browser.preview_canvas.type(browser.preview_canvas.find_all()[0]), 'image')
            self.assertEqual(str(browser.previous_page.cget('state')), 'disabled')
            self.assertEqual(browser.page_label.cget('text'), '1 / 2쪽')

            browser.next_page.invoke()
            self.wait_for(lambda: self.preview_ready(browser) and browser.preview_result.page == 1,
                          'Next PDF page never appeared')
            self.assertEqual(browser.preview_result.image.getpixel((100, 100)), (0, 0, 255))
            self.assertEqual(browser.page_label.cget('text'), '2 / 2쪽')
            self.assertEqual(str(browser.next_page.cget('state')), 'disabled')
            self.assertEqual(str(browser.previous_page.cget('state')), 'normal')

            browser.previous_page.invoke()
            self.wait_for(lambda: self.preview_ready(browser) and browser.preview_result.page == 0)
            self.assertEqual(browser.selected_path, row['path'])

    def test_late_previous_file_preview_is_discarded_while_new_file_loads(self):
        first = self.row('첫 번째.pdf')
        second = self.row('두 번째.pdf')
        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        release_second = threading.Event()
        self.addCleanup(release_first.set)
        self.addCleanup(release_second.set)

        def delayed_render(row, *args, **kwargs):
            if row['path'] == first['path']:
                first_started.set()
                if not release_first.wait(5):
                    raise TimeoutError('Test did not release first renderer')
                return DocumentPreview(image=Image.new('RGB', (40, 60), 'red'), page_count=1)
            second_started.set()
            if not release_second.wait(5):
                raise TimeoutError('Test did not release second renderer')
            return DocumentPreview(image=Image.new('RGB', (40, 60), 'blue'), page_count=1)

        with patch('result_browser.render_document_preview', side_effect=delayed_render):
            try:
                browser = self.browser([first, second])
                self.wait_for(first_started.is_set)
                browser.select(second['path'])
                release_first.set()
                self.wait_for(lambda: second_started.is_set() and browser._responses.empty(),
                              'The stale preview response was not consumed')
                self.assertEqual(browser.selected_path, second['path'])
                self.assertEqual(browser.title.cget('text'), second['name'])
                self.assertIsNone(browser.preview_result, 'Previous file flashed while next file was loading')
                self.assertIsNone(browser.photo)
                release_second.set()
                self.wait_for(lambda: self.preview_ready(browser))
                self.assertEqual(browser.preview_result.image.getpixel((10, 10)), (0, 0, 255))
            finally:
                release_first.set()
                release_second.set()

    def test_close_during_search_cancels_callbacks_and_ignores_late_updates(self):
        row = self.row('검색 전.pdf')
        browser = self.browser([row])
        self.wait_for(lambda: self.preview_ready(browser))

        def searching(**kwargs):
            self.app.busy = True

        self.app.do_search.side_effect = searching
        browser.query.set('계약서')
        browser.submit_search()
        self.assertTrue(browser.pending)
        self.app.do_search.assert_called_once_with(speak=False)
        query_reference = weakref.ref(browser.query)
        browser.win.destroy()
        self.assertIsNone(browser.query)
        self.assertIsNone(query_reference(), 'Closing a browser must release its Tcl variable on the UI thread')
        self.app.busy = False
        self.app.last_submitted = '계약서'
        self.app.result_cache = []
        browser.update_results([], '계약서')
        browser._check_search()
        # A worker may finish after its window closes; it must never touch Tk.
        browser._responses.put((browser.generation, DocumentPreview(error='late'), None))
        browser._poll_preview()
        self.pump_for(.2)
        self.assertTrue(browser.closed)
        self.assertNotIn(browser, self.app.result_browsers)
        self.assertIsNone(browser.photo)
        self.assertIsNone(browser.preview_result)
        self.assertEqual(self.callback_errors, [])

    def test_non_search_request_before_first_search_restores_enabled_button(self):
        browser=self.browser([self.row('이전 파일.pdf')])
        self.app.search_fx=None
        browser.query.set('안녕')
        browser.submit_search()
        self.wait_for(lambda:not browser.pending)
        self.assertEqual(str(browser.search_button.cget('state')),'normal')
        self.assertIn('이전 결과',browser.coverage.cget('text'))

    def test_identical_files_can_be_grouped_expanded_and_restored_without_writing(self):
        first=self.row('보고서.pdf')
        second=self.row('다른 이름.pdf')
        payload=Path(first['path']).read_bytes()
        Path(second['path']).write_bytes(payload)
        browser=self.browser([first,second])
        browser.set_grouping(True)
        self.wait_for(lambda:not browser.group_pending)
        self.assertEqual(len(browser.visible),1)
        self.assertEqual(len(browser.identical_groups[first['path']]),2)
        browser.toggle_group(first['path'])
        self.assertEqual(len(browser.visible),2)
        browser.select(second['path'])
        browser.toggle_group(first['path'])
        self.assertEqual(browser.selected_path,first['path'])
        browser.set_grouping(False)
        self.assertEqual(len(browser.visible),2)
        self.assertEqual(Path(first['path']).read_bytes(),payload)
        self.assertEqual(Path(second['path']).read_bytes(),payload)

    def test_grouping_discards_late_analysis_after_switching_to_all_files(self):
        from file_comparison import DuplicateAnalysis
        first=self.row('초안.pdf');second=self.row('복사본.pdf')
        started=threading.Event();release=threading.Event()
        def slow(rows,cancelled=lambda:False):
            started.set();release.wait(4)
            return DuplicateAnalysis(groups=[rows],unverified=[],hash_status={},cancelled=False)
        browser=self.browser([first,second])
        with patch('file_comparison.group_identical_files',side_effect=slow):
            try:
                browser.set_grouping(True)
                self.wait_for(started.is_set)
                browser.set_grouping(False);release.set()
                self.pump_for(.2)
                self.assertEqual(len(browser.visible),2)
                self.assertEqual(browser.identical_groups,{})
            finally:release.set()

    def test_expanding_group_keeps_distant_candidate_copy_next_to_representative(self):
        first=self.row('대표.pdf');copy=self.row('다른 이름의 복사본.pdf',candidate=True)
        between=[dict(path=str(self.directory/f'file-{i}.txt'),name=f'file-{i}.txt',group='일치하는 파일') for i in range(35)]
        browser=self.browser([first,*between,copy])
        browser.use_groups=True;browser.identical_groups={first['path']:[first,copy]}
        browser.refresh()
        self.assertNotIn(copy['path'],browser.cards)
        browser.toggle_group(first['path'])
        self.assertEqual([row['path'] for row in browser.displayed_rows[:2]],[first['path'],copy['path']])
        self.assertIn(copy['path'],browser.cards)
        self.assertEqual(copy['group'],'관련 후보','Grouping must not rewrite the actual search evidence')

    def test_candidates_start_collapsed_when_matches_exist_and_open_on_request(self):
        matched = self.row('찾은 파일.pdf')
        candidate = self.row('비슷한 파일.pdf', candidate=True)
        browser = self.browser([matched, candidate])
        self.assertFalse(browser.candidates_open)
        self.assertEqual([row['path'] for row in browser.displayed_rows], [matched['path']])
        self.assertNotIn(candidate['path'], browser.cards)
        self.assertIn('비슷한 파일 1개', browser.candidate_button.cget('text'))
        browser.candidate_button.invoke()
        self.assertTrue(browser.candidates_open)
        self.assertIn(candidate['path'], browser.cards)
        browser.select(candidate['path'])
        self.assertEqual(browser.selected_path, candidate['path'])
        self.assertIn('비슷한 파일', browser.badge.cget('text'))

    def test_no_candidate_section_is_shown_when_count_is_zero(self):
        matched = self.row('확실한 결과.pdf')
        candidate = self.row('검토할 파일.pdf', candidate=True)
        browser = self.browser([matched])
        self.assertNotIn('비슷한 파일', self.list_text(browser))
        browser.update_results([matched, candidate], browser.search_query)
        candidate_button = browser.candidate_button
        self.assertTrue(candidate_button.winfo_exists())
        browser.update_results([matched], browser.search_query)
        self.assertFalse(candidate_button.winfo_exists())
        self.assertNotIn('비슷한 파일', self.list_text(browser))
        self.assertEqual([row['path'] for row in browser.displayed_rows], [matched['path']])

    def test_automatic_result_updates_preserve_selection_and_candidate_expansion(self):
        first = self.row('가.pdf')
        second = self.row('나.pdf')
        candidate = self.row('다.pdf', candidate=True)
        added = self.row('새로 찾은 파일.pdf')
        browser = self.browser([first, second, candidate])
        browser.select(second['path'])
        browser.update_results([added, first, second, candidate], browser.search_query)
        self.assertEqual(browser.selected_path, second['path'])
        self.assertFalse(browser.candidates_open)
        browser.toggle_candidates()
        browser.select(candidate['path'])
        browser.update_results([second, candidate, first, added], browser.search_query)
        self.assertTrue(browser.candidates_open)
        self.assertEqual(browser.selected_path, candidate['path'])
        self.assertIn(candidate['path'], browser.cards)

    def test_candidate_only_results_are_visible_and_file_actions_use_selected_path(self):
        candidate = self.row('이름이 다른 문서.pdf', candidate=True)
        browser = self.browser([candidate])
        self.assertTrue(browser.candidates_open)
        self.assertEqual(browser.selected_path, candidate['path'])
        self.assertIn(candidate['path'], browser.cards)
        browser.file_buttons[0].invoke()
        self.app.open_file.assert_not_called()
        browser.open_button.invoke()
        self.app.open_file.assert_called_once_with(candidate['path'])
        browser.toggle_details()
        browser.folder_button.invoke()
        self.app.reveal.assert_called_once_with(candidate['path'])

    def test_selected_thirtieth_result_stays_visible_after_an_earlier_result_is_added(self):
        rows = [self.row(f'급여 자료 {number:02d}.pdf') for number in range(30)]
        added = self.row('새로 발견한 맨 앞 파일.pdf')
        browser = self.browser(rows)
        browser.select(rows[29]['path'])
        self.assertEqual(browser.visible_count, 30)
        browser.update_results([added, *rows], browser.search_query)
        self.assertEqual(browser.selected_path, rows[29]['path'])
        self.assertGreaterEqual(browser.visible_count, 31)
        self.assertIn(rows[29]['path'], browser.cards)
        self.assertIn(rows[29]['path'], [row['path'] for row in browser.displayed_rows])

    def test_photo_search_dispatch_preserves_document_results_and_clears_pending(self):
        first = self.row('문서 검색 결과.pdf')
        second = self.row('선택한 문서.pdf')
        browser = self.browser([first, second])
        browser.select(second['path'])
        previous_rows = list(browser.rows)
        previous_query = browser.search_query

        def photo_search(**kwargs):
            self.app.is_photo_search = True
            self.app.last_submitted = self.app.query.get()
            self.app.result_cache = [{'path': str(self.directory / 'photo.jpg'), 'name': 'photo.jpg'}]

        self.app.do_search.side_effect = photo_search
        browser.query.set('바다 사진 찾아줘')
        browser.submit_search()
        self.app.do_search.assert_called_once_with(speak=False)
        self.assertFalse(browser.pending)
        self.assertEqual(browser.rows, previous_rows)
        self.assertEqual(browser.search_query, previous_query)
        self.assertEqual(browser.query.get(), previous_query)
        self.assertEqual(browser.selected_path, second['path'])
        self.assertEqual(str(browser.search_button.cget('state')), 'normal')
        self.assertIn('사진 모음', browser.coverage.cget('text'))
        browser.update_results(self.app.result_cache, '바다 사진 찾아줘')
        self.assertEqual(browser.rows, previous_rows)
        self.pump_for(.15)
        self.assertFalse(browser.pending)

    def test_copy_button_uses_selected_file_and_preserves_original(self):
        first = self.row('처음 파일.pdf')
        selected = self.row('한글 {검토본} 사본.pdf')
        path = Path(selected['path'])
        before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        browser = self.browser([first, selected])
        browser.select(selected['path'])
        browser.copy_button.invoke()
        self.assertEqual(self.app.copy_result_files.call_count, 1)
        self.assertEqual(self.app.copy_result_files.call_args.args[0], [selected['path']])
        self.app.open_file.assert_not_called()
        self.assertEqual((hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns), before)
        self.assertIn('Ctrl+V', browser.transfer_note.cget('text'))

    def test_file_card_control_c_copies_its_file_without_opening(self):
        first = self.row('첫 파일.pdf')
        second = self.row('두 번째.pdf')
        browser = self.browser([first, second])
        card = browser.cards[second['path']][1]
        card.focus_force()
        self.root.update()
        with fake_tcl_clipboard(self.root) as text_copies:
            card.event_generate('<Control-c>')
            self.root.update()
            self.assertFalse(text_copies())
        self.assertEqual(self.app.copy_result_files.call_args.args[0], [second['path']])
        self.app.open_file.assert_not_called()

    def test_control_c_in_search_and_text_keeps_native_text_copy(self):
        browser = self.browser([self.row('문서.pdf')])
        browser.query.set('문자 복사 테스트')
        browser.entry.selection_range(0, 5)
        browser.entry.focus_force()
        self.root.update()
        with fake_tcl_clipboard(self.root) as copied:
            browser.entry.event_generate('<Control-c>')
            self.root.update()
            self.assertIn('문자 복사', repr(copied()))
        self.app.copy_result_files.assert_not_called()
        text = tk.Text(browser.win, height=2)
        text.pack(fill='x')
        text.insert('1.0', '본문 복사 테스트')
        text.tag_add('sel', '1.0', '1.5')
        text.focus_force()
        self.root.update()
        with fake_tcl_clipboard(self.root) as copied:
            text.event_generate('<Control-c>')
            self.root.update()
            self.assertIn('본문 복사', repr(copied()))
        self.app.copy_result_files.assert_not_called()

    def test_missing_file_disables_copy_and_does_not_report_success(self):
        row = self.row('곧 없어질 파일.pdf')
        browser = self.browser([row])
        self.wait_for(lambda: self.preview_ready(browser))
        Path(row['path']).unlink()
        browser.preview()
        self.assertEqual(str(browser.copy_button.cget('state')), 'disabled')
        browser.copy_button.invoke()
        self.app.copy_result_files.assert_not_called()
        self.assertNotIn('복사했어요', browser.transfer_note.cget('text'))

    def test_large_text_small_window_keeps_copy_button_visible(self):
        self.app.settings['text_scale'] = 1.3
        browser = self.browser([self.row('찾은 문서.pdf')])
        browser.win.geometry('780x560')
        self.pump_for(.25)
        button = browser.copy_button
        self.assertTrue(button.winfo_ismapped())
        self.assertGreater(button.winfo_width(), 30)
        self.assertGreaterEqual(button.winfo_rootx(), browser.win.winfo_rootx())
        self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), browser.win.winfo_rootx() + browser.win.winfo_width())
        self.assertGreaterEqual(button.winfo_rooty(), browser.win.winfo_rooty())
        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), browser.win.winfo_rooty() + browser.win.winfo_height())
        button.invoke()
        self.assertEqual(self.app.copy_result_files.call_count, 1)

    def test_copy_still_works_when_drag_registration_fails(self):
        row = self.row('끌기 미지원.pdf')
        with patch('result_browser.bind_file_drag', return_value=False) as binding:
            browser = self.browser([row])
        self.assertTrue(binding.called)
        browser.copy_button.invoke()
        self.assertEqual(self.app.copy_result_files.call_args.args[0], [row['path']])

    def test_context_menu_copies_the_clicked_file(self):
        first = self.row('처음 선택.pdf')
        second = self.row('우클릭한 파일.pdf')
        browser = self.browser([first, second])
        with patch.object(tk.Menu, 'tk_popup'):
            browser.file_menu(SimpleNamespace(x_root=1, y_root=1), second['path'])
        menu = browser.file_context_menu
        index = next(number for number in range(menu.index('end') + 1)
                     if menu.entrycget(number, 'label') == '파일 복사')
        # A background refresh/selection change must not retarget an open menu.
        browser.select(first['path'])
        menu.invoke(index)
        self.assertEqual(browser.selected_path, first['path'])
        self.assertEqual(self.app.copy_result_files.call_args.args[0], [second['path']])

    def connect_pin_state(self, initial=()):
        """Model the app's broadcast contract; persistence is tested end to end."""
        pinned = set(initial)
        self.app.is_pinned.side_effect = pinned.__contains__
        browsers = self.app.result_browsers

        def toggle(path, parent=None):
            if path in pinned:
                pinned.remove(path)
            else:
                pinned.add(path)
            for browser in list(browsers):
                browser.pin_state_changed()
            return path in pinned

        self.app.toggle_pinned.side_effect = toggle
        for browser in list(browsers):
            browser.pin_state_changed()
        return pinned

    def test_pin_button_targets_selection_and_updates_other_open_window(self):
        row = self.row('고정할 문서.pdf')
        path = Path(row['path'])
        before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        first = self.browser([row])
        second = self.browser([row])
        pinned = self.connect_pin_state()
        first.pin_button.invoke()
        self.assertEqual(pinned, {row['path']})
        self.assertEqual(self.app.toggle_pinned.call_args.args[0], row['path'])
        for browser in (first, second):
            self.assertIn('고정 해제', browser.pin_button.cget('text'))
        second.pin_button.invoke()
        self.assertEqual(pinned, set())
        for browser in (first, second):
            self.assertIn('고정하기', browser.pin_button.cget('text'))
        self.assertEqual((hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns), before)

    def test_missing_pinned_file_can_be_unpinned_without_open_or_copy(self):
        row = self.row('사라진 고정 파일.pdf')
        browser = self.browser([row])
        self.wait_for(lambda: self.preview_ready(browser))
        Path(row['path']).unlink()
        browser.preview()
        pinned = self.connect_pin_state([row['path']])
        self.assertEqual(str(browser.pin_button.cget('state')), 'normal')
        self.assertEqual(str(browser.open_button.cget('state')), 'disabled')
        self.assertEqual(str(browser.copy_button.cget('state')), 'disabled')
        browser.pin_button.invoke()
        self.assertEqual(pinned, set())
        self.assertEqual(str(browser.pin_button.cget('state')), 'disabled')
        self.app.open_file.assert_not_called()
        self.app.copy_result_files.assert_not_called()

    def test_failed_pin_does_not_claim_it_is_saved(self):
        row = self.row('저장 실패 문서.pdf')
        browser = self.browser([row])
        self.app.toggle_pinned.return_value = None
        browser.pin_button.invoke()
        self.assertIn('고정하기', browser.pin_button.cget('text'))
        self.assertNotIn('고정 해제', browser.pin_button.cget('text'))

    def test_final_mark_survives_reopening_and_changed_file_is_not_shown_as_verified(self):
        from final_versions import FinalVersions
        row=self.row('사용자가 확인한 최종 문서.pdf')
        path=Path(row['path']);original=path.read_bytes()
        self.app.final_versions=FinalVersions(self.directory/'metadata')
        first=self.browser([row])
        first.final_button.invoke()
        self.assertIn('내가 정한 최종본',first.final_badge.cget('text'))
        self.assertEqual(path.read_bytes(),original)
        self.app.final_versions=FinalVersions(self.directory/'metadata')
        reopened=self.browser([row])
        self.assertIn('내가 정한 최종본',reopened.final_badge.cget('text'))
        self.wait_for(lambda:self.preview_ready(first) and self.preview_ready(reopened))
        path.write_bytes(original+b'\nchanged')
        reopened.final_state_changed()
        self.assertIn('표시 후 변경됨',reopened.final_badge.cget('text'))
        self.assertNotIn('내가 정한 최종본',reopened.final_badge.cget('text'))
        reopened.final_button.invoke()
        self.assertEqual(self.app.final_versions.describe(path)['state'],'none')
        self.assertFalse(reopened.final_badge.winfo_manager())

    def test_mail_provenance_stays_in_details_and_original_message_is_explicit_action(self):
        row=self.row('메일 첨부 영수증.pdf')
        eml=self.directory/'선택한 메일.eml';eml.write_text('Subject: Synthetic\n\nmail',encoding='utf-8')
        source=dict(sender='sender@example.invalid',subject='연습 영수증 전달',date='2026-09-23',source_eml=str(eml))
        self.app.mail_source=Mock(return_value=source)
        browser=self.browser([row])
        self.assertFalse(browser.details_open)
        self.app.open_file.assert_not_called()
        browser.toggle_details()
        details=browser.details_text.get('1.0','end')
        self.assertIn(source['sender'],details);self.assertIn(source['subject'],details)
        self.assertEqual(browser.mail_button.cget('text'),'원본 메일 보기')
        browser.mail_button.invoke()
        self.app.open_file.assert_called_once_with(str(eml))

    def test_mailbox_action_accepts_only_fixed_https_provider_sites(self):
        for url in ('file:///C:/Windows/notepad.exe','https://mail.google.com.attacker.invalid/',
                    'https://mail.google.com@attacker.invalid/','javascript:alert(1)'):
            self.assertEqual(ResultBrowser._mailbox_url({'webmail_url':url}),'')
        self.assertEqual(ResultBrowser._mailbox_url({'webmail_url':'https://mail.google.com/'}),'https://mail.google.com/')

    def test_pin_button_remains_reachable_in_small_large_text_window(self):
        self.app.settings['text_scale'] = 1.3
        browser = self.browser([self.row('자주 쓸 문서.pdf')])
        self.connect_pin_state()
        browser.win.geometry('780x560')
        self.pump_for(.25)
        button = browser.pin_button
        self.assertTrue(button.winfo_ismapped())
        self.assertGreater(button.winfo_width(), 30)
        self.assertGreaterEqual(button.winfo_rootx(), browser.win.winfo_rootx())
        self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), browser.win.winfo_rootx() + browser.win.winfo_width())
        self.assertGreaterEqual(button.winfo_rooty(), browser.win.winfo_rooty())
        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), browser.win.winfo_rooty() + browser.win.winfo_height())
        button.invoke()
        self.assertIn('고정 해제', button.cget('text'))


if __name__ == '__main__':
    unittest.main()
