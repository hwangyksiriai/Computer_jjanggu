import time
import unittest
from result_refinement import ResultRefinement

class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(path='a.PDF',name='invoice',body='Acme 150',fields={'currencies':['USD']},mtime=time.time()),
                   dict(path='b.pdf',name='invoice',body='Other 150',fields={'currencies':['KRW']},mtime=1),
                   dict(path='c.txt',name='invoice',body='Acme 250',fields={},mtime=time.time())]
    def test_intersection_and_undo(self):
        m=ResultRefinement(self.rows); m.facet('pdf'); m.facet('currency','USD')
        self.assertEqual([r['path'] for r in m.rows],['a.PDF'])
        m.facet('text','other'); self.assertEqual(m.rows,[])
        m.back(); self.assertEqual(len(m.rows),1)
        m.reset(); self.assertEqual(m.rows,self.rows)
    def test_words_and_dates(self):
        m=ResultRefinement(self.rows); m.facet('text','ACME 150'); self.assertEqual(len(m.rows),1)
        m.reset(); m.facet('recent'); self.assertEqual(len(m.rows),2)
    def test_followup_cannot_expand(self):
        m=ResultRefinement(self.rows); m.facet('currency','USD')
        m.narrow('followup',self.rows+[{'path':'outside.pdf'}])
        self.assertEqual([r['path'] for r in m.rows],['a.PDF'])
    def test_missing_metadata_is_not_a_match(self):
        m=ResultRefinement([{'path':'x.pdf'}]); m.facet('currency','USD'); self.assertEqual(m.rows,[])

if __name__=='__main__': unittest.main()
