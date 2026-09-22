import unittest
from search_status import coverage_for,summary

class SearchStatusTests(unittest.TestCase):
    def test_image_without_text_is_not_failed_document(self):
        coverage=coverage_for([
            dict(path='a.pdf',body='',status='암호 문서 · 이름만 검색'),
            dict(path='photo.jpg',body='',visual=[1,0]),
            dict(path='b.txt',body='문서 본문')],{'.jpg'})
        self.assertEqual(coverage['unread_documents'],1)
        self.assertEqual(coverage['visual'],1)
        self.assertIn('본문을 읽지 못한 문서 1개',summary(0,coverage))
        self.assertIn('현재 확인한',summary(0,coverage))
    def test_pending_and_sample_never_claim_all_files_searched(self):
        text=summary(3,{},indexing=True,sample=True)
        self.assertIn('연습용',text); self.assertIn('분석 중',text)
    def test_pending_is_not_reported_as_unreadable_document(self):
        coverage=coverage_for([dict(path='a.pdf',body='',status='분석 대기 · 이름만 검색'),
                               dict(path='b.pdf',body='',status='암호 문서 · 이름만 검색')],set())
        self.assertEqual(coverage['pending'],1); self.assertEqual(coverage['unread_documents'],1)
        self.assertIn('자동으로 갱신',summary(0,coverage,indexing=True))
        self.assertIn('기다리는',summary(0,coverage))

if __name__=='__main__':unittest.main()
