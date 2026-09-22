"""Search constraints, using controlled embeddings rather than model quality claims."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from knowledge import Knowledge
from visual_query import visual_intent

class AI:
    def ready(self): return True
    def embed(self,*args,**kwargs): return [[1.,0.]]
    def call(self,op,**kwargs):
        if op=='visual_text': return [1.,0.]
        raise AssertionError(op)

class VisualSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name)
        self.source=self.base/'pictures'; self.source.mkdir()
        self.k=Knowledge(self.base/'state')
        for name,color in [('001.png','blue'),('002.png','red'),('003.png','blue')]:
            Image.new('RGB',(64,64),color).save(self.source/name)
        (self.source/'blue-chair.txt').write_text('파란 사진 의자',encoding='utf8')
        with patch('core.extract',return_value=('', '테스트 이미지')):
            self.k.index([self.source])
        with self.k.connect() as c:
            c.execute("UPDATE knowledge SET visual='[1,0]',vectors='[[1,0]]'")
            c.execute("UPDATE knowledge SET visual='[0,1]' WHERE path=?",(str(self.source/'003.png'),))
        self.k.ai=AI()
    def tearDown(self): self.temp.cleanup()
    def test_color_and_object_both_required(self):
        rows,_=self.k.smart_search('사진이 전반적으로 파란데 거기에 의자가 있어 찾아줘',[self.source])
        self.assertEqual([r['name'] for r in rows],['001.png'])
        self.assertEqual(rows[0]['group'],'관련 후보')
        self.assertIn('100%',rows[0]['reason'])
    def test_no_ai_does_not_claim_object_found(self):
        self.k.ai=None
        rows,_=self.k.smart_search('파란 사진 의자 찾아줘',[self.source])
        self.assertEqual(rows,[])
        self.assertIn('AI 준비',self.k.search_notice)
    def test_color_without_models(self):
        self.k.ai=None
        rows,_=self.k.smart_search('파란 사진 찾아줘',[self.source])
        self.assertEqual({r['name'] for r in rows},{'001.png','003.png'})
    def test_missing_visual_is_not_a_negative_result(self):
        with self.k.connect() as c: c.execute("UPDATE knowledge SET visual='[]'")
        rows,_=self.k.smart_search('의자가 있는 사진 찾아줘',[self.source])
        self.assertFalse(rows); self.assertIn('0개 분석됨',self.k.search_notice)
    def test_document_is_not_filtered_as_photo(self):
        self.assertFalse(visual_intent('파란 표지 급여명세서 찾아줘')['active'])

    def test_refresh_reuses_visual_query_plan_instead_of_reloading_models(self):
        with patch.object(self.k.ai,'call',wraps=self.k.ai.call) as call:
            first,_=self.k.smart_search('의자 사진 찾아줘',[self.source])
            initial=call.call_count
            self.assertGreater(initial,0)
            for _ in range(3):
                found,_=self.k.smart_search('의자 사진 찾아줘',[self.source])
                self.assertEqual([r['path'] for r in first],[r['path'] for r in found])
            self.assertEqual(call.call_count,initial)

    def test_initial_search_returns_detected_objects_without_loading_model(self):
        self.set_detections('001.png',[dict(label='chair',score=.95,box={},colors={'blue':1})])
        with patch.object(self.k.ai,'call',side_effect=AssertionError('should show cached results first')):
            found,_=self.k.smart_search('의자 사진 찾아줘',[self.source],allow_model=False)
        self.assertEqual([r['name'] for r in found],['001.png'])

    def test_empty_analysis_does_not_load_chat_or_block_first_photo_analysis(self):
        with self.k.connect() as c:c.execute("UPDATE knowledge SET visual='[]'")
        with patch.object(self.k.ai,'call',side_effect=AssertionError('no analyzed photos yet')) as call:
            self.k.smart_search('생일 케이크 사진 찾아줘',[self.source])
            call.assert_not_called()

    def test_translation_is_cached_and_its_worker_memory_is_released(self):
        with patch.object(self.k.ai,'call',side_effect=lambda op,**kw:'a birthday cake' if op=='chat' else [1.,0.]) as call,patch.object(self.k.ai,'close',create=True) as close:
            self.k.smart_search('생일 케이크 사진 찾아줘',[self.source])
            count=call.call_count
            self.k.smart_search('생일 케이크 사진 찾아줘',[self.source])
            self.assertEqual(call.call_count,count)
            self.assertEqual([c.args[0] for c in call.call_args_list].count('chat'),1)
            close.assert_called_once()

    def test_polite_broad_photo_queries_return_every_image_without_ai(self):
        self.k.ai=None
        for query in ('사진 찾아줘','사진 좀 보여줘','사진 찾아주세요','사진을 찾아 주세요'):
            with self.subTest(query=query):
                rows,_=self.k.smart_search(query,[self.source])
                self.assertEqual({r['name'] for r in rows},{'001.png','002.png','003.png'})

    def set_detections(self,name,objects):
        import json
        with self.k.connect() as c:
            path=str(self.source/name);fields=json.loads(c.execute('SELECT fields FROM knowledge WHERE path=?',(path,)).fetchone()[0])
            fields.update(_objects_version=1,objects=objects)
            c.execute('UPDATE knowledge SET fields=? WHERE path=?',(json.dumps(fields),path))

    def test_analyzed_photo_requires_actual_chair_detection(self):
        self.set_detections('001.png',[])
        self.set_detections('003.png',[dict(label='chair',score=.9,box={},colors={'red':1})])
        rows,_=self.k.smart_search('전반적으로 파란 사진에 의자가 있어 찾아줘',[self.source])
        self.assertEqual([r['name'] for r in rows],['003.png'])
        self.assertTrue(rows[0]['detected_objects'])

    def test_blue_background_does_not_establish_blue_chair(self):
        self.set_detections('001.png',[dict(label='chair',score=.9,box={},colors={'red':1})])
        self.set_detections('002.png',[dict(label='chair',score=.9,box={},colors={'blue':1})])
        self.set_detections('003.png',[])
        rows,_=self.k.smart_search('파란 의자 사진 찾아줘',[self.source])
        self.assertEqual([r['name'] for r in rows],['002.png'])

    def test_negative_object_only_uses_analyzed_photos(self):
        self.set_detections('001.png',[])
        self.set_detections('003.png',[dict(label='chair',score=.9,box={},colors={})])
        rows,_=self.k.smart_search('의자 없는 사진 찾아줘',[self.source])
        self.assertEqual([r['name'] for r in rows],['001.png'])

if __name__=='__main__': unittest.main()
