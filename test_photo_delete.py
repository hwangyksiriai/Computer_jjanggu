"""Photo deletion UI regressions use synthetic files and a mocked recycle service."""
from pathlib import Path
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from photo_gallery import PAGE_SIZE, PhotoGallery
from photo_library import PhotoLibrary
from photo_viewer import OFFLINE, PhotoViewer


class PhotoDeleteGalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.rows = []
        for number in range(PAGE_SIZE + 2):
            path = self.folder / f'photo-{number:02d}.jpg'
            Image.new('RGB', (120, 80), 'blue').save(path)
            self.rows.append(dict(path=str(path), name=path.name, mtime=number,
                                  size=path.stat().st_size, fields={}, body='', visual=[]))
        self.requests = []
        self.app = SimpleNamespace(
            root=self.root, settings={'text_scale': 1}, last_submitted='사진 보여줘', indexing=False,
            library=SimpleNamespace(search_coverage={}),
            active_library=SimpleNamespace(search_coverage={'roots': [str(self.folder)], 'images': len(self.rows)}),
            open_file=lambda value: None, preview=lambda value: None, reveal=lambda value: None,
            recycle_photo=lambda row, callback: self.requests.append((dict(row), callback)),
        )
        self.gallery = PhotoGallery(self.app, self.rows)
        self.confirm = patch('photo_gallery.messagebox.askyesno', return_value=True).start()
        self.show_error = patch('photo_gallery.messagebox.showerror').start()
        self.show_info = patch('photo_gallery.messagebox.showinfo').start()
        self.addCleanup(patch.stopall)
        self.pump(.1)

    def tearDown(self):
        self.gallery.close()
        self.root.destroy()
        self.temp.cleanup()
        self.assertFalse(self.errors, self.errors)

    def pump(self, seconds=.08):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)

    def finish(self, result, request=-1):
        self.requests[request][1](result)
        self.pump()

    def test_confirmation_cancel_preserves_original_results_and_saved_photo(self):
        path = self.rows[0]['path']
        self.gallery.toggle_saved(path)
        self.confirm.return_value = False
        self.gallery.delete_photo()
        self.confirm.assert_called_once()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.gallery.rows, self.rows)
        self.assertEqual(self.gallery.selected_path, path)
        self.assertIn(path, self.gallery.saved)
        self.assertTrue(Path(path).is_file())

    def test_pending_request_disables_duplicate_deletion_without_removing_photo(self):
        path = self.rows[0]['path']
        self.gallery.delete_photo(path)
        self.gallery.delete_photo(path)
        self.assertEqual(len(self.requests), 1)
        self.assertIn(path, self.gallery._deleting_paths)
        self.assertIn(path, [row['path'] for row in self.gallery.rows])
        self.assertTrue(Path(path).is_file())
        self.assertEqual(str(self.gallery.delete_button.cget('state')), 'disabled')

    def test_recycle_failure_keeps_result_and_allows_retry(self):
        path = self.rows[0]['path']
        self.gallery.delete_photo(path)
        self.finish({'ok': False, 'cancelled': False, 'error': '파일을 다른 프로그램에서 사용 중입니다.'})
        self.assertIn(path, [row['path'] for row in self.gallery.rows])
        self.assertNotIn(path, self.gallery._deleting_paths)
        self.assertTrue(Path(path).is_file())
        self.show_error.assert_called_once()
        self.gallery.delete_photo(path)
        self.assertEqual(len(self.requests), 2)

    def test_shell_cancel_keeps_result_without_error_dialog(self):
        path = self.rows[0]['path']
        self.gallery.delete_photo(path)
        self.finish({'ok': False, 'cancelled': True, 'error': ''})
        self.assertEqual(self.gallery.rows, self.rows)
        self.assertNotIn(path, self.gallery._deleting_paths)
        self.show_error.assert_not_called()

    def test_success_removes_saved_result_and_advances_large_viewer(self):
        path = self.rows[0]['path']
        self.gallery.toggle_saved(path)
        self.gallery.open_viewer()
        self.gallery.delete_photo(path)
        self.finish({'ok': True, 'cancelled': False, 'error': ''})
        self.assertNotIn(path, [row['path'] for row in self.gallery.rows])
        self.assertNotIn(path, [row['path'] for row in self.gallery.visible])
        self.assertNotIn(path, self.gallery.saved)
        self.assertEqual(self.gallery.selected_path, self.rows[1]['path'])
        self.assertEqual(self.gallery.viewer.row['path'], self.rows[1]['path'])
        self.assertEqual(self.gallery.viewer.total, len(self.rows) - 1)
        # The gallery must delegate deletion, never mutate original files itself.
        self.assertTrue(Path(path).is_file())

    def test_completion_uses_requested_photo_after_user_changes_selection(self):
        original_path, next_path = self.rows[0]['path'], self.rows[1]['path']
        self.gallery.delete_photo(original_path)
        self.gallery.select(next_path)
        self.finish({'ok': True, 'cancelled': False, 'error': ''})
        self.assertEqual(self.requests[0][0]['path'], original_path)
        self.assertEqual(self.gallery.selected_path, next_path)
        self.assertNotIn(original_path, [row['path'] for row in self.gallery.rows])
        self.assertIn(next_path, [row['path'] for row in self.gallery.rows])

    def test_old_search_refresh_does_not_resurrect_successfully_deleted_photo(self):
        path = self.rows[0]['path']
        self.gallery.delete_photo(path)
        self.finish({'ok': True, 'cancelled': False, 'error': ''})
        self.gallery.update_results(list(self.rows), self.gallery.search_query)
        self.assertNotIn(path, [row['path'] for row in self.gallery.rows])
        self.assertNotIn(path, [row['path'] for row in self.gallery.visible])
        self.assertEqual(len(self.gallery.rows), len(self.rows) - 1)

    def test_removing_last_page_keeps_nearest_remaining_photo_selected(self):
        self.gallery.paginate(1)
        for row in self.rows[PAGE_SIZE:]:
            self.gallery.delete_photo(row['path'])
            self.finish({'ok': True, 'cancelled': False, 'error': ''})
        self.assertEqual(self.gallery.page, 0)
        self.assertEqual(self.gallery.selected_path, self.rows[PAGE_SIZE - 1]['path'])
        self.assertEqual(len(self.gallery.visible), PAGE_SIZE)

    def test_last_result_closes_large_view_and_leaves_empty_gallery(self):
        self.gallery.update_results([self.rows[0]], self.gallery.search_query)
        self.gallery.toggle_saved(self.rows[0]['path'])
        self.gallery.open_viewer()
        self.gallery.delete_photo()
        self.finish({'ok': True, 'cancelled': False, 'error': ''})
        self.assertEqual(self.gallery.rows, [])
        self.assertEqual(self.gallery.visible, [])
        self.assertFalse(self.gallery.saved)
        self.assertIsNone(self.gallery.selected())
        self.assertIsNone(self.gallery.viewer)
        self.assertEqual(str(self.gallery.delete_button.cget('state')), 'disabled')

    def test_background_completion_after_close_does_not_touch_destroyed_widgets(self):
        self.gallery.delete_photo()
        self.gallery.close()
        self.finish({'ok': True, 'cancelled': False, 'error': ''})
        self.assertFalse(self.errors)


class PhotoDeleteViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.photo = self.folder / 'original.jpg'
        Image.new('RGB', (160, 100), 'blue').save(self.photo)
        self.root = tk.Tk()
        self.root.withdraw()
        self.calls = []
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.viewer = PhotoViewer(self.root, lambda delta: None, lambda: None, lambda: None,
                                  lambda: None, on_delete=lambda: self.calls.append('delete'))

    def tearDown(self):
        self.viewer.close()
        self.root.destroy()
        self.temp.cleanup()
        self.assertFalse(self.errors, self.errors)

    def wait_for(self, predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail('사진 표시 작업이 끝나지 않았습니다.')

    def test_loaded_original_delegates_delete_without_removing_file(self):
        self.viewer.show({'path': str(self.photo), 'name': self.photo.name}, 0, 1)
        self.wait_for(lambda: self.viewer._displayed_path == str(self.photo))
        self.assertEqual(str(self.viewer.delete_button.cget('state')), 'normal')
        self.viewer.delete_current()
        self.assertEqual(self.calls, ['delete'])
        self.assertTrue(self.photo.is_file())

    def test_missing_original_disables_delete_even_when_thumbnail_is_available(self):
        missing = str(self.folder / 'missing.jpg')
        self.viewer.show({'path': missing, 'thumbnail': str(self.photo)}, 0, 1)
        self.wait_for(lambda: self.viewer._displayed_path == missing)
        self.assertIsNotNone(self.viewer.photo)
        self.assertEqual(str(self.viewer.delete_button.cget('state')), 'disabled')
        self.viewer.delete_current()
        self.assertEqual(self.calls, [])

    def test_cloud_placeholder_disables_delete_without_hydration(self):
        self.viewer.show({'path': str(self.photo), 'status': OFFLINE}, 0, 1)
        self.wait_for(lambda: self.viewer._displayed_path == str(self.photo))
        self.assertEqual(str(self.viewer.delete_button.cget('state')), 'disabled')
        self.viewer.delete_current()
        self.assertEqual(self.calls, [])


class PhotoDeleteCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.originals = self.folder / 'Pictures'
        self.originals.mkdir()
        self.paths = [self.originals / name for name in ('remove.jpg', 'keep.jpg')]
        for path in self.paths:
            Image.new('RGB', (80, 60), 'blue').save(path)
        self.library = PhotoLibrary(self.folder / 'catalogue')
        self.library.scan([self.originals])

    def tearDown(self):
        self.temp.cleanup()

    def test_forget_removes_only_target_index_and_local_thumbnail(self):
        path, keep = map(str, self.paths)
        row = next(row for row in self.library.rows([self.originals]) if row['path'] == path)
        thumbnail = Path(row['thumbnail'])
        self.assertTrue(thumbnail.is_file())
        with self.library.connect() as connection:
            connection.execute('INSERT OR REPLACE INTO tray VALUES(?,?)', (path, 1))
            connection.execute('INSERT OR REPLACE INTO index_versions VALUES(?,?)', (path, 1))
        original_bytes = self.paths[0].read_bytes()
        self.library.forget_file(path)
        self.assertEqual([row['path'] for row in self.library.rows([self.originals])], [keep])
        with self.library.connect() as connection:
            for table in ('files', 'knowledge', 'tray', 'index_versions'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table} WHERE path=?', (path,)).fetchone()[0], 0)
        self.assertFalse(thumbnail.exists())
        self.assertEqual(self.paths[0].read_bytes(), original_bytes)
        self.assertTrue(self.paths[1].is_file())

    def test_forget_never_deletes_original_from_corrupt_thumbnail_reference(self):
        path, other_original = map(str, self.paths)
        original_bytes = self.paths[1].read_bytes()
        with self.library.connect() as connection:
            connection.execute('UPDATE knowledge SET thumbnail=? WHERE path=?', (other_original, path))
        self.library.forget_file(path)
        self.assertEqual(self.paths[1].read_bytes(), original_bytes)
        self.assertTrue(self.paths[0].is_file())

    def test_late_analysis_cannot_reinsert_forgotten_row_and_new_scan_can_restore(self):
        path = str(self.paths[0])
        row = next(row for row in self.library.rows([self.originals]) if row['path'] == path)
        self.library.forget_file(path)
        self.library.store_photo(row, row['thumbnail'], row['fields'], [1., 0.], '사진 모습 분석됨')
        self.assertNotIn(path, [row['path'] for row in self.library.rows([self.originals])])
        with self.library.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM knowledge WHERE path=?', (path,)).fetchone()[0], 0)
        # A restored original is indexed normally on a fresh explicit scan.
        self.library.scan([self.originals])
        self.assertIn(path, [row['path'] for row in self.library.rows([self.originals])])


if __name__ == '__main__':
    unittest.main()
