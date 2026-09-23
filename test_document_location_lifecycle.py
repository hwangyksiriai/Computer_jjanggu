"""Document scope lifecycle without opening Tk, watchers, or personal files."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from enhancements import EnhancedApp


class Value:
    def __init__(self,value=''):self.value=value
    def set(self,value):self.value=value
    def get(self):return self.value


class DocumentLocationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name)
        self.old=self.base/'old';self.new=self.base/'new';self.vault=self.base/'vault'
        for path in (self.old,self.new,self.vault):path.mkdir()
        self.app=EnhancedApp.__new__(EnhancedApp)
        app=self.app;app.data=self.base/'state';app.demo=self.old
        app.settings={'source':str(self.old),'vault':str(self.vault),'document_roots':[str(self.old)]}
        app.restarting=False;app.busy=False;app.indexing=False;app.index_again=False
        app.index_cancel=threading.Event();app._index_roots=();app._document_roots_migrated=False
        app.index_pool=ThreadPoolExecutor(max_workers=1);self.addCleanup(self.stop_indexer)
        app.changed_lock=threading.Lock();app.changed_paths=set();app.events=queue.Queue()
        app.watcher=None;app.watch_roots=None;app.root=SimpleNamespace(after=Mock())
        app.status=Value();app.query=Value();app.page='home';app.save=Mock()
        app.result_cache=[{'path':str(self.old/'previous.txt')}]
        app.refresh_index_results=Mock()
        app.library=SimpleNamespace(ai_error='',index_state={},index=Mock(return_value=0),
                                    rows=Mock(return_value=[]),stats=Mock(return_value={'total':0,'pending':0}),
                                    smart_search=Mock(return_value=([],'query')))

    def stop_indexer(self):
        self.app.index_cancel.set();self.app.index_pool.shutdown(wait=True,cancel_futures=True)

    def wait_for(self,predicate):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            while not self.app.events.empty():self.app.events.get_nowait()()
            if predicate():return
            time.sleep(.005)
        self.fail('Document index lifecycle did not settle')

    def test_empty_explicit_scope_never_queries_all_cached_documents(self):
        self.app.settings['document_roots']=[]
        self.assertEqual(self.app.document_rows(),[])
        self.assertEqual(self.app.document_stats(),{'total':0,'pending':0})
        self.assertEqual(self.app.search_documents('급여명세서'),([], '급여명세서'))
        self.app.reindex()
        for method in ('rows','stats','smart_search','index'):getattr(self.app.library,method).assert_not_called()
        self.assertEqual(self.app.library.search_coverage['roots'],[])
        self.assertFalse(self.app.indexing)

    def test_document_queries_use_all_selected_roots_and_exclude_organization_vault(self):
        self.app.settings['document_roots']=[str(self.new),str(self.old)]
        self.app.document_rows(limit=30)
        self.app.document_stats()
        self.app.search_documents('급여명세서')
        self.app.library.rows.assert_called_once_with([self.new,self.old],limit=30)
        self.app.library.stats.assert_called_once_with([self.new,self.old])
        self.app.library.smart_search.assert_called_once_with('급여명세서',[self.new,self.old],'')

    def test_switching_roots_cancels_old_index_and_restarts_once_with_new_scope(self):
        started=threading.Event();release=threading.Event();self.addCleanup(release.set)
        calls=[]
        def index(roots,progress=None,only_paths=None):
            calls.append(tuple(roots))
            if tuple(roots)==(self.old,):
                started.set();release.wait(3)
                progress(0,'old.txt')
                return 99
            progress(0,'new.txt')
            return 1
        self.app.library.index=index
        self.app.reindex();self.assertTrue(started.wait(2))
        with patch.object(self.app,'_refresh_document_watcher'):
            self.app.set_document_roots([self.new])
        self.assertTrue(self.app.index_cancel.is_set())
        self.assertEqual(self.app.result_cache,[])
        release.set()
        self.wait_for(lambda:not self.app.indexing and len(calls)==2)
        self.assertEqual(calls,[(self.old,),(self.new,)])
        self.assertEqual(self.app.status.get(),'1개 파일 확인 완료')
        self.assertEqual((self.app.settings['source'],self.app.settings['vault']),(str(self.old),str(self.vault)))

    def test_removing_all_roots_stops_old_index_without_starting_unrestricted_scan(self):
        started=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def index(roots,progress=None,only_paths=None):
            started.set();release.wait(3);progress(0,'old.txt');return 99
        self.app.library.index=Mock(side_effect=index)
        self.app.reindex();self.assertTrue(started.wait(2))
        with patch.object(self.app,'_refresh_document_watcher'):
            self.app.set_document_roots([])
        release.set();self.wait_for(lambda:not self.app.indexing)
        self.app.library.index.assert_called_once()
        self.assertEqual(self.app.library.search_coverage['roots'],[])
        self.assertIn('폴더를 연결',self.app.status.get())

    def test_replaced_watcher_cannot_enqueue_notifications_from_old_roots(self):
        with patch('file_watch.FileWatch') as watch,patch.object(self.app,'reindex'):
            self.app._refresh_document_watcher()
            old_callback=watch.call_args.args[1];old_watch=self.app.watcher
            self.app.set_document_roots([self.new])
            new_callback=watch.call_args.args[1]
            old_watch.close.assert_called_once()
            old_callback({str(self.old/'old.txt'),None})
            self.assertEqual(self.app.changed_paths,set())
            new_callback({str(self.new/'new.txt'),str(self.old/'outside.txt')})
            self.assertEqual(self.app.changed_paths,{str(self.new/'new.txt')})

    def test_offline_selected_root_reappearing_starts_watcher_and_full_index(self):
        missing=self.base/'offline'
        self.app.settings['document_roots']=[str(missing)];self.app.watch_roots=()
        self.assertEqual(self.app.document_roots(),[])
        missing.mkdir()
        with patch('file_watch.FileWatch') as watch,patch.object(self.app,'reindex') as reindex:
            self.app.ensure_watcher()
        self.assertEqual(watch.call_args.args[0],(str(missing),))
        reindex.assert_called_once_with()
        self.assertEqual(self.app.settings['document_roots'],[str(missing)])

    def test_similar_search_does_not_publish_a_previous_location_after_switch(self):
        row={'path':str(self.old/'document.txt'),'name':'document.txt'}
        self.app.run_job=Mock();self.app.show=Mock()
        self.app.similar(row)
        finish=self.app.run_job.call_args.args[1]
        self.app.settings['document_roots']=[str(self.new)]
        self.app.result_cache=[]
        finish(([row],''))
        self.assertEqual(self.app.result_cache,[])
        self.app.show.assert_not_called()
        self.assertIn('검색 위치가 바뀌었어요',self.app.status.get())


if __name__=='__main__':unittest.main()
