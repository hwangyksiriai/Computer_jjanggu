import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
import unittest
from unittest.mock import patch

from smart_collections import (SmartCollectionStore,SmartCollectionResult,SmartQueryRunner,
                               collection_values,modified_bounds,query_collection,condition_summary)


class SmartCollectionsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.root=self.base/'docs';self.root.mkdir()
        self.other=self.base/'elsewhere';self.other.mkdir()
        self.db=self.base/'catalog.db';self.store=SmartCollectionStore(self.base/'state')
        with closing(sqlite3.connect(self.db)) as connection,connection:
            connection.execute('CREATE TABLE files(path TEXT PRIMARY KEY,name TEXT,body TEXT,category TEXT,status TEXT,mtime REAL,size INTEGER,scope TEXT)')

    def add(self,name='문서.txt',body='급여 명세서 기본급 공제',category='급여명세서',root=None,mtime=None,scope=None):
        root=root or self.root;path=root/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_text(body,encoding='utf8')
        if mtime is not None:os.utime(path,(mtime,mtime))
        stat=path.stat()
        with closing(sqlite3.connect(self.db)) as connection,connection:
            connection.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)',
                (str(path),path.name,body,category,'분석 완료',stat.st_mtime,stat.st_size,str(scope or root)))
        return path

    def query(self,**kwargs):
        return query_collection(self.db,collection_values('모음',**kwargs),[self.root])

    def test_persistent_crud_does_not_replace_other_data(self):
        ident=self.store.create('월급',query='공제',document_type='급여명세서')
        reopened=SmartCollectionStore(self.store.data);row=reopened.get(ident)
        self.assertEqual(row['query'],'공제');self.assertEqual(len(reopened.list()),1)
        reopened.update(ident,'내 월급',query='기본급');self.assertEqual(self.store.get(ident)['revision'],2)
        self.assertTrue(reopened.delete(ident));self.assertFalse(reopened.delete(ident));self.assertIsNone(self.store.get(ident))

    def test_unicode_name_unique_and_invalid_update_atomic(self):
        ident=self.store.create('ＡＢＣ');self.store.create('다른 이름')
        with self.assertRaises(ValueError):self.store.create('abc')
        with self.assertRaises(ValueError):self.store.update(ident,'다른 이름')
        self.assertEqual(self.store.get(ident)['name'],'ＡＢＣ')

    def test_validation_and_ignored_hidden_dates(self):
        for kwargs in ({'name':''},{'name':'a'*81},{'name':'x','modified_period':'unknown'},
                       {'name':'x','modified_period':'custom'},
                       {'name':'x','modified_period':'custom','modified_from':'2026-02-30'},
                       {'name':'x','modified_period':'custom','modified_from':'2026-09-20','modified_to':'2026-09-01'}):
            with self.assertRaises(ValueError):collection_values(**kwargs)
        self.assertEqual(collection_values('x',modified_from='unfinished')['modified_from'],'')

    def test_content_and_type_find_generic_filename(self):
        included=self.add('SCAN0001.txt');self.add('월급메모.txt',body='공제 이야기',category='기타 문서')
        rows=self.query(query='기본급 공제',document_type='급여명세서').rows
        self.assertEqual([row['path'] for row in rows],[str(included)])

    def test_query_is_literal_and_case_insensitive_not_sql_like_wildcard(self):
        included=self.add('A 100%.txt',body='Straße [월급]')
        self.add('B.txt',body='STRASSE 1000')
        self.assertEqual([row['path'] for row in self.query(query='STRASSE 100%').rows],[str(included)])
        self.assertEqual(self.query(query="' OR 1=1 --").rows,[])

    def test_empty_none_scope_never_opens_database(self):
        for roots in ([],None):
            with patch('smart_collections.sqlite3.connect',side_effect=AssertionError('must not query')):
                self.assertEqual(query_collection(self.db,{},roots).rows,[])

    def test_current_scope_only_and_invalid_scope_path_rejected(self):
        yes=self.add();self.add('outside.txt',root=self.other)
        self.add('spoof.txt',root=self.other,scope=self.root)
        self.assertEqual([row['path'] for row in self.query().rows],[str(yes)])
        self.assertEqual(query_collection(self.db,collection_values('x'),[self.other]).rows[0]['name'],'outside.txt')

    def test_overlapping_roots_and_duplicate_inputs(self):
        path=self.add();result=query_collection(self.db,collection_values('x'),[self.root,self.root,self.root/'nested'])
        self.assertEqual([row['path'] for row in result.rows],[str(path)])

    def test_new_indexed_file_appears_without_rewriting_definition(self):
        ident=self.store.create('급여',document_type='급여명세서');definition=self.store.get(ident)
        self.assertEqual(query_collection(self.db,definition,[self.root]).rows,[])
        self.add();self.assertEqual(len(query_collection(self.db,SmartCollectionStore(self.store.data).get(ident),[self.root]).rows),1)
        self.assertEqual(self.store.get(ident),definition)

    def test_custom_dates_include_whole_final_day_and_use_file_mtime(self):
        stamp=lambda value:datetime.fromisoformat(value).timestamp()
        included=self.add('a.txt',body='발행일 2020-01-01',mtime=stamp('2026-09-30T23:59:59'))
        self.add('b.txt',body='발행일 2026-09-23',mtime=stamp('2026-10-01T00:00:00'))
        rows=self.query(modified_period='custom',modified_from='2026-09-01',modified_to='2026-09-30').rows
        self.assertEqual([row['path'] for row in rows],[str(included)])
        self.assertIn('수정일',condition_summary(collection_values('x',modified_period='custom',modified_to='2026-09-30')))

    def test_calendar_bounds_across_year_and_leap_day(self):
        a,b=modified_bounds({'modified_period':'last_month'},datetime(2026,1,20))
        self.assertEqual((datetime.fromtimestamp(a),datetime.fromtimestamp(b)),(datetime(2025,12,1),datetime(2026,1,1)))
        a,b=modified_bounds({'modified_period':'this_month'},datetime(2024,2,20))
        self.assertEqual((datetime.fromtimestamp(a),datetime.fromtimestamp(b)),(datetime(2024,2,1),datetime(2024,3,1)))
        a,b=modified_bounds({'modified_period':'last_30_days'},datetime(2026,1,1))
        self.assertEqual((datetime.fromtimestamp(b)-datetime.fromtimestamp(a)).days,30)

    def test_deleted_directory_and_stale_mtime_are_excluded(self):
        stamp=datetime(2026,9,2).timestamp()
        deleted=self.add('deleted.txt',mtime=stamp);deleted.unlink()
        directory=self.add('directory.txt',mtime=stamp);directory.unlink();directory.mkdir()
        stale=self.add('stale.txt',mtime=stamp);os.utime(stale,(stamp+40*86400,stamp+40*86400))
        self.assertEqual(self.query(modified_period='custom',modified_from='2026-09-01',modified_to='2026-09-30').rows,[])

    def test_read_only_original_and_catalog_unchanged(self):
        path=self.add();before=(path.read_bytes(),path.stat().st_mtime_ns,self.db.read_bytes())
        self.query(query='공제')
        self.assertEqual(before,(path.read_bytes(),path.stat().st_mtime_ns,self.db.read_bytes()))

    def test_replaced_file_does_not_match_old_content_before_reindex(self):
        changed_size=self.add('size.txt');old_time=changed_size.stat().st_mtime
        changed_size.write_text('회의 일정',encoding='utf8');os.utime(changed_size,(old_time,old_time))
        changed_time=self.add('time.txt');before=changed_time.stat()
        changed_time.write_bytes(b'x'*before.st_size);os.utime(changed_time,(before.st_mtime+2,before.st_mtime+2))
        result=self.query(query='기본급',document_type='급여명세서')
        self.assertEqual(result.rows,[]);self.assertEqual(result.outdated,2)
        self.assertEqual(changed_size.read_text(encoding='utf8'),'회의 일정')

    def test_limit_and_cancellation_discard_partial_results(self):
        for index in range(5):self.add(str(index)+'.txt')
        result=query_collection(self.db,collection_values('x'),[self.root],limit=2)
        self.assertEqual(len(result.rows),2);self.assertTrue(result.truncated)
        calls=0
        def cancel():
            nonlocal calls
            calls+=1;return calls>=5
        result=query_collection(self.db,collection_values('x'),[self.root],cancelled=cancel)
        self.assertTrue(result.cancelled);self.assertEqual(result.rows,[])

    def test_runner_latest_result_and_close_cancel(self):
        started=threading.Event();release=threading.Event();seen=[]
        def fake(db,definition,roots,cancelled,limit):
            seen.append(definition['name'])
            if definition['name']=='old':started.set();release.wait(2)
            return SmartCollectionResult([{'name':definition['name']}],cancelled=cancelled())
        with patch('smart_collections.query_collection',side_effect=fake):
            runner=SmartQueryRunner(self.db)
            try:
                runner.submit({'name':'old'},[self.root]);self.assertTrue(started.wait(2))
                runner.submit({'name':'queued'},[self.root]);runner.submit({'name':'new'},[self.root]);release.set()
                response=None;deadline=time.monotonic()+2
                while response is None and time.monotonic()<deadline:response=runner.drain();time.sleep(.005)
                self.assertIsNotNone(response);self.assertEqual(response[1].rows,[{'name':'new'}]);self.assertNotIn('queued',seen)
            finally:release.set();runner.close();runner.thread.join(2)
            self.assertFalse(runner.thread.is_alive());self.assertIsNone(runner.drain());runner.close()


if __name__=='__main__':unittest.main()
