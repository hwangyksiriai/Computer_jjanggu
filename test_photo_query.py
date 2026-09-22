"""User-language regression cases for broad photo search and corrections."""
import unittest

from photo_query import is_photo_followup,resolve_photo_query
from visual_query import normalize_photo_request,visual_intent


class PhotoQueryTests(unittest.TestCase):
    def test_polite_variants_keep_the_same_broad_intent(self):
        variants=('사진','사진 찾아줘','사진 좀 보여줘','사진 찾아주세요','사진을 찾아 주세요!',
                  '사진을 좀 보여 주세요','내 사진 전부 보여줘','이미지 좀 보여줄 수 있나요?',
                  '짱구야 사진 보여줘','모든 사진 보여주세요','사진 좀 보고 싶어요')
        expected=visual_intent('사진 찾아줘')
        for query in variants:
            with self.subTest(query=query):
                actual=visual_intent(query)
                self.assertTrue(actual['active']);self.assertTrue(actual['broad'])
                self.assertEqual(actual['prompt'],expected['prompt'])

    def test_request_words_do_not_erase_unknown_content(self):
        for query in ('제주도 사진 좀 보여주세요','작년 사진 찾아줘','미나가 보여준 사진 찾아줘',
                      '여름 사진','IMG_2024 사진 찾아줘'):
            with self.subTest(query=query):self.assertFalse(visual_intent(query)['broad'])
        self.assertEqual(normalize_photo_request('미나가 보여준 사진 좀 보여줘'),'미나가 보여준 사진')

    def test_seaside_synonyms(self):
        prompts=[]
        for query in ('바다 사진 찾아줘','바닷가 사진 찾아주세요','해변 사진 좀 보여줘'):
            intent=visual_intent(query)
            self.assertEqual(intent['objects'],['sea']);prompts.append(intent['prompt'])
        self.assertEqual(len(set(prompts)),1)

    def test_indoor_and_brightness_are_explicit(self):
        intent=visual_intent('어두운 실내에 파란 의자가 있는 사진')
        self.assertEqual(intent['scene'],'indoor');self.assertEqual(intent['brightness'],'dark')
        self.assertIn('indoor',intent['prompt']);self.assertIn('low-light',intent['prompt'])
        self.assertTrue(intent['object_color'])
        self.assertFalse(visual_intent('파란 배경에 의자가 있는 사진')['object_color'])

    def test_exclusion_forms(self):
        for query in ('사람 없는 사진','사람은 없어','사람이 안 보이는 사진','사람을 제외한 사진'):
            with self.subTest(query=query):
                intent=visual_intent(query)
                self.assertEqual(intent['excluded_objects'],['person']);self.assertEqual(intent['objects'],[])

    def test_documents_are_never_photo_corrections(self):
        for query in ('파란 표지 급여명세서 찾아줘','밝은 사진 제안서','그중 PDF만'):
            with self.subTest(query=query):
                self.assertFalse(visual_intent(query)['active'])
                self.assertFalse(is_photo_followup(query,'파란 의자 사진 찾아줘'))
        self.assertFalse(is_photo_followup('더 어두워','급여명세서 찾아줘'))

    def test_short_correction_preserves_place_and_object(self):
        result=resolve_photo_query('더 어두워','제주도 의자 있는 사진 찾아줘')
        self.assertIn('제주도',result)
        intent=visual_intent(result)
        self.assertEqual(intent['objects'],['chair']);self.assertEqual(intent['brightness'],'dark')

    def test_replaces_conflicting_color_and_brightness_and_scene(self):
        result=resolve_photo_query('이거 말고 빨간 배경','밝은 파란 의자 실내 사진 찾아줘')
        result=resolve_photo_query('더 어두워',result)
        result=resolve_photo_query('야외야',result)
        intent=visual_intent(result)
        self.assertEqual(intent['colors'],['red']);self.assertEqual(intent['brightness'],'dark')
        self.assertEqual(intent['scene'],'outdoor');self.assertFalse(intent['object_color'])
        self.assertEqual(intent['objects'],['chair'])

    def test_object_absence_can_be_changed_to_presence(self):
        result=resolve_photo_query('사람 없는 것','사람 있는 파란 사진 찾아줘')
        intent=visual_intent(result)
        self.assertEqual(intent['excluded_objects'],['person']);self.assertEqual(intent['objects'],[])
        result=resolve_photo_query('사람이 있어',result)
        intent=visual_intent(result)
        self.assertEqual(intent['objects'],['person']);self.assertEqual(intent['excluded_objects'],[])
        self.assertEqual(intent['colors'],['blue'])

    def test_new_search_resets_old_context(self):
        for query in ('고양이 사진 찾아줘','사진 좀 보여주세요','급여명세서 찾아줘','더러운 파란 의자 사진 찾아줘'):
            with self.subTest(query=query):
                self.assertFalse(is_photo_followup(query,'파란 의자 사진 찾아줘'))
                self.assertEqual(resolve_photo_query(query,'파란 의자 사진 찾아줘'),query)
        self.assertEqual(resolve_photo_query('더 어두워',''),'더 어두워')

    def test_polite_short_correction_is_still_a_correction(self):
        self.assertTrue(is_photo_followup('좀 어두워','파란 의자 사진 찾아줘'))
        result=resolve_photo_query('사람이 있어','파란 사진 사람은 안 보이는 것')
        self.assertNotIn('안 보이는',result)
        self.assertEqual(visual_intent(result)['objects'],['person'])


if __name__=='__main__':unittest.main()
