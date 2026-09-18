import os
from pathlib import Path
import tempfile
import uuid
import shutil
import time
import unittest
from unittest.mock import patch
from core import Library, safe_transfer, fingerprint, classify, extract

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.test_base=(Path(__file__).parent/'.local'/'tests').resolve()
        self.root=self.test_base/uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.source=self.root/'Desktop'; self.source.mkdir()
        self.vault=self.root/'Vault'; self.lib=Library(self.root/'state')
    def tearDown(self):
        assert self.test_base in self.root.resolve().parents
        shutil.rmtree(self.root)
    def file(self,name,body):
        p=self.source/name; p.write_text(body,encoding='utf-8')
        os.utime(p,(time.time()-60,time.time()-60)); return p
    def test_content_search_and_followup(self):
        self.file('scan_0032.txt','Invoice\nAmount due: USD 1320\nPayment due: October 1')
        self.file('new.txt','청구서 청구금액 55000원 지급기한 10월 1일')
        self.file('coffee.txt','Receipt 결제 완료 영수증')
        self.lib.index([self.source])
        rows,context=self.lib.search('인보이스 찾아줘')
        self.assertEqual(len(rows),2)
        rows,_=self.lib.search('그중 달러로 된 것만',context)
        self.assertEqual([r['name'] for r in rows],['scan_0032.txt'])
    def test_incremental_index_and_generated_folder_exclusion(self):
        self.file('first.txt','Invoice amount due')
        nested=self.source/'Documents'; nested.mkdir()
        (nested/'second.txt').write_text('second',encoding='utf-8')
        generated=self.source/'node_modules'; generated.mkdir()
        (generated/'ignored.txt').write_text('generated',encoding='utf-8')
        seen=[]
        def enriched(row):
            with self.lib.connect() as c:
                count=c.execute('SELECT COUNT(*) FROM files').fetchone()[0]
            seen.append((row['name'],count))
        self.assertEqual(self.lib.index([self.source],on_file=enriched),2)
        self.assertEqual(seen,[('first.txt',1),('second.txt',2)])
        cached=[]
        self.lib.index([self.source],on_file=lambda row:cached.append(row['body']))
        self.assertEqual(cached,['Invoice amount due','second'])
    def test_move_collision_and_undo(self):
        p=self.file('note.txt','valuable content')
        d=self.vault/'기타 문서'; d.mkdir(parents=True); (d/'note.txt').write_text('existing')
        self.lib.index([self.source]); plan=self.lib.plan(self.source,self.vault)
        done,errors=self.lib.move(plan,self.source,self.vault)
        self.assertFalse(errors); self.assertFalse(p.exists()); self.assertTrue(Path(done[0]).exists())
        self.assertEqual((d/'note.txt').read_text(),'existing')
        done,errors=self.lib.undo(); self.assertFalse(errors); self.assertEqual(p.read_text(),'valuable content')
        self.assertEqual(self.lib.search()[0][0]['path'],str(p))
    def test_undo_never_overwrites(self):
        p=self.file('note.txt','original')
        self.lib.move(self.lib.plan(self.source,self.vault),self.source,self.vault)
        p.write_text('newer')
        done,errors=self.lib.undo(); self.assertEqual(done,[]); self.assertTrue(errors)
        self.assertEqual(p.read_text(),'newer')
    def test_changed_after_preview_skipped(self):
        p=self.file('note.txt','original'); plan=self.lib.plan(self.source,self.vault); p.write_text('changed')
        done,errors=self.lib.move(plan,self.source,self.vault)
        self.assertFalse(done); self.assertTrue(errors); self.assertEqual(p.read_text(),'changed')
    def test_modified_vault_file_not_restored(self):
        self.file('note.txt','original')
        done,_=self.lib.move(self.lib.plan(self.source,self.vault),self.source,self.vault)
        Path(done[0]).write_text('edited in vault')
        undone,errors=self.lib.undo(); self.assertFalse(undone); self.assertTrue(errors)
        self.assertEqual(Path(done[0]).read_text(),'edited in vault')
    def test_protected_files_and_nested_folders(self):
        self.file('app.lnk','shortcut'); self.file('download.part','in progress'); self.file('desktop.ini','system')
        (self.source/'Project').mkdir(); (self.source/'Project'/'work.txt').write_text('important')
        self.assertEqual(self.lib.plan(self.source,self.vault),[])
        with self.assertRaises(ValueError): self.lib.plan(self.source,self.source/'inside')
    def test_scope_validation(self):
        p=self.file('note.txt','text'); plan=self.lib.plan(self.source,self.vault)
        plan[0]['dest']=str(self.root/'outside.txt')
        done,errors=self.lib.move(plan,self.source,self.vault)
        self.assertFalse(done); self.assertTrue(errors); self.assertTrue(p.exists())
    def test_exclusive_copy(self):
        p=self.file('note.txt','source'); dest=self.root/'already.txt'; dest.write_text('do not overwrite')
        with self.assertRaises(FileExistsError): safe_transfer(p,dest,fingerprint(p))
        self.assertEqual(dest.read_text(),'do not overwrite'); self.assertTrue(p.exists())
    def test_copy_failure_preserves_source(self):
        p=self.file('note.txt','source'); dest=self.root/'dest.txt'
        target='core.os.rename' if os.name=='nt' else 'core.shutil.copyfileobj'
        with patch(target,side_effect=OSError('disk full')):
            with self.assertRaises(OSError): safe_transfer(p,dest,fingerprint(p))
        self.assertTrue(p.exists()); self.assertFalse(dest.exists())
    def test_removed_files_pruned(self):
        p=self.file('note.txt','text'); self.lib.index([self.source]); p.unlink(); self.lib.index([self.source])
        self.assertEqual(self.lib.search()[0],[])
    def test_invoice_requires_multiple_signals(self):
        self.assertEqual(classify('memo.txt','공급가액만 기록'),'기타 문서')
        self.assertEqual(classify('scan.txt','공급가액 1000 지급기한 9월'),'인보이스')

if __name__=='__main__': unittest.main(verbosity=2)
