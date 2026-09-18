import bootstrap
import unittest,tempfile,time,threading,shutil,uuid
from pathlib import Path
from unittest.mock import patch
from knowledge import Knowledge
from core import Library
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
