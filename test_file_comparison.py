"""Synthetic-only regression tests for read-only content/version comparison."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import file_comparison as comparison


class FileComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def file(self,name,content=b'example'):
        path=self.root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content)
        return {'path':str(path),'name':name,'size':999,'mtime':0}

    def test_same_bytes_different_names_and_timestamps_group(self):
        left=self.file('invoice.pdf',b'same bytes')
        right=self.file('download (1).pdf',b'same bytes')
        other=self.file('different.pdf',b'other data')
        os.utime(right['path'],(1000000000,1000000000))
        rows=[left,right,other]
        originals=[dict(row) for row in rows]
        result=comparison.group_identical_files(rows)
        self.assertEqual(result.groups,[[left,right]])
        self.assertEqual(result.hash_status[left['path']],'identical')
        self.assertEqual(result.hash_status[other['path']],'unique')
        self.assertEqual(result.unverified,[])
        self.assertEqual(rows,originals)
        self.assertEqual(Path(left['path']).read_bytes(),b'same bytes')
        self.assertEqual(Path(right['path']).read_bytes(),b'same bytes')

    def test_equal_size_and_mtime_do_not_establish_equal_content(self):
        rows=[self.file('a.txt',b'AAAA'),self.file('b.txt',b'BBBB')]
        for row in rows:os.utime(row['path'],(1000000000,1000000000))
        result=comparison.group_identical_files(rows)
        self.assertEqual(result.groups,[])
        self.assertEqual(result.unverified,[])
        self.assertEqual(set(result.hash_status.values()),{'unique'})

    def test_size_filter_uses_actual_size_and_skips_unnecessary_hashes(self):
        rows=[self.file('a.txt',b'A'),self.file('b.txt',b'BB')]
        with patch.object(comparison,'_hash_file',side_effect=AssertionError('size bucket is a singleton')):
            result=comparison.group_identical_files(rows)
        self.assertEqual(result.groups,[])
        self.assertEqual(result.unverified,[])

    def test_missing_directory_and_invalid_paths_are_unknown(self):
        missing=str(self.root/'gone.pdf')
        directory=self.root/'folder';directory.mkdir()
        result=comparison.group_identical_files([
            {'path':missing},{'path':str(directory)},{'path':'relative.pdf'},
            {'name':'missing location'},None])
        self.assertEqual(result.groups,[])
        self.assertEqual(set(result.unverified),{missing,str(directory),'relative.pdf','missing location'})
        self.assertEqual(set(result.hash_status.values()),{'unverified'})

    def test_unreadable_candidate_is_unknown(self):
        left=self.file('a.txt');right=self.file('b.txt')
        real_open=Path.open
        def opened(path,*args,**kwargs):
            if str(path)==right['path']:raise PermissionError('synthetic unreadable file')
            return real_open(path,*args,**kwargs)
        with patch.object(Path,'open',opened):
            result=comparison.group_identical_files([left,right])
        self.assertEqual(result.groups,[])
        self.assertEqual(result.unverified,[right['path']])
        self.assertEqual(result.hash_status[right['path']],'unverified')

    def test_file_changed_during_read_is_excluded(self):
        left=self.file('a.txt',b'abcdefgh')
        right=self.file('b.txt',b'abcdefgh')
        real_open=Path.open;changed=False
        class ChangingStream:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*args):return self.stream.__exit__(*args)
            def fileno(self):return self.stream.fileno()
            def read(self,size):
                nonlocal changed
                data=self.stream.read(size)
                if not changed:
                    changed=True
                    with real_open(Path(left['path']),'ab') as writer:writer.write(b'changed')
                return data
        def opened(path,*args,**kwargs):
            stream=real_open(path,*args,**kwargs)
            return ChangingStream(stream) if str(path)==left['path'] and args==('rb',) else stream
        with patch.object(Path,'open',opened),patch.object(comparison,'_CHUNK_SIZE',4):
            result=comparison.group_identical_files([left,right])
        self.assertTrue(changed)
        self.assertEqual(result.groups,[])
        self.assertEqual(result.unverified,[left['path']])

    def test_file_changed_after_hash_is_excluded_by_final_sweep(self):
        left=self.file('a.txt',b'abcd');right=self.file('b.txt',b'abcd')
        real_hash=comparison._hash_file
        def hashed(path,before,cancelled):
            digest=real_hash(path,before,cancelled)
            if str(path)==right['path']:Path(left['path']).write_bytes(b'changed')
            return digest
        with patch.object(comparison,'_hash_file',hashed):
            result=comparison.group_identical_files([left,right])
        self.assertEqual(result.groups,[])
        self.assertEqual(result.unverified,[left['path']])

    def test_repeated_scan_cannot_reuse_hash_after_same_size_mtime_replacement(self):
        left=self.file('a.txt',b'abcd');right=self.file('b.txt',b'abcd')
        self.assertEqual(len(comparison.group_identical_files([left,right]).groups),1)
        before=Path(right['path']).stat()
        Path(right['path']).write_bytes(b'WXYZ')
        os.utime(right['path'],ns=(before.st_atime_ns,before.st_mtime_ns))
        result=comparison.group_identical_files([left,right])
        self.assertEqual(result.groups,[])
        self.assertEqual(result.unverified,[])

    def test_hash_reads_bounded_chunks(self):
        row=self.file('large.bin',b'abcdefghij'*10)
        sizes=[];real_open=Path.open
        class RecordingStream:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*args):return self.stream.__exit__(*args)
            def fileno(self):return self.stream.fileno()
            def read(self,size):sizes.append(size);return self.stream.read(size)
        def opened(path,*args,**kwargs):return RecordingStream(real_open(path,*args,**kwargs))
        with patch.object(Path,'open',opened),patch.object(comparison,'_CHUNK_SIZE',7):
            digest=comparison._hash_file(Path(row['path']),Path(row['path']).stat(),lambda:False)
        self.assertEqual(digest,hashlib.sha256(b'abcdefghij'*10).hexdigest())
        self.assertGreater(len(sizes),10)
        self.assertTrue(all(0<size<=7 for size in sizes))

    def test_cancellation_discards_partial_groups_and_marks_every_path_unknown(self):
        rows=[self.file('a.txt',b'abcd'),self.file('b.txt',b'abcd'),self.file('c.txt',b'longer')]
        real_hash=comparison._hash_file;stop=False
        def hashed(path,before,cancelled):
            nonlocal stop
            digest=real_hash(path,before,cancelled)
            stop=True
            return digest
        with patch.object(comparison,'_hash_file',hashed):
            result=comparison.group_identical_files(rows,cancelled=lambda:stop)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.groups,[])
        self.assertEqual(set(result.unverified),{row['path'] for row in rows})
        self.assertEqual(set(result.hash_status.values()),{'cancelled'})
        self.assertEqual(Path(rows[0]['path']).read_bytes(),b'abcd')

    def test_already_cancelled_does_not_access_files(self):
        row=self.file('a.txt')
        with patch.object(comparison,'_file_stat',side_effect=AssertionError('must not touch file')):
            result=comparison.group_identical_files([row],cancelled=lambda:True)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.unverified,[row['path']])

    def test_repeated_path_is_not_a_duplicate_but_empty_files_are(self):
        left=self.file('a.txt',b'');right=self.file('b.txt',b'')
        single=comparison.group_identical_files([left,dict(left)])
        self.assertEqual(single.groups,[])
        both=comparison.group_identical_files([left,dict(left),right])
        self.assertEqual(both.groups,[[left,right]])


class VersionCandidatesTests(unittest.TestCase):
    def test_explicit_version_markers_are_removed(self):
        expected='보고서.pdf'
        for tag in ('_v1',' v2','_ver.3','_rev2','_revision2','_version3',
                    '_최종','최종본','_수정본',' 복사본',' (1)',' [2]',
                    '_최종_v2','_v1.2',' (ver.3)'):
            with self.subTest(tag=tag):
                self.assertEqual(comparison.version_family('보고서'+tag+'.pdf'),expected)

    def test_dates_months_customers_purpose_and_extensions_remain_significant(self):
        distinct=(
            ('급여명세서 6월_v1.pdf','급여명세서 7월_v2.pdf'),
            ('급여명세서 2026-06_최종.pdf','급여명세서 2026-07_최종.pdf'),
            ('A회사_계약서_v1.pdf','B회사_계약서_v1.pdf'),
            ('A회사_계약서_v1.pdf','A회사_청구서_v1.pdf'),
            ('보고서 1.pdf','보고서 2.pdf'),
            ('보고서 (2025).pdf','보고서 (2026).pdf'),
            ('보고서_v2025.pdf','보고서_v2026.pdf'),
            ('보고서_v1.pdf','보고서_v2.docx'),
        )
        for left,right in distinct:
            with self.subTest(left=left,right=right):
                self.assertNotEqual(comparison.version_family(left),comparison.version_family(right))

    def test_candidates_are_explicitly_heuristic_copy_rows_and_keep_input_order(self):
        root=Path(tempfile.gettempdir())/'synthetic-comparison-never-opened'
        selected={'path':str(root/'급여명세서 6월_v2.pdf'),'mtime':999}
        first={'path':str(root/'급여명세서 6월_v1.pdf'),'mtime':500}
        second={'path':str(root/'급여명세서 6월_최종.pdf'),'mtime':1}
        unrelated={'path':str(root/'급여명세서 7월_v1.pdf'),'mtime':9999}
        rows=[selected,first,unrelated,second,dict(first)]
        originals=[dict(row) for row in rows]
        with patch.object(Path,'open',side_effect=AssertionError('filename matching does not read files')):
            result=comparison.version_candidates(selected,rows)
        self.assertEqual([row['path'] for row in result],[first['path'],second['path']])
        self.assertEqual(rows,originals)
        self.assertIsNot(result[0],first)
        self.assertTrue(all(row['comparison_kind']=='similar_version' for row in result))
        self.assertTrue(all('다를 수' in row['comparison_note'] for row in result))
        self.assertTrue(all('latest' not in row for row in result))

    def test_invalid_input_and_empty_rows_are_safe(self):
        self.assertEqual(comparison.version_family(None),'')
        self.assertEqual(comparison.version_candidates(None,[None,{'path':None}]),[])
        self.assertEqual(comparison.group_identical_files([]).groups,[])


if __name__=='__main__':unittest.main()
