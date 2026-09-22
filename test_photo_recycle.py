"""Safety regressions; real recycling is opt-in and creates its own tiny photo."""
import ctypes
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

import photo_recycle as recycle


@unittest.skipUnless(os.name == 'nt', 'Windows file API')
class PhotoRecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jjanggu-recycle-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / '한글 [사진] 1.png'
        self.path.write_bytes(b'owned test image')
        info = self.path.stat()
        self.row = dict(path=str(self.path), size=info.st_size, mtime=info.st_mtime)

    def test_only_exact_absolute_photo_path(self):
        self.assertEqual(recycle._safe_path(str(self.path)), self.path)
        for value in ('photo.jpg', 'C:photo.jpg', r'\\server\share\a.jpg',
                      r'\\?\C:\a.jpg', r'C:\pics\*.jpg', r'C:\pics\a?.jpg',
                      'C:\\a.jpg\0another', r'C:\a.jpg:stream', r'C:\a.exe',
                      'C:\\folder.\\a.jpg', r'C:\..\a.jpg'):
            with self.subTest(value=value), self.assertRaises(recycle.RecycleError):
                recycle._safe_path(value)

    def test_changed_file_is_preserved(self):
        self.path.write_bytes(b'a changed test photo')
        with patch.object(recycle, '_check_volume') as volume:
            result = recycle.recycle_photo(self.row)
        self.assertFalse(result['ok'])
        self.assertIn('변경', result['error'])
        volume.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b'a changed test photo')

    def test_directory_is_preserved(self):
        folder = Path(self.temp.name) / 'folder.jpg'
        folder.mkdir()
        result = recycle.recycle_photo(dict(path=str(folder)))
        self.assertFalse(result['ok'])
        self.assertTrue(folder.is_dir())

    def test_valid_snapshot_and_second_identity_check(self):
        path, identity = recycle._validate_photo(self.row)
        self.assertEqual(path, self.path)
        self.assertEqual(recycle._validate_photo(self.row, identity)[1], identity)
        changed = identity[:2] + (identity[2] + 1, identity[3])
        with self.assertRaises(recycle.RecycleError):
            recycle._validate_photo(self.row, changed)

    def test_missing_file_returns_clear_error(self):
        result = recycle.recycle_photo(dict(path=str(Path(self.temp.name) / 'missing.jpg')))
        self.assertFalse(result['ok'])
        self.assertIn('이미 삭제', result['error'])

    def test_reparse_and_online_only_attributes_are_rejected(self):
        original = Path.lstat
        for flag in (0x400, 0x1000, 0x40000, 0x400000):
            def pretend(item, *, follow_symlinks=False):
                info = original(item)
                if item == self.path:
                    return types.SimpleNamespace(st_file_attributes=flag, st_mode=info.st_mode)
                return info
            with self.subTest(flag=flag), patch.object(Path, 'lstat', pretend):
                with self.assertRaises(recycle.RecycleError):
                    recycle._validate_photo(self.row)

    def test_reparse_parent_is_rejected(self):
        original = Path.lstat
        def pretend(item, *, follow_symlinks=False):
            info = original(item)
            if item == self.path.parent:
                return types.SimpleNamespace(st_file_attributes=0x400, st_mode=info.st_mode)
            return info
        with patch.object(Path, 'lstat', pretend), self.assertRaises(recycle.RecycleError):
            recycle._validate_photo(self.row)

    def test_api_failure_never_falls_back_to_unlink(self):
        with patch.object(recycle, '_recycle_windows', side_effect=recycle.RecycleError('휴지통 없음')):
            result = recycle.recycle_photo(self.row)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], '휴지통 없음')
        self.assertTrue(self.path.is_file())

    def test_user_cancellation_is_distinct(self):
        with patch.object(recycle, '_recycle_windows', side_effect=recycle.RecycleError('취소', cancelled=True)):
            result = recycle.recycle_photo(self.row)
        self.assertTrue(result['cancelled'])
        self.assertFalse(result['ok'])
        self.assertTrue(self.path.exists())

    def test_no_permanent_delete_flags_and_sink_guard(self):
        self.assertTrue(recycle.RECYCLE_FLAGS & recycle.FOFX_RECYCLEONDELETE)
        self.assertTrue(recycle.RECYCLE_FLAGS & recycle.FOFX_EARLYFAILURE)
        _, identity = recycle._validate_photo(self.row)
        sink = recycle._RecycleSink(self.row, identity, Mock())
        hr = sink.callbacks[11](sink.pointer, 0, None)
        self.assertEqual(hr, recycle.E_ABORT)
        self.assertIn('휴지통', sink.blocked)
        self.assertTrue(self.path.exists())

    def test_sink_rechecks_identity_and_target(self):
        _, identity = recycle._validate_photo(self.row)
        sink = recycle._RecycleSink(self.row, identity, Mock())
        with patch.object(recycle, '_shell_path', return_value=str(self.path)):
            self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 1), 0)
            self.path.write_bytes(b'changed before deletion')
            self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 1), recycle.E_ABORT)
        self.assertTrue(self.path.exists())
        sink = recycle._RecycleSink(self.row, identity, Mock())
        with patch.object(recycle, '_shell_path', return_value=str(self.path.parent / 'other.jpg')):
            self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 1), recycle.E_ABORT)
        self.assertIn('대상이 달라', sink.blocked)

    def test_shell_success_without_recycled_item_is_not_success(self):
        _, identity = recycle._validate_photo(self.row)
        sink = recycle._RecycleSink(self.row, identity, Mock())
        self.assertEqual(sink.callbacks[12](sink.pointer, 0x80, 1, 0, None), recycle.E_ABORT)
        self.assertFalse(sink.recycled_path)
        self.assertTrue(self.path.exists())

    def test_non_fixed_volume_rejected_before_shell_operation(self):
        kernel = Mock()
        kernel.GetDriveTypeW.return_value = 4  # mapped network drive
        with patch.object(ctypes, 'WinDLL', return_value=kernel), self.assertRaises(recycle.RecycleError):
            recycle._check_volume(self.path)
        self.assertTrue(self.path.exists())

    @unittest.skipUnless(os.environ.get('JJANGGU_RECYCLE_INTEGRATION') == '1', 'opt-in actual Windows Recycle Bin test')
    def test_actual_recycle_only_self_created_photo(self):
        # A valid tiny PNG, created by this test; no personal file is opened.
        import base64
        content = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+afo0AAAAASUVORK5CYII=')
        self.path.write_bytes(content)
        info = self.path.stat()
        row = dict(path=str(self.path), size=info.st_size, mtime=info.st_mtime)
        result = recycle.recycle_photo(row)
        self.assertTrue(result['ok'], result)
        self.assertFalse(self.path.exists())
        recycled = Path(result['recycled_path'])
        self.assertIn('$recycle.bin', str(recycled).lower())
        self.assertEqual(recycled.read_bytes(), content)
        # Leave the test photo in Recycle Bin. Never empty the user's bin.


if __name__ == '__main__':
    unittest.main()
