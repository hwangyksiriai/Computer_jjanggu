import bootstrap
import os
from pathlib import Path
import shutil
import time
import uuid
import unittest
from knowledge import Knowledge,fields_from_text

class EnhancedTests(unittest.TestCase):
    def setUp(self):
        self.base=(Path(__file__).parent/'.local/tests').resolve(); self.tmp=self.base/uuid.uuid4().hex
        self.source=self.tmp/'source'; self.source.mkdir(parents=True); self.vault=self.tmp/'vault'; self.k=Knowledge(self.tmp/'state')
    def tearDown(self):
        assert self.base in self.tmp.resolve().parents; shutil.rmtree(self.tmp)
    def file(self,name,text):
        p=self.source/name; p.write_text(text,encoding='utf-8'); os.utime(p,(time.time()-60,time.time()-60)); return p
    def test_document_fields(self):
        f=fields_from_text('공급자: 초록 스튜디오\n발행일: 2026-09-03\n청구금액: USD 350\n지급기한: 2026-10-01')
        self.assertEqual(f['issued_date'],'2026-09-03'); self.assertEqual(f['issuer'],'초록 스튜디오'); self.assertIn('USD',f['currencies'])
    def test_payroll_without_filename_or_title(self):
        self.file('202609_001.txt','사번: 42\n기본급: 3000000\n국민연금: 100000\n실수령액: 2800000')
        self.file('attachment.txt','Employee ID: 42\nGross pay: 5000\nDeductions: 500\nNet pay: 4500')
        self.file('partial.txt','기본급: 3000000\n건강보험: 100000')
        self.file('meeting.txt','회의에서 기본급 인상을 논의할 예정')
        self.file('invoice.txt','Invoice amount due USD 300')
        self.k.index([self.source])
        # Existing classification from an older app must not hide readable content.
        with self.k.connect() as c: c.execute("UPDATE files SET category='기타 문서'")
        rows,_=self.k.smart_search('급여명세서 찾아줘',[self.source])
        self.assertEqual({r['name'] for r in rows},{'202609_001.txt','attachment.txt','partial.txt'})
        partial=next(r for r in rows if r['name']=='partial.txt')
        self.assertEqual(partial['group'],'관련 후보')
        self.assertIn('본문 단서',partial['reason'])
        self.assertEqual(self.k.search_coverage['text'],5)
    def test_high_semantic_score_without_payroll_evidence_is_excluded(self):
        self.file('client.md','클라이언트와 캠페인 금액을 논의합니다')
        self.k.index([self.source])
        class AI:
            def ready(self): return True
            def embed(self,*args,**kwargs): return [[1.,0.]]
        self.k.ai=AI()
        with self.k.connect() as c:c.execute("UPDATE knowledge SET vectors='[[1.0,0.0]]'")
        rows,_=self.k.smart_search('급여명세서 찾아줘',[self.source])
        self.assertEqual(rows,[])
    def test_learning_rule_and_metadata_survive_move(self):
        p=self.file('거래처A.txt','디자인 자료'); self.k.index([self.source])
        self.k.annotate(p,'제안서',['프로젝트 로켓'],{'received_date':'2026-09-02'},keyword='거래처a')
        q=self.file('거래처A_새파일.txt','다른 디자인 자료'); self.k.index([self.source])
        rows=self.k.rows(); new=next(r for r in rows if r['path']==str(q))
        self.assertEqual(new['category'],'제안서'); self.assertIn('프로젝트 로켓',new['tags'])
        self.k.tray_add([p]); done,errors=self.k.move(self.k.plan(self.source,self.vault),self.source,self.vault)
        self.assertFalse(errors); self.assertTrue(self.k.tray_files()[0].startswith(str(self.vault)))
        moved=next(r for r in self.k.rows() if r['name']==p.name)
        self.assertEqual(moved['fields']['received_date'],'2026-09-02')
        self.k.undo(); self.assertEqual(self.k.tray_files(),[str(p)])
    def test_reward_repeat_is_not_farmable(self):
        self.assertTrue(self.k.reward('unique',10,'test')); self.assertFalse(self.k.reward('unique',10,'test'))
        self.assertEqual(self.k.progress()['points'],10)
    def test_unknown_received_date_not_fabricated(self):
        self.file('invoice.txt','Invoice amount due USD 500'); self.k.index([self.source])
        rows,_=self.k.smart_search('지난달 받은 인보이스',[self.source])
        self.assertEqual(rows,[])
    def test_tags_search_and_tray(self):
        p=self.file('anything.txt','hello'); self.k.index([self.source]); self.k.annotate(p,'기타 문서',['우주'])
        rows,_=self.k.smart_search('#우주',[self.source]); self.assertEqual(len(rows),1)
        self.k.tray_add([p,p]); self.assertEqual(len(self.k.tray_files()),1)
    def test_category_path_traversal_rejected(self):
        p=self.file('a.txt','a'); self.k.index([self.source])
        with self.assertRaises(ValueError): self.k.annotate(p,'../../escape',[])
    def test_manual_fields_preserved_on_reindex(self):
        p=self.file('a.txt','공급자: A'); self.k.index([self.source]); self.k.annotate(p,'기타 문서',[],{'issuer':'내 이름'})
        p.write_text('공급자: B\nchanged',encoding='utf-8'); self.k.index([self.source])
        self.assertEqual(self.k.rows()[0]['fields']['issuer'],'내 이름')

if __name__=='__main__': unittest.main(verbosity=2)
