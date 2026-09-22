import bootstrap
import unittest,tempfile,time,threading,shutil,uuid
from pathlib import Path
from unittest.mock import patch
from knowledge import Knowledge
from core import Library,PENDING_STATUS
from file_watch import FileWatch

class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.base=(Path(__file__).parent/'.local/tests').resolve(); self.tmp=self.base/uuid.uuid4().hex
        self.src=self.tmp/'src'; self.src.mkdir(parents=True); self.vault=self.tmp/'vault'; self.vault.mkdir()
    def tearDown(self):
        assert self.base in self.tmp.resolve().parents; shutil.rmtree(self.tmp)
    def test_move_during_ai_does_not_resurrect_old_path(self):
        entered=threading.Event(); release=threading.Event()
        class AI:
            def ready(self): return True
            def embed(self,texts): entered.set(); release.wait(10); return [[1.,0.]]
            def call(self,*args,**kwargs): return [1.,0.]
        k=Knowledge(self.tmp/'state',AI()); p=self.src/'invoice.txt'; p.write_text('Invoice USD 500')
        import os
        os.utime(p,(time.time()-60,time.time()-60))
        thread=threading.Thread(target=lambda:k.index([self.src])); thread.start()
        try:
            self.assertTrue(entered.wait(5))
            done,errors=k.move(k.plan(self.src,self.vault),self.src,self.vault)
            self.assertFalse(errors); self.assertEqual(len(done),1)
        finally: release.set(); thread.join(15)
        self.assertFalse(thread.is_alive()); self.assertFalse(p.exists())
        with k.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM files WHERE path=?',(str(p),)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM knowledge WHERE path=?',(str(p),)).fetchone()[0],0)
        k.undo(); self.assertTrue(p.exists())
    def test_changed_while_extracting_is_not_committed(self):
        p=self.src/'a.txt'; p.write_text('old'); lib=Library(self.tmp/'state')
        def mutate(path): p.write_text('new content'); return 'old','done'
        with patch('core.extract',mutate): lib.index([self.src])
        self.assertEqual(lib.search()[0],[])
        lib.index([self.src]); self.assertEqual(lib.search('new')[0][0]['body'],'new content')
    def test_all_roots_are_searchable_before_first_slow_extraction(self):
        for name in ['a.txt','z.txt']:(self.src/name).write_text('문서',encoding='utf-8')
        (self.vault/'last.txt').write_text('보관 문서',encoding='utf-8')
        lib=Library(self.tmp/'state'); original=__import__('core').extract; seen=[]
        def inspect(path):
            if not seen:
                rows=lib.search()[0]
                self.assertEqual({r['name'] for r in rows},{'a.txt','z.txt','last.txt'})
                self.assertTrue(all(r['status']==PENDING_STATUS for r in rows))
                self.assertTrue(all(not r['body'] for r in rows))
            seen.append(path); return original(path)
        with patch('core.extract',inspect):lib.index([self.src,self.vault])
        self.assertEqual(len(seen),3)
        # A second scan must use the current text cache.
        with patch('core.extract',side_effect=AssertionError('unexpected extraction')):
            self.assertEqual(lib.index([self.src,self.vault]),3)

    def test_cancelled_catalog_resumes_content_on_next_run(self):
        from concurrent.futures import CancelledError
        p=self.src/'unknown.txt'; p.write_text('급여명세서 실수령 2500000원',encoding='utf-8')
        lib=Knowledge(self.tmp/'state')
        def cancel(count,name):
            if name:raise CancelledError()
        with self.assertRaises(CancelledError):lib.index([self.src],cancel)
        self.assertEqual(lib.rows()[0]['status'],PENDING_STATUS)
        self.assertFalse(lib.smart_search('급여명세서 찾아줘',[self.src])[0])
        lib.index([self.src])
        self.assertEqual(len(lib.smart_search('급여명세서 찾아줘',[self.src])[0]),1)

    def test_changed_file_cannot_use_old_ai_during_pending_analysis(self):
        p=self.src/'unknown.txt'; p.write_text('급여명세서 실수령 2500000원',encoding='utf-8')
        lib=Knowledge(self.tmp/'state'); lib.index([self.src])
        with lib.connect() as c:c.execute("UPDATE knowledge SET vectors='[[1,0]]',visual='[1,0]'")
        p.write_text('메뉴 케이크 디저트',encoding='utf-8')
        def inspect(path):
            row=lib.rows()[0]
            self.assertEqual(row['body'],''); self.assertEqual(row['vectors'],[])
            self.assertEqual(row['visual'],[]); self.assertEqual(row['thumbnail'],'')
            return '메뉴 케이크 디저트','본문 분석 완료'
        with patch('core.extract',inspect):lib.index([self.src])
        self.assertFalse(lib.smart_search('급여명세서 찾아줘',[self.src])[0])
    def test_incremental_delete_keeps_unrelated_rows(self):
        a=self.src/'a.txt'; b=self.src/'b.txt'; a.write_text('a'); b.write_text('b')
        k=Knowledge(self.tmp/'state'); k.index([self.src]); a.unlink()
        k.index([self.src],only_paths={str(a)})
        self.assertEqual([r['name'] for r in k.rows()],['b.txt'])
    def test_native_watch_create_modify_rename_delete(self):
        batches=[]; watch=FileWatch([self.src],lambda batch:batches.append(batch),delay=.25)
        def await_path(path):
            deadline=time.monotonic()+6
            while time.monotonic()<deadline:
                if any(str(path) in b for b in batches): batches.clear(); return
                time.sleep(.05)
            self.fail('No notification: '+str(path))
        try:
            time.sleep(.2); a=self.src/'한글 공백.txt'; a.write_text('one'); await_path(a)
            a.write_text('two'); await_path(a)
            b=self.src/'renamed.txt'; a.rename(b); await_path(b)
            b.unlink(); await_path(b)
            ignored=self.src/'node_modules'; ignored.mkdir(); (ignored/'a.txt').write_text('x'); time.sleep(.7)
            self.assertFalse(batches)
        finally: watch.close()

if __name__=='__main__': unittest.main()
