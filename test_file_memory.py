from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from file_memory import FileMemory


class FileMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='jjanggu-file-memory-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.memory = FileMemory(self.root / 'state')

    def file(self, name):
        path = self.root / name
        path.write_text('Synthetic file content: ' + name, encoding='utf-8')
        return path

    def test_reopening_moves_one_reference_to_front_and_persists(self):
        first, second = self.file('급여.pdf'), self.file('notes.txt')
        with patch('file_memory.time.time', return_value=1234):
            self.memory.record_open(first)
            self.memory.record_open(second)
            self.memory.record_open(first.parent / '.' / first.name)
        reopened = FileMemory(self.memory.data)
        self.assertEqual([row['path'] for row in reopened.recent()], [str(first), str(second)])
        self.assertEqual(reopened.recent()[0]['last_opened'], 1234)
        self.assertTrue(reopened.recent()[0]['available'])

    def test_recent_cap_and_missing_files_do_not_hide_older_valid_results(self):
        files = [self.file(f'file-{number}.txt') for number in range(105)]
        for path in files:
            self.memory.record_open(path)
        self.assertEqual(len(self.memory.recent(1000)), 100)
        files[-1].unlink()
        self.assertEqual(self.memory.recent(1)[0]['path'], str(files[-2]))
        self.assertFalse(self.memory.recent(1, include_missing=True)[0]['available'])
        self.assertFalse(self.memory.record_open(files[-1]))
        self.assertFalse(self.memory.record_open(self.root))
        self.assertEqual(self.memory.recent(0), [])

    def test_collection_references_are_persistent_deduplicated_and_never_delete_files(self):
        first, second = self.file('one.txt'), self.file('two.txt')
        collection = self.memory.create_collection('  이번 프로젝트  ')
        self.assertEqual(self.memory.add_files(collection, [first, first, second]), 2)
        self.assertEqual(self.memory.add_files(collection, [second]), 0)
        self.memory.rename_collection(collection, '회사 자료')
        reopened = FileMemory(self.memory.data)
        self.assertEqual(reopened.list_collections()[0]['name'], '회사 자료')
        self.assertEqual(reopened.list_collections()[0]['file_count'], 2)
        before = first.read_bytes(), second.read_bytes()
        self.assertEqual(reopened.remove_files(collection, [first, first]), 1)
        self.assertEqual(reopened.list_collections()[0]['file_count'], 1)
        self.assertTrue(reopened.delete_collection(collection))
        self.assertEqual(reopened.collection_files(collection), [])
        self.assertEqual((first.read_bytes(), second.read_bytes()), before)

    def test_names_are_validated_and_duplicate_names_are_explicit(self):
        collection = self.memory.create_collection('Project A')
        with self.assertRaises(ValueError):
            self.memory.create_collection('project a')
        for value in ('', '   ', 'x' * 81):
            with self.assertRaises(ValueError):
                self.memory.create_collection(value)
        with self.assertRaises(ValueError):
            self.memory.rename_collection(9999, 'Gone')
        self.assertEqual(self.memory.add_files(collection, [self.root / 'missing.txt']), 0)
        with self.assertRaises(ValueError):
            self.memory.add_files(9999, [self.file('valid.txt')])

    def test_missing_collection_file_stays_visible_but_unavailable(self):
        path = self.file('removed.txt')
        collection = self.memory.create_collection('보관할 자료')
        self.memory.add_files(collection, [path])
        path.unlink()
        rows = self.memory.collection_files(collection)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['available'])
        self.assertEqual(self.memory.collection_files(collection, include_missing=False), [])
        self.assertEqual(self.memory.list_collections()[0]['file_count'], 1)

    def test_app_move_and_undo_keep_recent_and_collection_links(self):
        original = self.file('original.txt')
        destination = self.root / 'moved.txt'
        collection = self.memory.create_collection('모음')
        self.memory.record_open(original)
        self.memory.add_files(collection, [original])
        original.rename(destination)
        self.memory.remap_paths({original: destination})
        self.assertEqual(self.memory.recent()[0]['path'], str(destination))
        self.assertEqual(self.memory.collection_files(collection)[0]['path'], str(destination))
        destination.rename(original)
        self.memory.remap_paths({destination: original})
        self.assertEqual(self.memory.recent()[0]['path'], str(original))
        self.assertTrue(self.memory.collection_files(collection)[0]['available'])

    def test_remap_chains_use_original_snapshot_and_do_not_move_files(self):
        first, second, third = (self.file(name) for name in ('one.txt', 'two.txt', 'three.txt'))
        collection = self.memory.create_collection('연결')
        self.memory.add_files(collection, [first, second])
        self.memory.record_open(first)
        self.memory.record_open(second)
        before = {path: path.read_bytes() for path in (first, second, third)}
        self.memory.remap_paths({first: second, second: third})
        self.assertEqual({row['path'] for row in self.memory.collection_files(collection)}, {str(second), str(third)})
        self.assertEqual([row['path'] for row in self.memory.recent()], [str(third), str(second)])
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_remap_collision_merges_references_and_keeps_latest_open(self):
        first, second = self.file('first.txt'), self.file('second.txt')
        collection = self.memory.create_collection('합치기')
        self.memory.add_files(collection, [first, second])
        self.memory.record_open(second)
        self.memory.record_open(first)
        self.memory.remap_paths({first: second})
        self.assertEqual(len(self.memory.recent()), 1)
        self.assertEqual(self.memory.recent()[0]['path'], str(second))
        self.assertEqual(len(self.memory.collection_files(collection)), 1)

    def test_parallel_opens_and_adds_keep_database_consistent(self):
        paths = [self.file(f'parallel-{number}.txt') for number in range(12)]
        collection = self.memory.create_collection('동시 작업')
        def remember(path):
            self.memory.record_open(path)
            self.memory.add_files(collection, [path])
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(remember, paths * 2))
        self.assertEqual(len(self.memory.recent()), len(paths))
        self.assertEqual(self.memory.list_collections()[0]['file_count'], len(paths))

    def test_pins_persist_deduplicate_and_keep_initial_order_without_touching_originals(self):
        first,second=self.file('급여 {6월} A.pdf'),self.file('회사 자료.txt')
        before={path:(path.read_bytes(),path.stat().st_mtime_ns) for path in (first,second)}
        with patch('file_memory.time.time',return_value=100):
            self.assertTrue(self.memory.set_pinned(first))
        with patch('file_memory.time.time',return_value=200):
            self.assertTrue(self.memory.set_pinned(second))
            self.assertTrue(self.memory.set_pinned(first.parent/'.'/first.name))
            if os.name=='nt':self.memory.set_pinned(str(first).upper())
        reopened=FileMemory(self.memory.data)
        rows=reopened.pinned_files()
        self.assertEqual([os.path.normcase(row['path']) for row in rows],
                         [os.path.normcase(str(second)),os.path.normcase(str(first))])
        self.assertEqual([row['pinned_at'] for row in rows],[200,100])
        self.assertTrue(reopened.is_pinned(first))
        self.assertTrue(all(row['available'] for row in rows))
        self.assertEqual(len(reopened.pinned_files(limit=1)),1)
        self.assertEqual(reopened.pinned_files(limit=0),[])
        self.assertFalse(reopened.set_pinned(first,False))
        self.assertFalse(reopened.is_pinned(first))
        self.assertEqual({path:(path.read_bytes(),path.stat().st_mtime_ns) for path in before},before)

    def test_missing_pin_remains_visible_and_removable_and_does_not_hide_available_limit(self):
        existing,missing=self.file('existing.txt'),self.file('later-gone.txt')
        self.memory.set_pinned(existing);self.memory.set_pinned(missing)
        missing.unlink()
        rows=self.memory.pinned_files()
        self.assertFalse(rows[0]['available'])
        self.assertTrue(self.memory.is_pinned(missing))
        self.assertEqual(self.memory.pinned_files(limit=1,include_missing=False)[0]['path'],str(existing))
        with self.assertRaises(ValueError):self.memory.set_pinned(missing)
        self.assertTrue(self.memory.is_pinned(missing),'failed add must preserve existing reference')
        self.assertFalse(self.memory.set_pinned(missing,False))
        self.assertFalse(self.memory.set_pinned(missing,False))
        self.assertFalse(self.memory.is_pinned(missing))
        with self.assertRaises(ValueError):self.memory.set_pinned(self.root)
        self.assertEqual(len(self.memory.pinned_files()),1)

    def test_photo_favorites_persist_bounded_metadata_and_preserve_originals(self):
        path=self.file('한글 사진 {1} 😀.png')
        before=path.read_bytes(),path.stat().st_mtime_ns
        row=dict(path=str(path),name='stale display name',mtime=float('nan'),size=12,
                 thumbnail=self.root/'preview.png',width=800,height=600,status='사진 미리보기 준비됨',
                 reason='a'*1500,category='사진',group='관련 후보',body='DO NOT SAVE BODY',
                 embedding=[0.1]*100,visual=[1.0]*100,detected_objects=[{'label':'cat'}],
                 tags=['private project'],fields={'taken_date':'2025-12-24','objects':['cat'],
                                               'photo_width':800,'private':'DO NOT SAVE FIELDS'})
        self.assertTrue(self.memory.save_photo(row))
        reopened=FileMemory(self.memory.data)
        saved=reopened.saved_photos()[0]
        self.assertEqual(saved['path'],str(path))
        self.assertEqual(saved['name'],path.name)
        self.assertEqual(saved['thumbnail'],str(self.root/'preview.png'))
        self.assertEqual(saved['width'],800)
        self.assertEqual(saved['height'],600)
        self.assertEqual(saved['fields'],{'taken_date':'2025-12-24'})
        self.assertEqual(len(saved['reason']),1000)
        self.assertTrue(saved['available'])
        self.assertEqual(saved['mtime'],path.stat().st_mtime)
        self.assertEqual(saved['saved_size'],path.stat().st_size)
        self.assertEqual(saved['saved_mtime_ns'],path.stat().st_mtime_ns)
        for key in ('body','embedding','visual','detected_objects','tags'):
            self.assertNotIn(key,saved)
        with reopened._connection() as connection:
            raw=connection.execute('SELECT metadata FROM saved_photos').fetchone()[0]
        self.assertNotIn('DO NOT SAVE',raw)
        self.assertLess(len(raw),4000)
        self.assertEqual((path.read_bytes(),path.stat().st_mtime_ns),before)
        self.assertEqual(row['name'],'stale display name','saving must not mutate its input row')

    def test_photo_repeated_save_refreshes_snapshot_without_reordering_or_duplication(self):
        first,second=self.file('사진 A.png'),self.file('사진 B.png')
        with patch('file_memory.time.time',return_value=100):self.memory.save_photo({'path':first})
        with patch('file_memory.time.time',return_value=200):self.memory.save_photo({'path':second})
        with patch('file_memory.time.time',return_value=300):
            self.memory.save_photo({'path':first.parent/'.'/first.name,'reason':'새 안내'})
            if os.name=='nt':self.memory.save_photo({'path':str(first).upper(),'reason':'새 안내'})
        rows=FileMemory(self.memory.data).saved_photos()
        self.assertEqual([os.path.normcase(row['path']) for row in rows],
                         [os.path.normcase(str(second)),os.path.normcase(str(first))])
        self.assertEqual([row['saved_at'] for row in rows],[200,100])
        self.assertEqual(rows[1]['reason'],'새 안내')

    def test_missing_photos_remain_visible_and_can_be_unsaved(self):
        path=self.file('gone-photo.png')
        self.memory.save_photo({'path':path})
        path.unlink()
        rows=FileMemory(self.memory.data).saved_photos()
        self.assertEqual(len(rows),1)
        self.assertFalse(rows[0]['available'])
        self.assertEqual(rows[0]['unavailable_reason'],'파일이 없거나 읽을 수 없어요')
        self.assertEqual(self.memory.saved_photos(include_missing=False),[])
        with self.assertRaises(ValueError):self.memory.save_photo({'path':path})
        self.assertTrue(self.memory.unsave_photo(path))
        self.assertFalse(self.memory.unsave_photo(path))
        self.assertEqual(self.memory.saved_photos(),[])

    def test_invalid_pin_and_photo_inputs_do_not_modify_saved_state(self):
        path=self.file('kept.png')
        self.memory.set_pinned(path);self.memory.save_photo({'path':path})
        for value in (None,'',self.root,self.root/'missing.png'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):self.memory.set_pinned(value)
                with self.assertRaises(ValueError):self.memory.save_photo({'path':value})
        for value in (None,[],str(path)):
            with self.assertRaises(ValueError):self.memory.save_photo(value)
        self.assertEqual(len(self.memory.pinned_files()),1)
        self.assertEqual(len(self.memory.saved_photos()),1)

    def test_reused_photo_path_is_not_treated_as_the_saved_original(self):
        path=self.file('favorite.png')
        self.memory.save_photo({'path':path})
        before=path.stat()
        path.rename(self.root/'original-still-exists.png')
        path.write_bytes(b'x'*before.st_size)
        os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        row=FileMemory(self.memory.data).saved_photos()[0]
        self.assertFalse(row['available'])
        self.assertEqual(row['unavailable_reason'],'원본이 바뀌었어요')
        self.assertEqual(self.memory.saved_photos(include_missing=False),[])
        self.assertTrue(self.memory.unsave_photo(path))
        self.assertEqual(path.read_bytes(),b'x'*before.st_size)

    def test_editing_saved_photo_marks_it_changed_until_explicitly_saved_again(self):
        path=self.file('edited.png')
        self.memory.save_photo({'path':path})
        path.write_bytes(b'new different-sized bytes')
        self.assertFalse(self.memory.saved_photos()[0]['available'])
        self.memory.save_photo({'path':path})
        self.assertTrue(self.memory.saved_photos()[0]['available'])

    def test_old_database_migration_keeps_recent_and_collections_untouched(self):
        data=self.root/'legacy-state';data.mkdir()
        path=self.file('옛날 자료.pdf')
        key=os.path.normcase(str(path))
        with closing(sqlite3.connect(data/'file-memory.sqlite3')) as connection,connection:
            connection.executescript('''
                CREATE TABLE recent_files(path_key TEXT PRIMARY KEY,path TEXT NOT NULL,
                    last_opened REAL NOT NULL,open_order INTEGER NOT NULL);
                CREATE TABLE collections(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,created REAL NOT NULL,updated REAL NOT NULL);
                CREATE TABLE collection_files(collection_id INTEGER NOT NULL,path_key TEXT NOT NULL,
                    path TEXT NOT NULL,added REAL NOT NULL,PRIMARY KEY(collection_id,path_key));
            ''')
            connection.execute('INSERT INTO recent_files VALUES(?,?,?,?)',(key,str(path),12,4))
            connection.execute('INSERT INTO collections VALUES(1,?,?,?,?)',('회사 모음','회사 모음',10,11))
            connection.execute('INSERT INTO collection_files VALUES(1,?,?,?)',(key,str(path),11))
        migrated=FileMemory(data)
        self.assertEqual(migrated.recent()[0]['last_opened'],12)
        self.assertEqual(migrated.list_collections()[0]['name'],'회사 모음')
        self.assertEqual(migrated.collection_files(1)[0]['added'],11)
        self.assertEqual(migrated.pinned_files(),[])
        self.assertEqual(migrated.saved_photos(),[])
        migrated.set_pinned(path);migrated.save_photo({'path':path})
        reopened=FileMemory(data)
        self.assertTrue(reopened.is_pinned(path))
        self.assertTrue(reopened.saved_photos()[0]['available'])
        self.assertEqual(reopened.list_collections()[0]['file_count'],1)

    def test_app_move_and_undo_preserve_pin_photo_identity_and_metadata(self):
        source=self.file('original.png');destination=self.root/'renamed.png'
        self.memory.set_pinned(source)
        self.memory.save_photo({'path':source,'reason':'내가 고른 사진'})
        identity={key:value for key,value in self.memory.saved_photos()[0].items() if key.startswith('saved_')}
        source.rename(destination)
        before=destination.read_bytes(),destination.stat().st_mtime_ns
        self.memory.remap_paths({source:destination})
        self.assertTrue(self.memory.is_pinned(destination))
        self.assertFalse(self.memory.is_pinned(source))
        row=self.memory.saved_photos()[0]
        self.assertEqual(row['path'],str(destination))
        self.assertEqual(row['name'],destination.name)
        self.assertEqual(row['reason'],'내가 고른 사진')
        self.assertTrue(row['available'])
        self.assertEqual({key:row[key] for key in identity},identity)
        self.assertEqual((destination.read_bytes(),destination.stat().st_mtime_ns),before)
        destination.rename(source)
        self.memory.remap_paths({destination:source})
        reopened=FileMemory(self.memory.data)
        self.assertTrue(reopened.is_pinned(source))
        self.assertTrue(reopened.saved_photos()[0]['available'])
        self.assertEqual(reopened.saved_photos()[0]['name'],source.name)

    def test_pin_and_photo_remap_chain_uses_original_snapshot(self):
        first,second=self.file('first.png'),self.file('second.png')
        third=self.root/'third.png'
        for path in (first,second):
            self.memory.set_pinned(path);self.memory.save_photo({'path':path,'reason':path.name})
        second.rename(third);first.rename(second)
        before={path:path.read_bytes() for path in (second,third)}
        self.memory.remap_paths({first:second,second:third})
        self.assertEqual([row['path'] for row in self.memory.pinned_files()],[str(third),str(second)])
        rows=self.memory.saved_photos()
        self.assertEqual([row['path'] for row in rows],[str(third),str(second)])
        self.assertEqual([row['reason'] for row in rows],['second.png','first.png'])
        self.assertTrue(all(row['available'] for row in rows))
        self.assertEqual({path:path.read_bytes() for path in before},before)

    def test_pin_and_photo_remap_swap_preserves_both_original_identities(self):
        first,second=self.file('swap A.png'),self.file('swap B.png')
        for path in (first,second):
            self.memory.set_pinned(path);self.memory.save_photo({'path':path,'reason':path.name})
        temporary=self.root/'swapping.tmp'
        first.rename(temporary);second.rename(first);temporary.rename(second)
        before={path:path.read_bytes() for path in (first,second)}
        self.memory.remap_paths({first:second,second:first})
        self.assertEqual([row['path'] for row in self.memory.pinned_files()],[str(first),str(second)])
        rows=self.memory.saved_photos()
        self.assertEqual([(row['path'],row['reason']) for row in rows],
                         [(str(first),'swap B.png'),(str(second),'swap A.png')])
        self.assertTrue(all(row['available'] for row in rows))
        self.assertEqual({path:path.read_bytes() for path in before},before)

    def test_pin_and_photo_remap_collisions_keep_latest_saved_reference(self):
        first,second=self.file('collision A.png'),self.file('collision B.png')
        before={path:path.read_bytes() for path in (first,second)}
        for source_newest in (False,True):
            with self.subTest(source_newest=source_newest):
                for path in (first,second):
                    self.memory.set_pinned(path,False);self.memory.unsave_photo(path)
                order=(second,first) if source_newest else (first,second)
                for index,path in enumerate(order):
                    with patch('file_memory.time.time',return_value=100+index):
                        self.memory.set_pinned(path);self.memory.save_photo({'path':path,'reason':path.name})
                self.memory.remap_paths({first:second})
                pins=self.memory.pinned_files();photos=self.memory.saved_photos()
                self.assertEqual(len(pins),1);self.assertEqual(len(photos),1)
                self.assertEqual(pins[0]['path'],str(second))
                self.assertEqual(pins[0]['pinned_at'],101)
                self.assertEqual(photos[0]['reason'],order[-1].name)
                self.assertEqual(photos[0]['saved_at'],101)
                self.assertEqual(photos[0]['available'],not source_newest)
        self.assertEqual({path:path.read_bytes() for path in before},before)

    def test_invalid_remap_cannot_partially_change_pins_or_photos(self):
        path=self.file('untouched.png')
        self.memory.set_pinned(path);self.memory.save_photo({'path':path})
        with self.assertRaises(ValueError):self.memory.remap_paths({path:self.root/'new.png',None:path})
        self.assertTrue(self.memory.is_pinned(path))
        self.assertEqual(self.memory.saved_photos()[0]['path'],str(path))

    def test_parallel_pin_and_photo_updates_remain_deduplicated(self):
        paths=[self.file(f'parallel-photo-{number}.png') for number in range(8)]
        def remember(path):
            self.memory.set_pinned(path);self.memory.save_photo({'path':path})
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(remember,paths*2))
        reopened=FileMemory(self.memory.data)
        self.assertEqual(len(reopened.pinned_files()),len(paths))
        self.assertEqual(len(reopened.saved_photos()),len(paths))
        self.assertTrue(all(row['available'] for row in reopened.saved_photos()))


if __name__ == '__main__':
    unittest.main()
