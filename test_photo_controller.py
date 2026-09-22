"""Run the real photo/bubble/controller flow on synthetic local originals only.

AI model entry points are forbidden in these tests. Color and brightness are
computed from actual generated pixels, independently of each file's name.
"""
from contextlib import ExitStack
import gc
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from PIL import Image
from easy_app import EasyApp
from local_ai import LocalAI
from visual_query import visual_intent


class PhotoControllerTests(unittest.TestCase):
    def setUp(self):
        # Tk variables own Tcl objects. Collect old fixture cycles on the UI
        # thread, never opportunistically in the app's background worker.
        gc.collect()
        self.gc_was_enabled = gc.isenabled()
        gc.disable()
        self.temp = tempfile.TemporaryDirectory(prefix='jjanggu-photo-controller-')
        self.base = Path(self.temp.name)
        self.data = self.base / 'settings'
        self.data.mkdir()
        self.source = self.base / 'work-documents'
        self.source.mkdir()
        self.vault = self.base / 'storage'
        self.photos = self.base / 'forgotten-folder' / 'camera'
        self.photos.mkdir(parents=True)
        self.dark_blue = self.photos / 'IMG_0042.jpg'
        self.light_blue = self.photos / 'DSC_0081.png'
        self.red = self.photos / 'attachment_732.jpg'
        for path, color in [(self.dark_blue, (15, 30, 155)), (self.light_blue, (130, 175, 250)),
                            (self.red, (200, 15, 30))]:
            Image.new('RGB', (320, 220), color).save(path)
        self.invoice = self.source / '첨부_문서.txt'
        self.invoice.write_text('INVOICE\nBill to: Test Company\nAmount due: USD 120', encoding='utf-8')
        os.utime(self.invoice, (time.time() - 120, time.time() - 120))
        (self.data / 'settings.json').write_text(json.dumps(dict(source=str(self.source), vault=str(self.vault),
            sound=False, focus_auto=False, walk=False, animate=False, auto=False, topmost=False)), encoding='utf-8')
        self.originals = self.snapshot()
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(LocalAI, 'ready', return_value=False))
        self.patches.enter_context(patch.object(LocalAI, 'ready_for', return_value=False))
        self.patches.enter_context(patch.object(LocalAI, 'detector_ready', return_value=False))
        self.ai_call = self.patches.enter_context(patch.object(LocalAI, 'call', side_effect=AssertionError('Tests must never load AI models')))
        self.patches.enter_context(patch('enhancements.Hotkey'))
        self.patches.enter_context(patch('app.App.load_voices'))
        self.patches.enter_context(patch.object(EasyApp, 'ensure_watcher'))
        self.patches.enter_context(patch.object(EasyApp, 'desktop_tick'))
        self.patches.enter_context(patch('tkinter.messagebox.showerror'))
        self.patches.enter_context(patch('tkinter.messagebox.showinfo'))
        self.speech = self.patches.enter_context(patch('app.Voice.say'))

        def test_roots(source=None, vault=None, extra=()):
            roots = [self.photos] + [Path(path) for path in extra]
            if not all(path.resolve().is_relative_to(self.base.resolve()) for path in roots):
                raise AssertionError('Photo test tried to search outside its temporary fixture')
            return roots
        self.patches.enter_context(patch('photo_controller.photo_roots', side_effect=test_roots))
        self.errors = []
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.pet_only_start = True
        self.root.report_callback_exception = lambda *args: self.errors.append(str(args[1]))
        self.app = EasyApp(self.root, self.data)
        self.idle(minimum=.7)

    def tearDown(self):
        try:
            self.app.photo_cancel.set()
            self.idle()
            for browser in list(getattr(self.app, 'result_browsers', [])):
                if hasattr(browser, '_pool'):
                    browser._pool.shutdown(wait=True, cancel_futures=True)
                if hasattr(browser, 'close'):
                    browser.close()
                elif browser.win.winfo_exists():
                    browser.win.destroy()
            self.app.pool.shutdown(wait=True, cancel_futures=True)
            self.app.index_pool.shutdown(wait=True, cancel_futures=True)
            for callback in self.root.tk.call('after', 'info'):
                # Cancel scheduling only. Each owning widget removes its own
                # registered Tcl command when destroyed below.
                self.root.tk.call('after', 'cancel', callback)
            self.app.quit()
            self.ai_call.assert_not_called()
            self.speech.assert_not_called()
        finally:
            if hasattr(self, 'root'):
                try:
                    if self.root.winfo_exists():
                        self.root.destroy()
                except tk.TclError:
                    pass
            self.patches.close()
            self.temp.cleanup()
            self.app = None
            self.root = None
            gc.collect()
            if self.gc_was_enabled:
                gc.enable()

    def snapshot(self):
        return {str(path.relative_to(self.base)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for directory in (self.source, self.photos) for path in directory.rglob('*') if path.is_file()}

    def idle(self, minimum=.2, timeout=15):
        started = time.monotonic()
        stable_since = None
        while time.monotonic() - started < timeout:
            self.root.update()
            busy = (self.app.busy or self.app.indexing or self.app.photo_indexing or self.app.photo_searching
                    or (self.app.is_photo_search and self.app.photo_pending_refresh) or not self.app.events.empty())
            if busy:
                stable_since = None
            elif stable_since is None:
                stable_since = time.monotonic()
            if stable_since and time.monotonic() - stable_since > .15 and time.monotonic() - started >= minimum:
                break
            time.sleep(.02)
        self.assertFalse(self.app.busy, 'Search worker did not finish')
        self.assertFalse(self.app.indexing, 'Document indexing did not finish')
        self.assertFalse(self.app.photo_indexing, 'Photo discovery did not finish')
        self.assertFalse(self.app.photo_searching, 'Photo search did not finish')
        self.assertEqual(self.errors, [], 'Tk callback errors')

    def search(self, query, bubble=False):
        if bubble:
            self.app.open_desktop_search()
            self.app.bubble.submit(query)
        else:
            self.app.query.set(query)
            self.app.do_search(False)
        self.idle()
        return {Path(row['path']) for row in self.app.result_cache}

    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from PhotoControllerTests.descendants(child)

    def test_photo_request_opens_gallery_without_showing_main_window_or_moving_originals(self):
        found = self.search('사진 보여줘', bubble=True)
        self.assertEqual(found, {self.dark_blue, self.light_blue, self.red})
        self.assertTrue(self.app.is_photo_search)
        self.assertIs(self.app.active_library, self.app.photo_library)
        self.assertTrue(self.app.photo_gallery.win.winfo_exists())
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertEqual(len(self.app.photo_gallery.rows), 3)
        self.assertTrue(all(row['thumbnail'] for row in self.app.photo_gallery.rows))
        self.assertIn('현재 찾은 사진 3장', self.app.bubble.message.get())
        self.assertEqual(self.app.source, self.source)
        self.assertEqual(self.app.vault, self.vault)
        self.assertEqual(self.snapshot(), self.originals)
        self.assertEqual({Path(row['path']) for row in self.app.library.rows([self.source])}, {self.invoice})

    def test_unknown_names_are_found_by_actual_photo_color_without_ai(self):
        found = self.search('파란 사진 찾아줘')
        self.assertEqual(found, {self.dark_blue, self.light_blue})
        self.assertTrue(all('파란' not in path.name for path in found))
        self.assertEqual({Path(row['path']) for row in self.app.photo_gallery.rows}, found)
        self.assertIn('파란', self.app.photo_gallery.request.cget('text'))

    def test_gallery_brightness_followup_preserves_color_and_back_restores_results(self):
        self.search('파란 사진 찾아줘')
        gallery = self.app.photo_gallery
        gallery.refine_query.set('더 어두워')
        gallery.refine()
        self.idle()
        intent = visual_intent(self.app.photo_resolved)
        self.assertEqual(intent['colors'], ['blue'])
        self.assertEqual(intent['brightness'], 'dark')
        self.assertEqual({Path(row['path']) for row in self.app.result_cache}, {self.dark_blue})
        self.assertIs(self.app.photo_gallery, gallery)
        self.assertIn('어두', gallery.request.cget('text'), 'The current condition must be visible in the reused gallery')
        gallery.refine_query.set('빨간색이야')
        gallery.refine()
        self.idle()
        self.assertEqual({Path(row['path']) for row in self.app.result_cache}, {self.red})
        back = next(widget for widget in self.descendants(gallery.win)
                    if isinstance(widget, ttk.Button) and widget.cget('text') == '조건 한 단계 뒤로')
        back.invoke()
        self.idle()
        self.assertEqual({Path(row['path']) for row in self.app.result_cache}, {self.dark_blue})
        self.assertEqual(len(self.app.photo_history), 1)
        back.invoke()
        self.idle()
        self.assertEqual({Path(row['path']) for row in self.app.result_cache}, {self.dark_blue, self.light_blue})
        self.assertFalse(visual_intent(self.app.photo_resolved)['brightness'])
        self.assertEqual(self.app.photo_history, [])

    def test_document_request_resets_photo_mode_and_next_photo_does_not_keep_old_color(self):
        self.search('파란 사진 찾아줘')
        found = self.search('인보이스 찾아줘')
        self.assertFalse(self.app.is_photo_search)
        self.assertIs(self.app.active_library, self.app.library)
        self.assertEqual(found, {self.invoice})
        found = self.search('더 어두워')
        self.assertTrue(self.app.is_photo_search)
        self.assertEqual(visual_intent(self.app.photo_resolved)['colors'], [])
        self.assertEqual(self.app.photo_history, [], 'A new photo session must not undo into an earlier document-separated search')
        self.assertEqual(found, {self.dark_blue, self.red})
        self.assertEqual(self.app.source, self.source)

    def test_voice_failure_notice_preserves_search_answer_and_bubble_results(self):
        self.search('사진 보여줘', bubble=True)
        reply = self.app.last_reply
        message = self.app.bubble.message.get()
        status = self.app.status.get()
        rows = list(self.app.bubble.rows)
        self.app.voice.error_callback('등록한 음성을 재생하지 못했어요.')
        self.idle()
        self.assertEqual(self.app.last_reply, reply)
        self.assertEqual(self.app.bubble.message.get(), message)
        self.assertEqual(self.app.bubble.rows, rows)
        self.assertEqual(self.app.status.get(), status)
        self.assertEqual(self.app.last_voice_notice, '등록한 음성을 재생하지 못했어요.')

    def test_new_request_resets_old_file_filter_even_when_result_rows_are_identical(self):
        self.search('파란 사진 찾아줘')
        gallery = self.app.photo_gallery
        gallery.query.set('IMG_0042')
        gallery.refresh()
        self.assertEqual(len(gallery.visible), 1)
        self.search('파란 사진 보여줘')
        self.assertEqual(gallery.query.get(), '')
        self.assertEqual({Path(row['path']) for row in gallery.visible}, {self.dark_blue, self.light_blue})

    def test_additional_photo_scope_never_changes_desktop_organization_source(self):
        self.search('사진 보여줘')
        extra = self.base / 'another-photo-location'
        extra.mkdir()
        added = extra / 'Kakao_001.png'
        Image.new('RGB', (160, 120), 'green').save(added)
        before = (added.read_bytes(), added.stat().st_mtime_ns)
        with patch('photo_controller.filedialog.askdirectory', return_value=str(extra)):
            self.app.choose_photo_folder()
        self.idle()
        self.assertIn(added, {Path(row['path']) for row in self.app.result_cache})
        self.assertEqual(self.app.settings['photo_roots'], [str(extra)])
        self.assertEqual(self.app.source, self.source)
        self.assertEqual(self.app.settings['source'], str(self.source))
        self.assertEqual(self.app.vault, self.vault)
        self.assertEqual((added.read_bytes(), added.stat().st_mtime_ns), before)
        self.assertEqual(self.snapshot(), self.originals)

    def test_recycle_success_and_restored_file_search(self):
        from photo_gallery import PhotoGallery
        # Register both catalogues without starting external OCR or AI models.
        with patch('core.extract', return_value=('', '이미지 글자 분석 완료')):
            self.app.library.index([self.photos])
        self.assertEqual(self.search('파란 사진 찾아줘', bubble=True), {self.dark_blue, self.light_blue})
        path = str(self.dark_blue)
        selected = next(row for row in self.app.result_cache if row['path'] == path)
        other_gallery = PhotoGallery(self.app, self.app.result_cache)
        other_gallery.toggle_saved(path)
        original_bytes = self.dark_blue.read_bytes()

        # Capture a result computed before deletion; delivering it afterwards
        # must not resurrect the old row on any current search surface.
        pending = []
        with patch.object(self.app, 'run_job', side_effect=lambda work, finish, message: pending.append((work(), finish))):
            self.app.handle_photo_search(self.app.last_submitted, speak=False, refresh=True)
        self.assertIn(path, [row['path'] for row in pending[0][0][0]])

        recycle_folder = self.base / 'test-recycle-bin'
        recycle_folder.mkdir()
        recycled = recycle_folder / self.dark_blue.name
        completed = []

        def fake_recycle(row):
            original = Path(row['path'])
            self.assertEqual(original, self.dark_blue)
            self.assertTrue(original.resolve().is_relative_to(self.base.resolve()))
            original.rename(recycled)
            return dict(ok=True, cancelled=False, error='')

        with patch('photo_recycle.recycle_photo', side_effect=fake_recycle) as recycler:
            self.app.recycle_photo(selected, completed.append)
            deadline = time.monotonic() + 10
            while not completed and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
        self.assertEqual(len(completed), 1, 'Recycle completion was not delivered through the Tk queue')
        self.assertTrue(completed[0]['ok'])
        recycler.assert_called_once()
        self.assertFalse(self.dark_blue.exists())
        self.assertEqual(recycled.read_bytes(), original_bytes)
        self.assertNotIn(path, self.app.photo_deleting)
        self.assertIn(path, self.app.photo_removed)
        for catalogue in (self.app.library, self.app.photo_library):
            self.assertNotIn(path, [row['path'] for row in catalogue.rows([self.photos])])
        for gallery in (self.app.photo_gallery, other_gallery):
            self.assertNotIn(path, [row['path'] for row in gallery.rows])
            self.assertNotIn(path, gallery.saved)
        self.assertNotIn(path, [row['path'] for row in self.app.result_cache])
        self.assertNotIn(path, [row['path'] for row in self.app.bubble.rows])

        stale_result, stale_finish = pending[0]
        stale_finish(stale_result)
        self.idle()
        self.assertNotIn(path, [row['path'] for row in self.app.result_cache])
        self.assertNotIn(path, [row['path'] for row in self.app.photo_gallery.rows])

        recycled.rename(self.dark_blue)
        self.app.start_photo_scan(force=True)
        self.idle()
        self.assertEqual(self.search('파란 사진 보여줘'), {self.dark_blue, self.light_blue})
        self.assertNotIn(path, self.app.photo_removed)
        self.assertNotIn(path, self.app.photo_gallery._removed_paths)
        self.assertIn(path, [row['path'] for row in self.app.photo_gallery.rows])
        self.assertEqual(self.snapshot(), self.originals)

    def test_recycle_failure_keeps_both_catalogues(self):
        with patch('core.extract', return_value=('', '이미지 글자 분석 완료')):
            self.app.library.index([self.photos])
        self.search('파란 사진 찾아줘', bubble=True)
        path = str(self.dark_blue)
        selected = next(row for row in self.app.result_cache if row['path'] == path)
        self.app.photo_gallery.toggle_saved(path)
        before = {id(catalogue): catalogue.rows([self.photos]) for catalogue in (self.app.library, self.app.photo_library)}
        results_before = list(self.app.result_cache)
        completed = []
        with patch('photo_recycle.recycle_photo', return_value=dict(ok=False, cancelled=False, error='다른 프로그램에서 사용 중입니다.')) as recycler:
            self.app.recycle_photo(selected, completed.append)
            deadline = time.monotonic() + 10
            while not completed and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
        self.assertEqual(len(completed), 1, 'Failed recycle completion was not delivered through the Tk queue')
        self.assertFalse(completed[0]['ok'])
        recycler.assert_called_once()
        self.assertNotIn(path, self.app.photo_deleting)
        self.assertNotIn(path, self.app.photo_removed)
        for catalogue in (self.app.library, self.app.photo_library):
            self.assertEqual(catalogue.rows([self.photos]), before[id(catalogue)])
        self.assertEqual(self.app.result_cache, results_before)
        self.assertIn(path, [row['path'] for row in self.app.photo_gallery.rows])
        self.assertIn(path, self.app.photo_gallery.saved)
        self.assertIn(path, [row['path'] for row in self.app.bubble.rows])
        self.assertEqual(self.snapshot(), self.originals)


if __name__ == '__main__':
    unittest.main()
