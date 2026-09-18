import threading,unittest
from unittest.mock import patch
from app import Voice
from qwen_voice import speech_chunks

class VoiceRoutingTests(unittest.TestCase):
    def test_chunks_preserve_words(self):
        text='오늘은 어떤 일을 도와줄까? '+('파일을 확인하고 결과를 알려줄게. '*12)
        chunks=speech_chunks(text)
        self.assertTrue(all(0<len(c)<=90 for c in chunks))
        self.assertEqual(''.join(''.join(chunks).split()),''.join(text.split()))

    def test_speaks_requested_text_not_greeting(self):
        finished=threading.Event();seen=[]
        class Engine:
            def generate(self,text,reference):seen.append((text,reference));return 'generated.wav'
        messages=[];voice=Voice(messages.append);voice.qwen_client=Engine()
        with patch.object(voice,'play_clip',side_effect=lambda *a,**kw:finished.set()) as play,patch('app.subprocess.Popen') as system_tts:
            voice.say('파일 세 개를 찾았어.',{'sound':True,'voice_mode':'qwen_local','voice_reference':'reference.wav','voice_clips':{'greeting':'unrelated.wav'}})
            self.assertTrue(finished.wait(3))
            self.assertEqual(seen,[('파일 세 개를 찾았어.','reference.wav')])
            self.assertEqual(play.call_args.args[0],'generated.wav');system_tts.assert_not_called()
            self.assertFalse(any('실패' in m for m in messages),messages)

    def test_cancel_discards_late_audio(self):
        started=threading.Event();release=threading.Event();done=threading.Event()
        class Engine:
            def generate(self,*args):started.set();release.wait(3);done.set();return 'late.wav'
        voice=Voice(lambda e:None);voice.qwen_client=Engine()
        with patch.object(voice,'play_clip') as play:
            voice.say('찾아볼게.',{'sound':True,'voice_mode':'qwen_local','voice_reference':'ref.wav'})
            self.assertTrue(started.wait(3));voice.stop();release.set();self.assertTrue(done.wait(3))
            play.assert_not_called()

if __name__=='__main__':unittest.main()
