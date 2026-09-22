import unittest
from voice_quality import content_check

class VoiceQualityTests(unittest.TestCase):
    def test_same_content_ignores_spacing_and_punctuation(self):
        self.assertTrue(content_check('알았어. 인보이스를 찾아볼게.','알았어 인보이스를 찾아 볼게')['ok'])
    def test_unrelated_clip_and_repetition_are_blocked(self):
        self.assertFalse(content_check('파일 세 개를 찾았어.','감사합니다')['ok'])
        self.assertFalse(content_check('파일 세 개를 찾았어.','하이'*30)['ok'])
    def test_wrong_numeric_count_is_blocked(self):
        self.assertFalse(content_check('파일 12개를 찾았어.','파일 13개를 찾았어.')['ok'])
        self.assertFalse(content_check('파일 세 개를 찾았어.','파일 네 개를 찾았어.')['ok'])
        self.assertTrue(content_check('파일 12개를 찾았어.','파일 열두 개를 찾았어.')['ok'])
