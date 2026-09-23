"""Persistent favorite references; all originals are generated fixtures.

Recycle calls are mocked and never delete any file.
"""
from collections import OrderedDict
import gc
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import weakref

from PIL import Image
from file_memory import FileMemory
from photo_controller import PhotoController
from photo_gallery import PhotoGallery, _decode_saved, _saved_problem


class Value:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class FavoriteFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='photo-favorites-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / '합성 사진 {파랑}.png'
        self.other = self.base / '다른 사진.png'
        Image.new('RGB', (80, 60), 'blue').save(self.path)
        Image.new('RGB', (80, 60), 'red').save(self.other)
        self.row = dict(path=str(self.path), name=self.path.name, mtime=self.path.stat().st_mtime,
                        thumbnail=str(self.other), fields={'taken_date': '2025-05-04'})
        self.originals = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (self.path, self.other)}
        self.addCleanup(self.check_originals)
        self.memory = FileMemory(self.base / 'state')

    def check_originals(self):
        for path, snapshot in self.originals.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), snapshot)


class PhotoFavoriteLogicTests(FavoriteFixtures):
    def app(self):
        return SimpleNamespace(file_memory=self.memory, result_browsers=weakref.WeakSet())

    def gallery(self, app=None):
        gallery = PhotoGallery.__new__(PhotoGallery)
        gallery.app = app or self.app()
        gallery.app.result_browsers.add(gallery)
        gallery.win = Mock()
        gallery.rows = [dict(self.row)]
        gallery.visible = [dict(self.row)]
        gallery.saved = OrderedDict()
        gallery.saved_only = Value(False)
        gallery.saved_context = False
        gallery._closed = False
        gallery._images = OrderedDict()
        gallery._removed_paths = set()
        gallery._deleting_paths = set()
        gallery.selected_path = str(self.path)
        gallery.refresh = Mock()
        gallery._mark_selection = Mock()
        gallery._update_tabs = Mock()
        gallery.reload_saved(refresh=False)
        return gallery

    def test_save_survives_new_gallery_and_fresh_database_instance(self):
        gallery = self.gallery()
        gallery.toggle_saved(str(self.path))
        self.assertIn(str(self.path), gallery.saved)
        fresh = FileMemory(self.base / 'state')
        reopened = self.gallery(SimpleNamespace(file_memory=fresh, result_browsers=weakref.WeakSet()))
        self.assertIn(str(self.path), reopened.saved)
        self.assertTrue(reopened.saved[str(self.path)]['available'])
        self.assertEqual(reopened.saved[str(self.path)]['fields']['taken_date'], '2025-05-04')

    def test_unsave_updates_other_open_galleries_and_keeps_original(self):
        app = self.app()
        first, second = self.gallery(app), self.gallery(app)
        first.toggle_saved(str(self.path))
        self.assertIn(str(self.path), second.saved)
        second.toggle_saved(str(self.path))
        self.assertEqual(first.saved, {})
        self.assertEqual(self.memory.saved_photos(), [])

    def test_failed_save_or_unsave_keeps_previous_ui_and_database_state(self):
        gallery = self.gallery()
        with patch('photo_gallery.messagebox.showerror') as error:
            with patch.object(self.memory, 'save_photo', side_effect=OSError('fixture')):
                gallery.toggle_saved(str(self.path))
            self.assertFalse(gallery.saved)
            self.assertFalse(self.memory.saved_photos())
            gallery.toggle_saved(str(self.path))
            with patch.object(self.memory, 'unsave_photo', side_effect=OSError('fixture')):
                gallery.toggle_saved(str(self.path))
            self.assertIn(str(self.path), gallery.saved)
            self.assertEqual(len(self.memory.saved_photos()), 1)
            self.assertEqual(error.call_count, 2)

    def test_failed_reload_does_not_discard_loaded_favorites(self):
        gallery = self.gallery()
        gallery.toggle_saved(str(self.path))
        with patch.object(self.memory, 'saved_photos', side_effect=OSError('fixture')):
            with patch('photo_gallery.messagebox.showerror') as error:
                self.assertFalse(gallery.reload_saved())
        self.assertIn(str(self.path), gallery.saved)
        error.assert_called_once()

    def test_outdated_search_row_cannot_save_replacement_photo_under_same_path(self):
        gallery = self.gallery()
        gallery.rows[0]['mtime'] -= 5
        with patch('photo_gallery.messagebox.showerror') as error:
            gallery.toggle_saved(str(self.path))
        self.assertFalse(self.memory.saved_photos())
        self.assertFalse(gallery.saved)
        self.assertIn('바뀌었어요', error.call_args.args[0])

    def test_missing_reference_remains_visible_and_can_be_unsaved(self):
        gallery = self.gallery()
        gallery.toggle_saved(str(self.path))
        missing = dict(self.memory.saved_photos()[0], available=False,
                       unavailable_reason='파일이 없거나 읽을 수 없어요')
        with patch.object(self.memory, 'saved_photos', return_value=[missing]):
            gallery.reload_saved()
        self.assertFalse(gallery.saved[str(self.path)]['available'])
        gallery.toggle_saved(str(self.path))
        self.assertFalse(self.memory.saved_photos())

    def test_saved_decode_uses_original_instead_of_unrelated_cached_thumbnail(self):
        self.memory.save_photo(self.row)
        row = dict(self.memory.saved_photos()[0], _saved_reference=True)
        picture, problem = _decode_saved(row, (100, 100))
        self.assertEqual(problem, '')
        self.assertEqual(picture.getpixel((0, 0)), (0, 0, 255))
        viewer = PhotoGallery._viewer_row(row)
        self.assertNotIn('thumbnail', viewer)

    def test_missing_replaced_and_mid_decode_changed_original_never_use_stale_pixels(self):
        self.memory.save_photo(self.row)
        row = dict(self.memory.saved_photos()[0], _saved_reference=True)
        with patch('photo_gallery.Path.stat', side_effect=FileNotFoundError):
            with patch('photo_gallery._decode') as decode:
                self.assertIsNone(_decode_saved(row, (100, 100))[0])
                decode.assert_not_called()
        changed = dict(row, saved_size=row['saved_size'] + 1)
        with patch('photo_gallery._decode') as decode:
            self.assertEqual(_decode_saved(changed, (100, 100)), (None, '원본이 바뀌었어요'))
            decode.assert_not_called()
        with patch('photo_gallery._saved_problem', side_effect=['', '원본이 바뀌었어요']):
            self.assertEqual(_decode_saved(row, (100, 100)), (None, '원본이 바뀌었어요'))

    def test_large_viewer_revalidates_saved_identity_and_never_falls_back_to_thumbnail(self):
        from photo_viewer import _load_photo
        self.memory.save_photo(self.row)
        row = dict(self.memory.saved_photos()[0], _saved_reference=True)
        changed = dict(row, saved_size=row['saved_size'] + 1)
        picture, message, can_open, can_reveal = _load_photo(changed, (100, 100))
        self.assertIsNone(picture)
        self.assertIn('바뀌었어요', message)
        self.assertFalse(can_open)
        self.assertFalse(can_reveal)

    def test_reload_remapped_reference_replaces_old_saved_path(self):
        gallery = self.gallery()
        gallery.toggle_saved(str(self.path))
        # Backend remap follows confirmed organizer movement; this test changes
        # references only, leaving both generated originals untouched.
        self.memory.remap_paths({str(self.path): str(self.other)})
        gallery.reload_saved()
        self.assertNotIn(str(self.path), gallery.saved)
        self.assertIn(str(self.other), gallery.saved)
        self.assertFalse(gallery.saved[str(self.other)]['available'])

    def test_saved_context_ignores_unrelated_search_response(self):
        gallery = self.gallery()
        gallery.saved_context = True
        gallery.search_query = 'same text'
        before = list(gallery.rows)
        gallery.update_results([], 'same text')
        self.assertEqual(gallery.rows, before)

    def test_saved_entry_is_independent_of_active_search_and_reuses_its_own_window(self):
        original_gallery = object()
        app = SimpleNamespace(photo_gallery=original_gallery, result_cache=[{'path': 'document'}],
                              query=Value('급여명세서'), is_photo_search=False, last_submitted='급여명세서')
        fake = Mock(_closed=False)
        fake.win.winfo_exists.return_value = True
        with patch('photo_gallery.PhotoGallery', return_value=fake) as create:
            self.assertIs(PhotoController.open_saved_photos(app), fake)
            create.assert_called_once_with(app, [], query='', saved_context=True)
            self.assertIs(PhotoController.open_saved_photos(app), fake)
        self.assertIs(app.photo_gallery, original_gallery)
        self.assertEqual(app.result_cache, [{'path': 'document'}])
        self.assertEqual(app.query.get(), '급여명세서')
        self.assertEqual(app.last_submitted, '급여명세서')
        self.assertFalse(app.is_photo_search)

    def test_closed_gallery_success_persists_removal_without_touching_widgets(self):
        gallery = self.gallery()
        gallery.toggle_saved(str(self.path))
        gallery._closed = True
        gallery.remove_photo(str(self.path))
        self.assertEqual(self.memory.saved_photos(), [])
        gallery.win.assert_not_called()

    def test_controller_recycle_success_without_any_open_gallery_removes_favorite(self):
        self.memory.save_photo(self.row)
        class Library:
            db = 'synthetic'
            mutation_lock = threading.Lock()
            search_coverage = {}
            def forget_file(self, path): pass
            def coverage(self, roots): return {}
        document, photos = Library(), Library()
        # The real libraries have separate locks.
        document.mutation_lock = threading.Lock()
        photos.mutation_lock = threading.Lock()
        app = SimpleNamespace(file_memory=self.memory, photo_deleting=set(), photo_removed=set(),
                              photo_removal_generation=0, status=Value(''), library=document, photo_library=photos,
                              events=queue.Queue(), photo_search_roots=lambda: [], result_cache=[],
                              result_browsers=weakref.WeakSet(), bubble=None, page='other', is_photo_search=False)
        completed = []
        for result in ({'ok': False, 'cancelled': True}, {'ok': False, 'error': 'fixture'}, {'ok': True}):
            with patch('photo_recycle.recycle_photo', return_value=dict(result)):
                PhotoController.recycle_photo(app, self.row, completed.append)
                callback = app.events.get(timeout=5)
                callback()
            self.assertEqual(bool(self.memory.saved_photos()), not result['ok'])
        self.assertEqual(len(completed), 3)


