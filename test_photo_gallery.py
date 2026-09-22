import tempfile
from pathlib import Path
import time
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image
from photo_gallery import PhotoGallery, PAGE_SIZE, _decode


class PhotoGalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.events = []
        self.rows = []
        for i in range(29):
            path = self.folder / f'chair-{i:02d}.jpg'
            Image.new('RGB', (600, 400), ('blue' if i % 2 else 'orange')).save(path)
            self.rows.append(dict(path=str(path), name=path.name, mtime=1000+i, visual=[.2] if i % 2 else [],
                                  reason='파란 의자와 모습이 비슷한 후보' if i % 2 else '', fields={}, body=''))
        self.app = SimpleNamespace(
            root=self.root, settings={'text_scale': 1}, last_submitted='의자가 있는 사진', indexing=False,
            library=SimpleNamespace(search_coverage={'images': 0}),
            active_library=SimpleNamespace(search_coverage={'roots': [str(self.folder)], 'images': 29, 'visual': 14}),
            open_file=lambda value: self.events.append(('open', value)),
            preview=lambda value: self.events.append(('preview', value['path'])),
            reveal=lambda value: self.events.append(('reveal', value)),
            refine_photo=lambda value: self.events.append(('refine', value)),
            find_similar_photo=lambda value: self.events.append(('similar', value['path'])),
            choose_photo_folder=lambda: self.events.append(('folder', None)),
            undo_photo_query=lambda: self.events.append(('back', None)),
            cancel_photo_search=lambda: self.events.append(('stop', None)),
            resume_photo_analysis=lambda: self.events.append(('resume', None)),
        )
        self.gallery = PhotoGallery(self.app, self.rows)
        self.pump(.12)

    def tearDown(self):
        self.gallery.close()
        self.root.destroy()
        self.temp.cleanup()

    def pump(self, seconds=.2):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(.01)

    def test_first_page_has_large_thumbnails_and_active_scope(self):
        self.pump(.35)
        self.assertEqual(len(self.gallery.cards), PAGE_SIZE)
        self.assertGreater(len(self.gallery.photos), 0)
        self.assertTrue(self.gallery.preview_photo)
        self.assertIn('14/29', self.gallery.coverage.cget('text'))
        self.assertIn('1–24 / 29', self.gallery.count.cget('text'))
        self.assertGreaterEqual(self.gallery.columns, 2)
        self.assertIn(self.gallery, self.app.result_browsers)

    def test_pagination_keyboard_and_native_file_actions(self):
        self.gallery.paginate(1)
        self.pump(.1)
        self.assertEqual(len(self.gallery.cards), 5)
        self.assertEqual(self.gallery.selected()['path'], self.rows[24]['path'])
        self.gallery.move_selection(-1)
        self.assertEqual(self.gallery.page, 0)
        self.assertEqual(self.gallery.selected()['path'], self.rows[23]['path'])
        self.gallery.open()
        self.gallery.open(True)
        self.gallery.reveal()
        self.assertEqual(self.events, [('open', self.rows[23]['path']), ('preview', self.rows[23]['path']), ('reveal', self.rows[23]['path'])])

    def test_incremental_update_keeps_selection_filter_sort_and_page(self):
        self.gallery.sort.set('최근 수정 순')
        self.gallery.query.set('chair')
        self.gallery.refresh()
        self.gallery.paginate(1)
        selected = self.gallery.selected_path
        extra = dict(self.rows[-1], path=str(self.folder / 'chair-new.jpg'), name='chair-new.jpg', mtime=2000)
        self.gallery.update_results([extra] + self.rows, self.gallery.search_query)
        self.assertEqual(self.gallery.selected_path, selected)
        self.assertEqual(self.gallery.page, 1)
        self.assertEqual(self.gallery.query.get(), 'chair')
        self.assertEqual(self.gallery.sort.get(), '최근 수정 순')
        self.gallery.update_results([], 'unrelated query')
        self.assertEqual(len(self.gallery.rows), 30)

    def test_refinement_and_similar_callbacks_pass_user_intent(self):
        self.gallery.refine_query.set('사람이 없는 것만')
        self.gallery.refine()
        self.gallery.find_similar()
        self.assertEqual(self.events, [('refine', '사람이 없는 것만'), ('similar', self.rows[0]['path'])])

    def test_analysis_filter_and_empty_state_remain_reversible(self):
        self.gallery.mode.set('모습 분석 대기')
        self.gallery.refresh()
        self.assertEqual(len(self.gallery.visible), 15)
        self.gallery.query.set('does-not-exist')
        self.gallery.refresh()
        self.assertEqual(self.gallery.visible, [])
        self.assertIsNone(self.gallery.selected())
        self.assertTrue(all(str(button.cget('state')) == 'disabled' for button in self.gallery.actions))
        self.gallery.reset()
        self.assertEqual(len(self.gallery.visible), 29)

    def test_small_window_and_large_fonts_keep_cards_scrollable(self):
        self.app.settings['text_scale'] = 1.3
        self.gallery.apply_readability()
        self.gallery.win.geometry('640x460')
        self.pump(.3)
        self.assertFalse(self.gallery._filters_shown)
        self.assertEqual(len(self.gallery.pane.panes()), 1)
        self.assertTrue(self.gallery.mobile_actions.winfo_ismapped())
        self.assertGreater(self.gallery.canvas.winfo_height(), 150)
        self.assertGreater(self.gallery.canvas.bbox('all')[3], self.gallery.canvas.winfo_height())
        self.gallery.canvas.yview_moveto(1)
        self.pump(.1)
        self.assertGreater(self.gallery.canvas.yview()[0], 0)
        self.gallery.move_selection(23)
        self.pump(.1)
        self.assertEqual(self.gallery.selected_path, self.rows[23]['path'])
        self.assertGreater(self.gallery.canvas.yview()[0], 0)

    def test_missing_thumbnail_falls_back_and_unreadable_image_is_explicit(self):
        row = dict(self.rows[0], thumbnail=str(self.folder / 'missing.jpg'))
        self.gallery.update_results([row], self.gallery.search_query)
        self.pump(.25)
        self.assertIn(row['path'], self.gallery.photos)
        bad = dict(row, path=str(self.folder / 'broken.jpg'), name='broken.jpg', thumbnail='')
        self.gallery.update_results([bad], self.gallery.search_query)
        self.pump(.2)
        self.assertIn('미리보기를 만들 수 없어요', self.gallery.cards[0].image_label.cget('text'))
        self.assertEqual(str(self.gallery.actions[0].cget('state')), 'normal')

    def test_close_cancels_jobs_and_unregisters(self):
        self.gallery.close()
        self.assertNotIn(self.gallery, self.app.result_browsers)
        self.assertFalse(self.gallery.win.winfo_exists())
        self.root.update()

    def test_unsupported_photos_and_capture_dates_are_visible(self):
        row = dict(self.rows[0], path=str(self.folder / 'camera.heic'), name='camera.heic',
                   fields={'taken_date': '2024-03-09', 'photo_error': '사진 형식을 읽지 못했어요'})
        self.gallery.update_results([row], self.gallery.search_query)
        self.pump(.2)
        self.assertEqual(len(self.gallery.rows), 1)
        self.assertEqual(self.gallery.date.cget('text'), '찍은 날 2024.03.09')
        self.assertIn('사진 형식을 읽지 못했어요', self.gallery.reason.cget('text'))

    def test_cloud_placeholder_does_not_trigger_original_file_read(self):
        row = dict(self.rows[0], status='PC에 내려받지 않은 사진')
        with patch('photo_gallery._decode_sources') as decode:
            self.gallery.update_results([row], self.gallery.search_query)
            self.pump(.15)
        decode.assert_not_called()
        self.assertIn('PC에 내려받지 않은 사진', self.gallery.reason.cget('text'))


class ThumbnailTests(unittest.TestCase):
    def test_large_source_is_downsampled_and_rotated_for_display(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rotated.jpg'
            image = Image.new('RGB', (1200, 600), 'blue')
            exif = Image.Exif()
            exif[274] = 6
            image.save(path, exif=exif)
            picture = _decode(path, (210, 160))
            self.assertLessEqual(picture.width, 210)
            self.assertLessEqual(picture.height, 160)
            self.assertGreater(picture.height, picture.width)


if __name__ == '__main__':
    unittest.main()
