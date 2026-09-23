"""Synthetic folder selection and app-owned file reference regressions."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from core import Library
from file_memory import FileMemory
from knowledge import Knowledge


class LibraryFixture(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent / '.local' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='search-scope-', dir=base)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.child = self.source / 'nested'
        self.child.mkdir()
        self.other = self.root / 'other'
        self.other.mkdir()
        self.library = Knowledge(self.root / 'state')

    def file(self, root, name='note.txt', body='메모 테스트 본문'):
        path = root / name
        path.write_text(body, encoding='utf-8')
        stamp = time.time() - 60
        os.utime(path, (stamp, stamp))
        return path

    def paths(self, rows):
        return {Path(row['path']) for row in rows}


class SearchScopeTests(LibraryFixture):
    def test_none_searches_all_but_empty_roots_search_nothing(self):
        first = self.file(self.source)
        other = self.file(self.other)
        Library.index(self.library, [self.source, self.other])
        self.assertEqual(self.paths(self.library.search(roots=None)[0]), {first, other})
        self.assertEqual(self.paths(self.library.rows(None)), {first, other})
        self.assertEqual(self.library.stats(None)['total'], 2)
        self.assertEqual(self.paths(self.library.smart_search('', None)[0]), {first, other})
        for roots in ([], (), iter(())):
            self.assertEqual(self.library.search('메모', roots=roots)[0], [])
        for roots in ([], (), iter(())):
            self.assertEqual(self.library.rows(roots), [])
        self.assertEqual(self.library.stats([]), {'total': 0, 'pending': 0})

    def test_empty_smart_search_clears_coverage_without_running_ai(self):
        self.file(self.source)
        Library.index(self.library, [self.source])
        self.library.smart_search('', [self.source])
        self.library.ai = Mock()
        rows, query = self.library.smart_search('급여명세서', [])
        self.assertEqual((rows, query), ([], '급여명세서'))
        self.assertEqual(self.library.search_coverage['roots'], [])
        self.library.ai.ready_for.assert_not_called()
        self.library.ai.embed.assert_not_called()
        self.library.ai.call.assert_not_called()

    def test_unchanged_child_file_follows_parent_and_subfolder_selection(self):
        nested = self.file(self.child)
        sibling = self.file(self.source, 'outside.txt')
        Library.index(self.library, [self.source])
        with patch('core.extract', side_effect=AssertionError('Unchanged text must be reused')):
            Library.index(self.library, [self.child])
            self.assertEqual(self.paths(self.library.rows([self.child])), {nested})
            self.assertEqual(self.library.stats([self.child])['total'], 1)
            self.assertEqual(self.paths(self.library.smart_search('메모', [self.child])[0]), {nested})
            Library.index(self.library, [self.source])
            self.assertEqual(self.paths(self.library.rows([self.source])), {nested, sibling})
            self.assertEqual(self.library.stats([self.source])['total'], 2)
            self.assertEqual(self.paths(self.library.smart_search('메모', [self.source])[0]), {nested, sibling})

    def test_incremental_index_updates_scope_without_reextracting(self):
        nested = self.file(self.child)
        Library.index(self.library, [self.source])
        cached = []
        with patch('core.extract', side_effect=AssertionError('Unchanged text must be reused')):
            Library.index(self.library, [self.child], only_paths={str(nested)}, on_file=cached.append)
        self.assertEqual(cached[0]['scope'], str(self.child))
        self.assertEqual(self.paths(self.library.rows([self.child])), {nested})

    def test_removed_root_cached_files_stay_outside_current_selection(self):
        self.file(self.source)
        remaining = self.file(self.other)
        Library.index(self.library, [self.source, self.other])
        self.assertEqual(self.paths(self.library.rows([self.other])), {remaining})
        self.assertEqual(self.paths(self.library.search(roots=[self.other])[0]), {remaining})
        self.assertEqual(self.paths(self.library.smart_search('메모', [self.other])[0]), {remaining})


class MoveMemoryTests(LibraryFixture):
    def setUp(self):
        super().setUp()
        self.vault = self.root / 'vault'
        self.memory = FileMemory(self.library.data)
        self.collection = self.memory.create_collection('업무 모음')

    def remember(self, *paths):
        for path in paths:
            self.memory.record_open(path)
        self.memory.add_files(self.collection, paths)

    def reference_paths(self):
        return (self.paths(self.memory.recent(include_missing=True)),
                self.paths(self.memory.collection_files(self.collection)))

    def test_actual_move_collision_and_undo_update_recent_and_collection(self):
        original = self.file(self.source)
        self.remember(original)
        Library.index(self.library, [self.source])
        plan = self.library.plan(self.source, self.vault)
        occupied = Path(plan[0]['dest'])
        occupied.parent.mkdir(parents=True)
        occupied.write_text('기존 파일', encoding='utf-8')
        done, errors = self.library.move(plan, self.source, self.vault)
        self.assertEqual(errors, [])
        destination = Path(done[0])
        self.assertNotEqual(destination, occupied)
        self.assertFalse(original.exists())
        self.assertEqual(self.reference_paths(), ({destination}, {destination}))
        undone, errors = self.library.undo()
        self.assertEqual((undone, errors), ([str(original)], []))
        self.assertEqual(self.reference_paths(), ({original}, {original}))
        self.assertEqual(occupied.read_text(encoding='utf-8'), '기존 파일')

    def test_partial_move_and_undo_remap_only_successes(self):
        first = self.file(self.source, 'first.txt')
        second = self.file(self.source, 'second.txt')
        self.remember(first, second)
        plan = self.library.plan(self.source, self.vault)
        second.write_text('미리보기 뒤 변경', encoding='utf-8')
        done, errors = self.library.move(plan, self.source, self.vault)
        self.assertEqual(len(done), 1)
        self.assertEqual(len(errors), 1)
        destination = Path(done[0])
        self.assertEqual(self.reference_paths(), ({destination, second}, {destination, second}))
        first.write_text('같은 이름의 새 파일', encoding='utf-8')
        undone, errors = self.library.undo()
        self.assertEqual(undone, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.reference_paths(), ({destination, second}, {destination, second}))

    def test_old_move_is_not_replayed_when_source_filename_reappears(self):
        original = self.file(self.source, 'reused.txt')
        self.remember(original)
        done, errors = self.library.move(self.library.plan(self.source, self.vault), self.source, self.vault)
        self.assertFalse(errors)
        old_destination = Path(done[0])
        self.file(self.source, original.name, '같은 이름의 새 파일')
        self.remember(original)
        other = self.file(self.source, 'later.txt')
        self.remember(other)
        plan = [item for item in self.library.plan(self.source, self.vault) if item['source'] == str(other)]
        done, errors = self.library.move(plan, self.source, self.vault)
        self.assertFalse(errors)
        later_destination = Path(done[0])
        expected = {original, old_destination, later_destination}
        self.assertEqual(self.reference_paths(), (expected, expected))
        undone, errors = self.library.undo()
        self.assertEqual((undone, errors), ([str(other)], []))
        expected = {original, old_destination, other}
        self.assertEqual(self.reference_paths(), (expected, expected))

    def test_partial_undo_preserves_failed_reference_and_restores_success(self):
        first = self.file(self.source, 'first.txt')
        second = self.file(self.source, 'second.txt')
        self.remember(first, second)
        done, errors = self.library.move(self.library.plan(self.source, self.vault), self.source, self.vault)
        self.assertFalse(errors)
        first_destination = next(Path(path) for path in done if Path(path).name == first.name)
        first.write_text('복원을 막는 새 파일', encoding='utf-8')
        undone, errors = self.library.undo()
        self.assertEqual(undone, [str(second)])
        self.assertEqual(len(errors), 1)
        expected = {first_destination, second}
        self.assertEqual(self.reference_paths(), (expected, expected))

    def test_old_undo_is_not_replayed_for_new_file_at_previous_destination(self):
        original = self.file(self.source, 'reused.txt')
        self.remember(original)
        done, _ = self.library.move(self.library.plan(self.source, self.vault), self.source, self.vault)
        old_destination = Path(done[0])
        self.library.undo()
        self.file(old_destination.parent, old_destination.name, '보관함의 새 파일')
        self.remember(old_destination)
        other = self.file(self.source, 'later.txt')
        self.remember(other)
        plan = [item for item in self.library.plan(self.source, self.vault) if item['source'] == str(other)]
        done, errors = self.library.move(plan, self.source, self.vault)
        self.assertFalse(errors)
        expected = {original, old_destination, Path(done[0])}
        self.assertEqual(self.reference_paths(), (expected, expected))


if __name__ == '__main__':
    unittest.main(verbosity=2)