class PhotoFavoriteTkTests(FavoriteFixtures):
    def setUp(self):
        super().setUp()
        gc.collect()
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda _kind, value, _trace: self.errors.append(str(value))
        self.events = []
        self.app = SimpleNamespace(root=self.root, file_memory=self.memory, settings={'text_scale': 1},
                    last_submitted='사진 검색', photo_indexing=False,
                    photo_library=SimpleNamespace(search_coverage={}),
                    open_file=lambda path: self.events.append(('open', path)),
                    reveal=lambda path: self.events.append(('reveal', path)),
                    preview=lambda row: self.events.append(('preview', row['path'])),
                    recycle_photo=lambda row, callback: self.events.append(('recycle', callback)),
                    refine_photo=lambda text: self.events.append(('search', text)))
        self.galleries = []

    def gallery(self, saved=False):
        gallery = PhotoGallery(self.app, [] if saved else [dict(self.row)], query='', saved_context=saved)
        self.galleries.append(gallery)
        self.pump()
        return gallery

    def pump(self, predicate=lambda: True, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail('Synthetic photo UI did not become ready')

    def tearDown(self):
        for gallery in self.galleries:
            gallery.close()
            gallery._pool.shutdown(wait=True, cancel_futures=True)
        for callback in self.root.tk.call('after', 'info'):
            self.root.tk.call('after', 'cancel', callback)
        self.root.destroy()
        self.galleries.clear()
        self.app = self.root = None
        gc.collect()
        self.assertEqual(self.errors, [])

    def test_window_close_reopen_and_new_backend_keep_saved_photo_and_real_preview(self):
        first = self.gallery()
        first.toggle_saved(str(self.path))
        first.close()
        self.app.file_memory = FileMemory(self.base / 'state')
        saved = self.gallery(saved=True)
        self.assertTrue(saved.saved_only.get())
        self.assertEqual([row['path'] for row in saved.visible], [str(self.path)])
        self.assertFalse(saved.all_button.winfo_manager())
        self.assertFalse(saved.refine_box.winfo_manager())
        self.assertNotIn('닫기 전', saved.win.title())
        self.pump(lambda: saved.preview_photo is not None)
        saved.toggle_saved(str(self.path))
        self.assertEqual(saved.visible, [])
        self.assertEqual(self.app.file_memory.saved_photos(), [])

    def test_missing_row_has_no_pixels_and_only_unsave_is_enabled(self):
        self.memory.save_photo(self.row)
        missing = dict(self.memory.saved_photos()[0], available=False,
                       unavailable_reason='파일이 없거나 읽을 수 없어요')
        with patch.object(self.memory, 'saved_photos', return_value=[missing]):
            gallery = self.gallery(saved=True)
            self.pump(lambda: '없거나' in gallery.preview_image.cget('text'))
        self.assertIsNone(gallery.preview_photo)
        self.assertEqual(gallery.photos, {})
        self.assertEqual(str(gallery.save_button.cget('state')), 'normal')
        for button in gallery.actions:
            if button is not gallery.save_button:
                self.assertEqual(str(button.cget('state')), 'disabled')
        gallery.open(); gallery.open_viewer(); gallery.reveal(); gallery.find_similar()
        self.assertFalse(self.events)
        gallery.toggle_saved(str(self.path))
        self.assertFalse(self.memory.saved_photos())

    def test_recycle_failure_cancel_and_late_success_obey_persistent_state(self):
        gallery = self.gallery()
        gallery.toggle_saved(str(self.path))
        with patch('photo_gallery.messagebox.askyesno', return_value=False):
            gallery.delete_photo(str(self.path))
        self.assertFalse(self.events)
        self.assertEqual(len(self.memory.saved_photos()), 1)
        with patch('photo_gallery.messagebox.askyesno', return_value=True):
            gallery.delete_photo(str(self.path))
        with patch('photo_gallery.messagebox.showerror'):
            self.events[-1][1]({'ok': False, 'error': 'fixture'})
        self.assertEqual(len(self.memory.saved_photos()), 1)
        with patch('photo_gallery.messagebox.askyesno', return_value=True):
            gallery.delete_photo(str(self.path))
        finished = self.events[-1][1]
        gallery.close()
        finished({'ok': True})
        self.assertEqual(self.memory.saved_photos(), [])

    def test_saved_viewer_resize_and_reselection_recheck_original_identity(self):
        self.memory.save_photo(self.row)
        gallery = self.gallery(saved=True)
        gallery.open_viewer()
        viewer = gallery.viewer
        self.pump(lambda: viewer.photo is not None)
        with patch('photo_gallery._saved_problem', return_value='원본이 바뀌었어요'):
            viewer.win.geometry('640x500')
            self.pump(lambda: viewer.photo is None and '바뀌었어요' in viewer.status.cget('text'))
            self.assertEqual(str(viewer.open_button.cget('state')), 'disabled')
            self.assertEqual(str(viewer.delete_button.cget('state')), 'disabled')
        # Re-showing the same saved row must recheck even at the same size.
        viewer.show(viewer.row, 0, 1)
        self.pump(lambda: viewer.photo is not None)
        with patch('photo_gallery._saved_problem', return_value='원본이 바뀌었어요'):
            viewer.show(viewer.row, 0, 1)
            self.pump(lambda: viewer.photo is None and '바뀌었어요' in viewer.status.cget('text'))
        viewer.close()
        viewer._pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    unittest.main()
