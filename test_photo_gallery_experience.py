"""Exercise the photo browsing experience against generated local photos only."""
import gc
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from photo_gallery import PAGE_SIZE, PhotoGallery


class PhotoGalleryExperienceTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.gc_was_enabled = gc.isenabled()
        gc.disable()
        self.temp = tempfile.TemporaryDirectory(prefix='jjanggu-gallery-experience-')
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))
        self.root.tk.createcommand('bgerror', lambda error: self.errors.append(str(error)))
        self.events = []
        self.rows = []
        for index in range(PAGE_SIZE + 7):
            path = self.folder / f'photo-{index:02d}.jpg'
            Image.new('RGB', (320, 220), ('#568EC1' if index % 2 else '#EBC48C')).save(path)
            self.rows.append(dict(path=str(path), name=path.name, mtime=1000 + index,
                visual=[.2] if index % 2 else [], fields={}, body='',
                reason='파란 계열의 사진 후보' if index % 2 else ''))
        self.app = SimpleNamespace(
            root=self.root, settings={'text_scale': 1}, last_submitted='사진 보여줘',
            photo_indexing=False,
            photo_library=SimpleNamespace(search_coverage={
                'roots': [str(self.folder)], 'images': len(self.rows), 'visual': 15}),
            open_file=lambda path: self.events.append(('open', path)),
            preview=lambda row: self.events.append(('preview', row['path'])),
            reveal=lambda path: self.events.append(('reveal', path)),
            refine_photo=lambda text: self.events.append(('refine', text)),
            find_similar_photo=lambda row: self.events.append(('similar', row['path'])),
            choose_photo_folder=lambda: self.events.append(('folder', None)),
            undo_photo_query=lambda: self.events.append(('back', None)),
        )
        self.gallery = PhotoGallery(self.app, self.rows)
        self.pump(.15)

    def tearDown(self):
        try:
            self.gallery.close()
            self.gallery._pool.shutdown(wait=True, cancel_futures=True)
            for identifier in self.root.tk.call('after', 'info'):
                self.root.tk.call('after', 'cancel', identifier)
            self.root.destroy()
            self.temp.cleanup()
        finally:
            self.gallery = None
            self.root = None
            gc.collect()
            if self.gc_was_enabled:
                gc.enable()

    def pump(self, seconds=.1):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertEqual(self.errors, [], 'The gallery must not hide Tk callback errors')

    def test_manual_sort_and_filter_show_the_selected_photo_on_first_page(self):
        self.gallery.paginate(1)
        self.assertEqual(self.gallery.page, 1)
        self.gallery.sort.set('최근 수정 순')
        self.gallery.refresh()
        self.assertEqual(self.gallery.page, 0)
        self.assertEqual(self.gallery.selected_path, self.rows[-1]['path'])
        self.assertEqual(self.gallery.cards[0].row['path'], self.gallery.selected_path)
        self.gallery.paginate(1)
        self.gallery.query.set('photo-0')
        self.gallery.refresh()
        self.assertEqual(self.gallery.page, 0)
        self.assertEqual(self.gallery.selected_path, self.rows[9]['path'])
        self.assertIn(self.gallery.selected_path, [card.row['path'] for card in self.gallery.cards])

    def test_automatic_results_keep_the_selected_photo_when_rank_changes(self):
        self.gallery.paginate(1)
        selected = self.gallery.selected_path
        reordered = self.rows[PAGE_SIZE:] + self.rows[:PAGE_SIZE]
        self.gallery.update_results(reordered, self.gallery.search_query)
        self.assertEqual(self.gallery.selected_path, selected)
        self.assertEqual(self.gallery.page, 0)
        self.assertIn(selected, [card.row['path'] for card in self.gallery.cards])
        self.assertFalse(self.events, 'Receiving new results must not open an external program')

    def test_saved_photos_survive_a_different_search(self):
        chosen = self.rows[3]
        self.gallery.toggle_saved(chosen['path'])
        self.gallery.search_query = '다른 사진'
        self.gallery.update_results(self.rows[20:], self.gallery.search_query)
        self.assertIn(chosen['path'], self.gallery.saved)
        self.gallery.show_saved()
        self.assertTrue(self.gallery.saved_only.get())
        self.assertEqual([row['path'] for row in self.gallery.visible], [chosen['path']])
        self.assertEqual(self.gallery.selected()['path'], chosen['path'])
        self.gallery.show_saved()
        self.assertFalse(self.gallery.saved_only.get())
        self.assertEqual([row['path'] for row in self.gallery.visible],
                         [row['path'] for row in self.rows[20:]])

    def test_removing_saved_photos_updates_selection_and_empty_state(self):
        first, second = self.rows[0]['path'], self.rows[1]['path']
        self.gallery.toggle_saved(first)
        self.gallery.toggle_saved(second)
        self.gallery.show_saved()
        self.gallery.select(first)
        self.gallery.toggle_saved(first)
        self.assertEqual([row['path'] for row in self.gallery.visible], [second])
        self.assertEqual(self.gallery.selected_path, second)
        self.gallery.toggle_saved(second)
        self.assertTrue(self.gallery.saved_only.get())
        self.assertEqual(self.gallery.visible, [])
        self.assertIsNone(self.gallery.selected())
        self.assertTrue(all(str(button.cget('state')) == 'disabled' for button in self.gallery.actions))
        self.gallery.show_saved()
        self.assertEqual(len(self.gallery.visible), len(self.rows))

    def test_space_and_double_click_route_open_big_view_without_external_app(self):
        card = self.gallery.cards[0]
        card.focus_force()
        self.pump()
        with patch.object(self.gallery, 'open_viewer') as show:
            card.event_generate('<KeyPress-space>')
            self.pump()
            self.assertEqual(show.call_count, 1)
            # Tk cannot synthesize the Double modifier. Exercise the callback
            # bound to a card double click after verifying that binding exists.
            self.assertTrue(card.bind('<Double-1>'))
            self.gallery._open_path(self.rows[1]['path'])
            self.assertEqual(show.call_count, 2)
            self.assertEqual(self.gallery.selected_path, self.rows[1]['path'])
        self.assertEqual(self.events, [])

    def test_typing_arrow_keys_does_not_change_selected_photo(self):
        self.gallery.select(self.rows[2]['path'])
        self.gallery.refine_query.set('파란 의자가 있는 사진')
        entry = self.gallery.refine_entry
        entry.focus_force()
        entry.icursor('end')
        self.pump()
        end = entry.index('insert')
        entry.event_generate('<KeyPress-Left>')
        self.pump()
        self.assertEqual(entry.index('insert'), end - 1)
        self.assertEqual(self.gallery.selected_path, self.rows[2]['path'])
        entry.event_generate('<KeyPress-Right>')
        self.pump()
        self.assertEqual(entry.index('insert'), end)
        self.assertEqual(self.gallery.selected_path, self.rows[2]['path'])
        self.assertEqual(self.events, [])

    def test_big_view_tracks_navigation_and_closes_back_to_gallery(self):
        self.gallery.paginate(1)
        self.gallery.open_viewer()
        self.pump(.15)
        viewer = self.gallery.viewer
        self.assertIsNotNone(viewer)
        self.assertTrue(viewer.win.winfo_exists())
        self.assertEqual(viewer.row['path'], self.rows[PAGE_SIZE]['path'])
        self.gallery.move_selection(-1)
        self.pump()
        self.assertEqual(self.gallery.page, 0)
        self.assertEqual(viewer.row['path'], self.rows[PAGE_SIZE - 1]['path'])
        self.assertEqual(viewer.row['path'], self.gallery.selected_path)
        self.gallery.close_viewer()
        self.assertFalse(viewer.win.winfo_exists())
        self.assertIsNone(self.gallery.viewer)
        self.assertTrue(self.gallery.win.winfo_exists())
        self.assertEqual(self.gallery.selected_path, self.rows[PAGE_SIZE - 1]['path'])
        self.assertEqual(self.events, [])

    def test_no_results_close_old_big_view_instead_of_showing_a_stale_photo(self):
        self.gallery.open_viewer()
        self.pump(.1)
        viewer = self.gallery.viewer
        self.gallery.update_results([], self.gallery.search_query)
        self.pump(.1)
        self.assertEqual(self.gallery.visible, [])
        self.assertIsNone(self.gallery.selected())
        self.assertIsNone(self.gallery.viewer)
        self.assertFalse(viewer.win.winfo_exists())
        self.assertTrue(self.gallery.win.winfo_exists())

    def test_closing_after_refinement_does_not_leave_a_broken_ui_callback(self):
        self.gallery.refine_query.set('파란색인 것만')
        self.gallery.refine()
        self.gallery.close()
        self.pump(1.35)
        self.assertEqual(self.events, [('refine', '파란색인 것만')])

    def test_compact_filters_apply_and_escape_returns_to_the_gallery(self):
        self.gallery.win.geometry('640x460')
        self.pump(.15)
        self.gallery.toggle_filters()
        dialog = self.gallery.filter_dialog
        self.pump(.1)
        self.assertTrue(dialog.winfo_exists())
        self.gallery.toggle_filters()
        self.assertIs(self.gallery.filter_dialog, dialog)
        self.gallery.query.set('photo-03')

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        apply_button = next(widget for widget in descendants(dialog)
                            if 'text' in widget.keys() and widget.cget('text') == '조건 적용')
        apply_button.invoke()
        self.pump(.2)
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual([row['path'] for row in self.gallery.visible], [self.rows[3]['path']])
        self.assertTrue(self.gallery.win.winfo_exists())
        self.gallery.toggle_filters()
        dialog = self.gallery.filter_dialog
        dialog.focus_force()
        self.pump(.1)
        dialog.event_generate('<KeyPress-Escape>')
        self.pump(.1)
        self.assertFalse(dialog.winfo_exists())
        self.assertTrue(self.gallery.win.winfo_exists())
        self.assertEqual(self.events, [])

    def test_compact_window_still_prioritizes_scrollable_photos(self):
        self.assertFalse(self.gallery._filters_shown)
        self.app.settings['text_scale'] = 1.3
        self.gallery.apply_readability()
        self.gallery.win.geometry('640x460')
        self.pump(.25)
        self.assertFalse(self.gallery._filters_shown)
        self.assertGreaterEqual(self.gallery.canvas.winfo_height(), 150)
        self.assertGreater(self.gallery.canvas.bbox('all')[3], self.gallery.canvas.winfo_height())
        self.gallery.move_selection(PAGE_SIZE - 1)
        self.pump()
        self.assertGreater(self.gallery.canvas.yview()[0], 0)
        self.assertEqual(self.gallery.selected_path, self.rows[PAGE_SIZE - 1]['path'])


if __name__ == '__main__':
    unittest.main()
