"""Title-free and scanned payroll discovery, with unrelated-document controls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core import extract, payroll_title
from knowledge import Knowledge


class PayrollSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name); self.source=self.base/'source'; self.source.mkdir()
        self.library=Knowledge(self.base/'state')

    def tearDown(self): self.temp.cleanup()

    def file(self,name,text):
        path=self.source/name; path.write_text(text,encoding='utf-8'); return path

    def test_aliases_and_spaced_ocr_without_title(self):
        self.file('202609.txt','지 급 내 역 3,000,000\n공 제 내 역 300,000\n실 지 급 액 2,700,000')
        self.file('attachment.txt','Basic pay 4000\nIncome tax 300\nNet earnings 3700\nPay date 2026-09-21')
        self.file('partial.txt','본봉 3000000\n장기요양보험 10000')
        self.file('meeting.txt','회의 안건: 지급내역과 공제내역을 다시 검토하기')
        self.file('invoice.txt','공급가액 3000000\n부가세 300000\n입금액 3300000')
        self.library.index([self.source])
        rows,_=self.library.smart_search('급여명세서',[self.source])
        self.assertEqual({r['name'] for r in rows},{'202609.txt','attachment.txt','partial.txt'})
        self.assertTrue(all(r['group']=='관련 후보' for r in rows))
        self.assertTrue(payroll_title('급 여 명 세 서'))

    def test_old_index_is_read_again_once(self):
        path=self.file('attachment.txt','지급내역 3000000 공제내역 300000')
        self.library.index([self.source])
        with self.library.connect() as c:
            c.execute("UPDATE files SET body='',category='기타 문서'")
            c.execute('DELETE FROM index_versions')
        self.library.index([self.source])
        rows,_=self.library.smart_search('급여명세서',[self.source])
        self.assertEqual([r['path'] for r in rows],[str(path)])
        with patch('core.extract',side_effect=AssertionError('unchanged files should stay cached')):
            self.library.index([self.source])

    def test_payroll_mention_in_a_report_is_not_a_green_match(self):
        self.file('report.txt','신고서\n'+'신고 내용 설명 '*150+'첨부: 급여명세서를 확인할 수 있습니다.')
        self.file('note.txt','급여명세서를 요청합니다. 소득세도 확인해 주세요.')
        self.file('attachment.txt','2026년 9월 급 여 명 세 서\n기본급 3000000\n공제총액 200000\n실수령액 2800000')
        self.library.index([self.source])
        rows,_=self.library.smart_search('급여명세서 찾아줘',[self.source])
        groups={r['name']:r['group'] for r in rows}
        self.assertEqual(groups,{'report.txt':'관련 후보','note.txt':'관련 후보','attachment.txt':'일치하는 파일'})

    def test_real_image_and_mixed_pdf_ocr(self):
        from PIL import Image,ImageDraw,ImageFont
        import pymupdf
        picture=self.source/'스캔본.png'
        image=Image.new('RGB',(1400,1100),'white'); draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('C:/Windows/Fonts/malgun.ttf',44)
        lines=['2026년 9월','사번: 042','기본급     3,000,000','국민연금     100,000',
               '건강보험      80,000','소득세        50,000','실수령액   2,770,000']
        for i,line in enumerate(lines): draw.text((100,70+i*125),line,font=font,fill='black')
        image.save(picture)
        pdf=self.source/'첨부파일.pdf'
        doc=pymupdf.open(); cover=doc.new_page()
        cover.insert_text((40,60),'Monthly attachment. The following page contains the scanned document.')
        page=doc.new_page(width=700,height=550); page.insert_image(page.rect,filename=str(picture))
        doc.save(pdf); doc.close()
        self.library.index([self.source])
        rows,_=self.library.smart_search('급여명세서',[self.source])
        self.assertEqual({r['name'] for r in rows},{picture.name,pdf.name},
                         [(r['name'],r['status'],r['body']) for r in self.library.rows()])
        self.assertTrue(all('본문 단서' in r['reason'] for r in rows))


if __name__=='__main__': unittest.main()
