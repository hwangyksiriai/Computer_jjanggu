import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from final_versions import FinalVersions


class FinalVersionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='final-versions-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = FinalVersions(self.base / 'state')

    def file(self, name, body='synthetic'):
        path = self.base / name
        path.write_text(body, encoding='utf-8')
        return path

    def test_explicit_final_survives_restart_and_does_not_modify_original(self):
        path = self.file('arbitrary name.txt')
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        self.assertEqual(self.store.describe(path)['state'], 'none')
        self.assertEqual(self.store.mark(path)['state'], 'final')
        self.assertEqual(FinalVersions(self.base / 'state').describe(path)['state'], 'final')
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertTrue(self.store.clear(path))
        self.assertFalse(self.store.clear(path))
        self.assertEqual(self.store.describe(path)['state'], 'none')

    def test_newest_date_and_final_filename_do_not_create_marks(self):
        path = self.file('보고서_최종.txt')
        self.assertEqual(self.store.describe(path)['state'], 'none')

    def test_mark_replaces_only_supplied_same_version_family(self):
        first, second = self.file('보고서_v1.txt'), self.file('보고서_v2.txt')
        unrelated = self.file('급여명세서_7월.txt')
        self.store.mark(first)
        self.store.mark(unrelated)
        self.store.mark(second, [first, unrelated])
        self.assertEqual(self.store.describe(first)['state'], 'none')
        self.assertEqual(self.store.describe(second)['state'], 'final')
        self.assertEqual(self.store.describe(unrelated)['state'], 'final')

    def test_unsupplied_same_filename_in_other_folder_is_not_overwritten(self):
        folder = self.base / 'other'; folder.mkdir()
        first = self.file('report_v1.txt')
        other = folder / 'report_v2.txt'; other.write_text('other project')
        self.store.mark(first)
        self.store.mark(other)
        self.assertEqual(self.store.describe(first)['state'], 'final')

    def test_changed_and_missing_original_keep_removable_mark(self):
        path = self.file('a.txt')
        self.store.mark(path)
        path.write_text('changed synthetic contents')
        self.assertEqual(self.store.describe(path)['state'], 'changed')
        with patch('final_versions._identity', side_effect=FileNotFoundError):
            self.assertEqual(self.store.describe(path)['state'], 'missing')
        self.assertTrue(self.store.clear(path))

    def test_missing_or_folder_cannot_be_marked(self):
        for path in (self.base, self.base / 'missing.txt'):
            with self.assertRaises(ValueError):
                self.store.mark(path)

    def test_actual_rename_and_undo_follow_confirmed_reference_mapping(self):
        source = self.file('v1.txt')
        destination = self.base / 'moved.txt'
        self.store.mark(source)
        source.rename(destination)
        self.store.remap_paths({source: destination})
        self.assertEqual(self.store.describe(source)['state'], 'none')
        self.assertEqual(self.store.describe(destination)['state'], 'final')
        destination.rename(source)
        self.store.remap_paths({destination: source})
        self.assertEqual(self.store.describe(source)['state'], 'final')

    def test_remap_to_different_content_requires_confirmation(self):
        source, target = self.file('a.txt'), self.file('b.txt', 'unrelated')
        self.store.mark(source)
        self.store.remap_paths({source: target})
        self.assertEqual(self.store.describe(target)['state'], 'changed')

    def test_failed_mark_transaction_keeps_previous_final(self):
        first, second = self.file('a_v1.txt'), self.file('a_v2.txt')
        self.store.mark(first)
        with patch('final_versions._identity', side_effect=PermissionError):
            with self.assertRaises(ValueError):
                self.store.mark(second, [first])
        self.assertEqual(self.store.describe(first)['state'], 'final')


if __name__ == '__main__':
    unittest.main()
