import tempfile
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
import unittest
from unittest.mock import patch

from PIL import Image

from photo_viewer import OFFLINE, PhotoViewer, _load_photo


class PhotoViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.events = []
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.rows = []
        for name, size, color in [('landscape.jpg', (640, 320), 'blue'), ('portrait.png', (180, 360), 'red')]:
            path = self.folder / name
            Image.new('RGB', size, color).save(path)
            self.rows.append({'path': str(path), 'name': name, 'reason': '찾던 모습과 비슷한 사진'})
        self.viewer = PhotoViewer(self.root, lambda delta: self.events.append(('move', delta)),
                                  lambda: self.events.append(('open',)),
                                  lambda: self.events.append(('reveal',)),
                                  lambda: self.events.append(('close',)))
        self.pump(.18)

    def tearDown(self):
        self.viewer.close()
        self.root.destroy()
        self.temp.cleanup()
        self.assertFalse(self.errors, self.errors)

    def pump(self, seconds=.1):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(.005)

    def wait_for(self, predicate, seconds=5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail('사진 표시 작업이 끝나지 않았습니다.')

    def test_entire_photo_uses_original_not_small_cached_thumbnail(self):
        thumb = self.folder / 'thumb.png'
        Image.new('RGB', (25, 25), 'green').save(thumb)
        self.viewer.show(dict(self.rows[0], thumbnail=str(thumb)), 0, 2)
        self.wait_for(lambda: self.viewer.photo is not None)
        self.assertAlmostEqual(self.viewer.photo.width() / self.viewer.photo.height(), 2, delta=.03)
        self.assertGreater(self.viewer.photo.width(), 25)
        self.assertEqual(self.viewer.position.cget('text'), '1 / 2')
        self.assertEqual(str(self.viewer.previous.cget('state')), 'disabled')
        self.assertEqual(str(self.viewer.next.cget('state')), 'normal')
        self.viewer.open_original()
        self.viewer.reveal()
        self.assertEqual(self.events, [('open',), ('reveal',)])
        self.assertTrue(Path(self.rows[0]['path']).is_file())

    def test_missing_source_uses_thumbnail_and_explains_limit(self):
        row = dict(self.rows[0], path=str(self.folder / 'moved.jpg'), thumbnail=self.rows[1]['path'])
        self.viewer.show(row, 0, 1)
        self.wait_for(lambda: self.viewer._displayed_path == row['path'])
        self.assertIsNotNone(self.viewer.photo)
        self.assertIn('원본을 찾을 수 없어', self.viewer.status.cget('text'))
        self.assertEqual(str(self.viewer.open_button.cget('state')), 'disabled')
        self.assertEqual(str(self.viewer.reveal_button.cget('state')), 'normal')
        self.viewer.open_original()
        self.assertEqual(self.events, [])

    def test_missing_source_without_thumbnail_has_clear_empty_state(self):
        row = dict(self.rows[0], path=str(self.folder / 'missing.jpg'))
        self.viewer.show(row, 0, 1)
        self.wait_for(lambda: self.viewer._displayed_path == row['path'])
        self.assertIsNone(self.viewer.photo)
        self.assertIn('원본 사진을 찾을 수 없어요', self.viewer.status.cget('text'))
        self.assertEqual(str(self.viewer.next.cget('state')), 'disabled')

    def test_cloud_row_only_decodes_local_thumbnail(self):
        # The original really exists. The status must still prevent opening it.
        from photo_gallery import _decode_sources
        row = dict(self.rows[0], status=OFFLINE, thumbnail=self.rows[1]['path'])
        with patch('photo_gallery._decode_sources', wraps=_decode_sources) as decoder:
            self.viewer.show(row, 0, 1)
            self.wait_for(lambda: self.viewer.photo is not None)
        self.assertTrue(decoder.call_args_list)
        self.assertTrue(all(call.args[0] == (self.rows[1]['path'],) for call in decoder.call_args_list))
        self.assertEqual(str(self.viewer.open_button.cget('state')), 'disabled')
        self.assertIn('내려받아', self.viewer.status.cget('text'))
        with patch('photo_gallery._decode_sources') as decoder:
            picture, message, can_open, _ = _load_photo(dict(row, thumbnail=row['path']), (300, 200))
        decoder.assert_not_called()
        self.assertIsNone(picture)
        self.assertFalse(can_open)

    def test_resize_keeps_portrait_uncropped_and_actions_visible(self):
        self.viewer.show(self.rows[1], 1, 2)
        self.viewer.win.geometry('600x440')
        self.pump(.4)
        self.wait_for(lambda: self.viewer.photo is not None and self.viewer._resize_id is None)
        self.assertAlmostEqual(self.viewer.photo.width() / self.viewer.photo.height(), .5, delta=.03)
        self.assertLessEqual(self.viewer.photo.width(), self.viewer.canvas.winfo_width())
        self.assertLessEqual(self.viewer.photo.height(), self.viewer.canvas.winfo_height())
        for control in (self.viewer.open_button, self.viewer.reveal_button, self.viewer.close_button):
            self.assertTrue(control.winfo_ismapped())
            self.assertLess(control.winfo_rooty(), self.viewer.win.winfo_rooty() + self.viewer.win.winfo_height())
        self.assertEqual(self.viewer.position.cget('text'), '2 / 2')

    def test_new_selection_does_not_wait_on_old_result_or_show_stale_image(self):
        started = threading.Event()
        release = threading.Event()

        def slow_first(row, size):
            result = _load_photo(row, size)
            if row['path'] == self.rows[0]['path']:
                started.set()
                release.wait(3)
            return result

        try:
            with patch('photo_viewer._load_photo', side_effect=slow_first):
                self.viewer.show(self.rows[0], 0, 2)
                self.wait_for(started.is_set)
                self.viewer.show(self.rows[1], 1, 2)
                # The Tk loop and navigation remain responsive during decoding.
                self.assertEqual(self.viewer.move(-1), 'break')
                self.assertEqual(self.events, [('move', -1)])
                self.assertEqual(self.viewer.name.cget('text'), 'portrait.png')
                release.set()
                self.wait_for(lambda: self.viewer._displayed_path == self.rows[1]['path'])
                self.assertAlmostEqual(self.viewer.photo.width() / self.viewer.photo.height(), .5, delta=.03)
        finally:
            release.set()

    def test_escape_closes_only_viewer_once_and_navigation_obeys_bounds(self):
        self.viewer.show(self.rows[0], 0, 2)
        self.assertEqual(self.viewer.move(-1), 'break')
        self.assertEqual(self.events, [])
        self.assertEqual(self.viewer.move(1), 'break')
        self.assertEqual(self.events, [('move', 1)])
        self.assertEqual(self.viewer._escape(), 'break')
        self.viewer.close()
        self.assertEqual(self.events.count(('close',)), 1)
        self.assertTrue(self.root.winfo_exists())
        self.assertFalse(self.viewer.win.winfo_exists())
        self.assertIsNone(self.viewer._poll_id)
        self.assertIsNone(self.viewer._resize_id)
        self.pump(.05)

    def test_background_update_preserves_picture_focus_and_decode(self):
        row = dict(self.rows[0], mtime=1000, size=2000)
        self.viewer.show(row, 0, 2)
        self.wait_for(lambda: self.viewer.photo is not None)
        self.pump(.25)
        photo = self.viewer.photo
        updated = dict(row, name='배경 분석이 갱신한 제목.jpg', reason='새로 확인한 파란색 사진')
        with patch('photo_viewer._load_photo', wraps=_load_photo) as decoder, \
                patch.object(self.viewer.win, 'deiconify') as activate, \
                patch.object(self.viewer.win, 'lift') as raise_window, \
                patch.object(self.viewer.canvas, 'focus_set') as focus:
            self.viewer.show(updated, 2, 5)
            self.pump(.25)
        decoder.assert_not_called()
        activate.assert_not_called()
        raise_window.assert_not_called()
        focus.assert_not_called()
        self.assertIs(self.viewer.photo, photo)
        self.assertEqual(self.viewer.position.cget('text'), '3 / 5')
        self.assertEqual(self.viewer.name.cget('text'), updated['name'])
        self.assertEqual(self.viewer.reason.cget('text'), updated['reason'])
        self.assertEqual(str(self.viewer.previous.cget('state')), 'normal')

    def test_changed_file_metadata_reloads_same_path(self):
        row = dict(self.rows[0], mtime=1000)
        self.viewer.show(row, 0, 1)
        self.wait_for(lambda: self.viewer.photo is not None)
        previous = self.viewer.photo
        Image.new('RGB', (160, 320), 'green').save(row['path'])
        self.viewer.show(dict(row, mtime=2000), 0, 1)
        self.wait_for(lambda: self.viewer.photo is not None)
        self.assertIsNot(self.viewer.photo, previous)
        self.assertAlmostEqual(self.viewer.photo.width() / self.viewer.photo.height(), .5, delta=.03)

    def test_enlarged_text_and_long_details_preserve_photo_space(self):
        self.viewer.close()
        self.root._photo_text_scale = 1.3
        self.viewer = PhotoViewer(self.root, lambda delta: None, lambda: None, lambda: None, lambda: None)
        self.viewer.win.geometry('600x440')
        self.viewer.show(dict(self.rows[0], name=('엄청긴한글파일이름' * 20) + '.jpg',
                              reason=('파란 의자를 찾은 이유\n' * 30)), 0, 20)
        self.pump(.4)
        self.wait_for(lambda: self.viewer.photo is not None)
        self.assertEqual(tkfont.Font(root=self.viewer.win, font=self.viewer.name.cget('font')).actual()['size'], 16)
        self.assertTrue(self.viewer.name.cget('text').endswith('…'))
        self.assertTrue(self.viewer.reason.cget('text').endswith('…'))
        self.assertNotIn('\n', self.viewer.reason.cget('text'))
        self.assertGreaterEqual(self.viewer.canvas.winfo_height(), 120)
        for control in (self.viewer.open_button, self.viewer.reveal_button, self.viewer.close_button):
            self.assertTrue(control.winfo_ismapped())
            self.assertLessEqual(control.winfo_rooty() + control.winfo_height(),
                                 self.viewer.win.winfo_rooty() + self.viewer.win.winfo_height())


if __name__ == '__main__':
    unittest.main()
