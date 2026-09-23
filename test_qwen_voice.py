import io,json,queue,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app import Voice
from qwen_voice import QwenVoiceClient,speech_chunks

class VoiceRoutingTests(unittest.TestCase):
    def test_chunks_preserve_words(self):
        text='오늘은 어떤 일을 도와줄까? '+('파일을 확인하고 결과를 알려줄게. '*12)
        chunks=speech_chunks(text)
        self.assertTrue(all(0<len(c)<=90 for c in chunks))
        self.assertEqual(''.join(''.join(chunks).split()),''.join(text.split()))

    def test_speaks_requested_text_not_greeting(self):
        finished=threading.Event();seen=[]
        class Engine:
            def generate(self,text,reference,**kwargs):seen.append((text,reference));return 'generated.wav'
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
            def generate(self,*args,**kwargs):started.set();release.wait(3);done.set();return 'late.wav'
        voice=Voice(lambda e:None);voice.qwen_client=Engine()
        with patch.object(voice,'play_clip') as play:
            voice.say('찾아볼게.',{'sound':True,'voice_mode':'qwen_local','voice_reference':'ref.wav'})
            self.assertTrue(started.wait(3));voice.stop();release.set();self.assertTrue(done.wait(3))
            play.assert_not_called()

    def test_unrelated_original_recording_is_not_used_as_an_answer(self):
        voice=Voice(lambda message:None)
        with patch.object(voice,'play_clip') as play,patch('app.subprocess.Popen') as system_tts:
            voice.say('돋보기 들고 찾아볼게!',{'sound':True,'voice_mode':'original_only','voice_clips':{'search':__file__}})
            play.assert_not_called();system_tts.assert_not_called()

class VoiceVerificationTests(unittest.TestCase):
    def test_cancel_interrupts_blocked_asr_and_next_request_can_verify(self):
        from local_ai import LocalAI
        entered=threading.Event();cancelled=threading.Event();finished=threading.Event()
        errors=[];requests=[]
        class Process:
            def __init__(self,stdin):self.stdin=stdin;self.exitcode=None
            def poll(self):return self.exitcode
            def terminate(self):self.exitcode=0
        class ASRInput(io.StringIO):
            def write(self,text):
                request=json.loads(text);requests.append(request)
                if len(requests)>1:
                    verifier.responses.put({'ok':True,'id':request['id'],'value':'파일을 찾아볼게.'})
                entered.set()
                return super().write(text)
        verifier=LocalAI();original_cancelled=verifier.cancelled
        def start():
            if verifier.process and verifier.process.poll() is None:return
            verifier.responses=queue.Queue();verifier.process=Process(ASRInput())
        def transcribe(ai,path):return ai.call('transcribe',audio=[0.0],timeout=10)
        client=QwenVoiceClient();client.verifier=verifier
        client.process=Process(io.StringIO());client.responses=queue.Queue()
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder);cache=state/'voice-generated';cache.mkdir()
            output=cache/'reply.wav';output.write_bytes(b'generated audio')
            reference=state/'reference.wav';reference.write_bytes(b'reference audio')
            reference.with_suffix('.json').write_text(json.dumps({'text':'참고 대사'}),encoding='utf-8')
            client.responses.put({'ok':True,'path':str(output)})
            def generate():
                try:client.generate('파일을 찾아볼게.',str(reference),cancelled.is_set)
                except Exception as error:errors.append(str(error))
                finally:finished.set()
            with patch('qwen_voice.DATA',state),patch('voice_quality.transcribe_wav',transcribe), \
                 patch.object(verifier,'start',start),patch.object(verifier,'_require_operation'):
                thread=threading.Thread(target=generate,daemon=True);thread.start()
                try:
                    self.assertTrue(entered.wait(3),'ASR did not start')
                    cancelled.set()
                    self.assertTrue(finished.wait(3),'Cancelled ASR kept the speech request blocked')
                    self.assertTrue(errors)
                    self.assertFalse(list(cache.glob('*.json')),'Cancelled speech was cached as verified')
                    self.assertIs(verifier.cancelled,original_cancelled)
                    client.responses.put({'ok':True,'path':str(output)})
                    self.assertEqual(client.generate('파일을 찾아볼게.',str(reference)),str(output))
                    self.assertEqual(len(requests),2)
                    self.assertIs(verifier.cancelled,original_cancelled)
                finally:
                    cancelled.set();verifier.interrupt();thread.join(3);client.close()

    def test_reference_transcribes_with_speech_only_models(self):
        from voice_setup_ui import _transcribe_reference
        ai=Mock();ai.ready.return_value=False;ai.ready_for.side_effect=lambda capability:capability=='speech'
        with patch('local_ai.LocalAI',return_value=ai),patch('voice_quality.transcribe_wav',return_value='참고 대사') as transcribe:
            self.assertEqual(_transcribe_reference('reference.wav'),'참고 대사')
        ai.ready_for.assert_called_once_with('speech');ai.ready.assert_not_called()
        transcribe.assert_called_once_with(ai,'reference.wav');ai.close.assert_called_once()

    def test_reference_without_speech_models_does_not_start_transcription(self):
        from voice_setup_ui import _transcribe_reference
        ai=Mock();ai.ready_for.return_value=False
        with patch('local_ai.LocalAI',return_value=ai),patch('voice_quality.transcribe_wav') as transcribe:
            self.assertEqual(_transcribe_reference('reference.wav'),'')
        transcribe.assert_not_called();ai.close.assert_called_once()

    def test_verification_script_uses_app_client_and_closes_after_failure(self):
        from verify_qwen_voice import verify
        client=Mock();client.generate.side_effect=RuntimeError('내용 검사 실패')
        with patch('verify_qwen_voice.QwenVoiceClient',return_value=client):
            with self.assertRaisesRegex(RuntimeError,'내용 검사 실패'):
                verify(Path('reference.wav'),['뭘 찾아줄까?'])
        client.generate.assert_called_once_with('뭘 찾아줄까?','reference.wav')
        client.close.assert_called_once()

if __name__=='__main__':unittest.main()
