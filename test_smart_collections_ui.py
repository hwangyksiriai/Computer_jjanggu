"""Synthetic Tk regressions; run serially with the other UI suites."""
from contextlib import closing
import gc
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from result_browser import ResultBrowser
from smart_collections import SmartCollectionStore,SmartCollectionResult
from smart_collections_ui import (SmartCollectionsWindow,SmartCollectionEditor,SmartResultSource,
                                   refresh_smart_collections)


class SmartCollectionsUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
        self.docs=self.base/'docs';self.docs.mkdir();self.other=self.base/'other';self.other.mkdir()
        self.db=self.base/'catalog.db'
        with closing(sqlite3.connect(self.db)) as connection,connection:
            connection.execute('CREATE TABLE files(path TEXT PRIMARY KEY,name TEXT,body TEXT,category TEXT,status TEXT,mtime REAL,size INTEGER,scope TEXT)')
        self.root=tk.Tk();self.root.withdraw();self.errors=[];self.browsers=[];self.sources=[];self.preview_finished=[]
        self.root.report_callback_exception=lambda kind,value,trace:self.errors.append(value)
        self.attach=patch('ime_entry.attach',return_value=None);self.attach.start()
        original=ResultBrowser._render_worker;finished=self.preview_finished
        def tracked(requests,responses):
            event=threading.Event();finished.append(event)
            try:original(requests,responses)
            finally:event.set()
        self.preview_patch=patch.object(ResultBrowser,'_render_worker',staticmethod(tracked));self.preview_patch.start()
        self.roots=[self.docs]
        self.app=SimpleNamespace(root=self.root,data=self.base/'state',settings={'text_scale':1.0},
            query=tk.StringVar(master=self.root),last_submitted='',busy=False,indexing=False,
            search_fx={},result_cache=[],document_roots=lambda:list(self.roots),
            library=SimpleNamespace(db=self.db,search_coverage={}),open_file=Mock(),reveal=Mock(),do_search=Mock(),
            is_pinned=Mock(return_value=False),toggle_pinned=Mock(),copy_result_files=Mock())
        self.store=self.app.smart_collection_store=SmartCollectionStore(self.app.data)

    def tearDown(self):
        for widget in list(self.root.winfo_children()):
            if isinstance(widget,tk.Toplevel):widget.destroy()
        for source in self.sources:source.close();source.runner.thread.join(3)
        self.wait(lambda:all(event.is_set() for event in self.preview_finished))
        self.assertEqual(self.errors,[])
        self.preview_patch.stop();self.attach.stop()
        self.app.query=None;self.app=None;self.browsers.clear();self.sources.clear()
        self.root.report_callback_exception=None;self.root.destroy();self.root=None
        gc.collect();self.temp.cleanup()

    def wait(self,predicate,timeout=4):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.root.update()
            if predicate():return
            time.sleep(.01)
        self.fail('UI worker did not settle')

    def add(self,name,root=None):
        root=root or self.docs;path=root/name;path.write_text('급여 명세서 기본급 공제',encoding='utf8');stat=path.stat()
        with closing(sqlite3.connect(self.db)) as connection,connection:
            connection.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?)',
                (str(path),name,path.read_text(encoding='utf8'),'급여명세서','분석 완료',stat.st_mtime,stat.st_size,str(root)))
        return str(path)

    def browser(self):
        ident=self.store.create('급여',document_type='급여명세서');definition=self.store.get(ident)
        browser=ResultBrowser(self.app,[],context_title='자동 모음 · 급여');self.browsers.append(browser)
        source=SmartResultSource(self.app,definition,browser);self.sources.append(source)
        return browser,source

    def test_saved_query_async_then_new_file_refresh_preserves_selection(self):
        first=self.add('scan001.txt');browser,source=self.browser()
        self.wait(lambda:len(browser.rows)==1);browser.select(first)
        second=self.add('scan002.txt');refresh_smart_collections(self.app)
        self.wait(lambda:len(browser.rows)==2)
        self.assertEqual(browser.selected_path,first)
        self.assertEqual({row['path'] for row in browser.rows},{first,second})
        self.assertIn('수정일',browser.coverage.cget('text'))

    def test_scope_change_clears_old_rows_and_zero_never_leaks(self):
        first=self.add('first.txt');second=self.add('second.txt',self.other);browser,source=self.browser()
        self.wait(lambda:len(browser.rows)==1);self.assertEqual(browser.rows[0]['path'],first)
        self.roots[:]=[self.other];refresh_smart_collections(self.app)
        self.assertEqual(browser.rows,[])
        self.wait(lambda:len(browser.rows)==1);self.assertEqual(browser.rows[0]['path'],second)
        self.roots.clear();refresh_smart_collections(self.app)
        self.wait(lambda:not source.loading)
        self.assertEqual(browser.rows,[]);self.assertIn('폴더',browser.coverage.cget('text'))

    def test_slow_old_scope_response_discarded(self):
        first=self.add('first.txt');second=self.add('second.txt',self.other)
        entered=threading.Event();release=threading.Event()
        def fake(db,definition,roots,cancelled,limit):
            if roots==(str(self.docs),):entered.set();release.wait(3)
            path=first if roots==(str(self.docs),) else second
            return SmartCollectionResult([{'path':path,'name':Path(path).name,'body':'','group':'일치하는 파일'}])
        with patch('smart_collections.query_collection',side_effect=fake):
            browser,source=self.browser()
            try:
                self.assertTrue(entered.wait(2));self.roots[:]=[self.other];source.refresh();release.set()
                self.wait(lambda:len(browser.rows)==1)
                self.assertEqual(browser.rows[0]['path'],second)
            finally:release.set();source.close();source.runner.thread.join(3)

    def test_normal_search_switch_not_overwritten_and_worker_stops(self):
        self.add('a.txt');browser,source=self.browser();self.wait(lambda:len(browser.rows)==1)
        browser._previous_search=None;browser.search_query='새 검색';browser.pending=True
        refresh_smart_collections(self.app)
        browser.update_results([], '새 검색')
        self.wait(lambda:source.closed)
        self.assertEqual(browser.rows,[]);self.assertEqual(browser.search_query,'새 검색')
        self.assertIsNone(browser.context_title);refresh_smart_collections(self.app);self.assertEqual(browser.rows,[])

    def test_failed_search_resumes_with_new_scope(self):
        self.add('a.txt');second=self.add('b.txt',self.other);browser,source=self.browser()
        self.wait(lambda:len(browser.rows)==1)
        browser._previous_search=None;browser.search_query='새 검색';browser.pending=True
        self.roots[:]=[self.other];refresh_smart_collections(self.app);browser._search_failed()
        self.wait(lambda:len(browser.rows)==1 and browser.rows[0]['path']==second)

    def test_delete_definition_clears_results_but_not_original(self):
        path=self.add('a.txt');browser,source=self.browser();self.wait(lambda:len(browser.rows)==1)
        self.store.delete(source.ident);refresh_smart_collections(self.app)
        self.assertEqual(browser.rows,[]);self.assertTrue(Path(path).is_file());self.assertIn('지워졌어요',browser.coverage.cget('text'))

    def test_close_during_work_never_calls_destroyed_tk(self):
        entered=threading.Event();release=threading.Event()
        def fake(*args):entered.set();release.wait(3);return SmartCollectionResult([])
        with patch('smart_collections.query_collection',side_effect=fake):
            browser,source=self.browser()
            try:
                self.assertTrue(entered.wait(2));browser.win.destroy();release.set();source.runner.thread.join(3)
                self.assertTrue(source.closed);self.assertFalse(source.runner.thread.is_alive())
            finally:release.set()

    def test_editor_templates_dates_and_small_large_font_scroll(self):
        self.app.settings['text_scale']=1.6
        manager=SmartCollectionsWindow(self.app);self.app.smart_collections_view=manager
        manager.win.geometry('430x390');editor=manager.from_template();editor.win.geometry('420x380')
        self.root.update();self.assertEqual(editor.values['name'].get(),'급여명세서')
        self.assertEqual(editor.values['document_type'].get(),'급여명세서')
        self.assertFalse(editor.dates.winfo_ismapped())
        editor.values['modified_period'].set('수정일 직접 정하기');editor._dates();self.root.update()
        self.assertTrue(editor.dates.winfo_ismapped())
        editor.values['modified_from'].set('2026-09-30');editor.values['modified_to'].set('2026-09-01');editor.save()
        self.assertFalse(editor.closed);self.assertIn('마지막 날',editor.feedback.cget('text'))
        editor.values['modified_to'].set('2026-09-30');saved=[];editor.on_saved=saved.append;editor.save()
        self.assertTrue(editor.closed);self.assertEqual(len(saved),1);self.assertEqual(len(self.store.list()),1)
        manager.refresh();self.root.update()
        self.assertLessEqual(manager.win.winfo_width(),450)
        self.assertGreater(manager.page.winfo_reqheight(),manager.win.winfo_height())


if __name__=='__main__':unittest.main()
