import io,json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from app_paths import BASE,runtime_root
from local_ai import LocalAI,MODEL_FILES,CAPABILITY_MODELS

def make_models(cache,names):
    for name in names:
        for relative in MODEL_FILES[name]:
            path=cache/name/relative;path.parent.mkdir(parents=True,exist_ok=True)
            if relative.endswith('.onnx'):
                with path.open('wb') as stream:stream.truncate(1024*1024)
            else:path.write_text('{}',encoding='utf-8')

class DiagnosticsTests(unittest.TestCase):
    def test_bad_runtime_config_does_not_prevent_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'runtime.json').write_text('{broken',encoding='utf-8')
            with patch('app_paths.DATA',root),patch('app_paths.FROZEN',True):
                self.assertEqual(runtime_root(),root/'runtime')
    def test_actual_bootstrap_import_survives_malformed_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for malformed in ('{broken','[]','null','{"runtime_root": []}','{"runtime_root":"relative-folder"}'):
                (root/'runtime.json').write_text(malformed,encoding='utf-8')
                env=dict(os.environ,JJANGGU_DATA_DIR=str(root))
                result=subprocess.run([sys.executable,'-X','utf8','-c','import bootstrap; print(bootstrap.runtime)'],cwd=BASE,
                                      env=env,capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),str(BASE))
    def test_missing_model_files_do_not_look_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'models-ready.json').write_text(json.dumps({'ready':True,'models':['Xenova/missing']}),encoding='utf-8')
            with patch('local_ai.DATA',root),patch('app_paths.runtime_root',return_value=root):
                self.assertFalse(LocalAI().ready())
    def test_photo_works_without_profile_marker_semantic_chat_or_speech(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=Path(folder)/'models';make_models(cache,CAPABILITY_MODELS['photo'])
            with patch('local_ai.model_cache',return_value=cache):
                ai=LocalAI()
                self.assertTrue(ai.ready_for('photo'));self.assertTrue(ai.detector_ready())
                self.assertFalse(ai.ready_for('semantic'));self.assertFalse(ai.ready_for('chat'))
                self.assertFalse(ai.ready_for('speech'));self.assertFalse(ai.ready())
                with self.assertRaisesRegex(RuntimeError,'자유 대화'):
                    ai.call('chat',messages=[])
    def test_ready_search_does_not_require_voice_or_chat(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=Path(folder)/'models';make_models(cache,CAPABILITY_MODELS['search'])
            with patch('local_ai.model_cache',return_value=cache):
                self.assertTrue(LocalAI().ready())
                self.assertFalse(LocalAI().ready_for('all'))
    def test_partial_onnx_download_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=Path(folder)/'models';make_models(cache,CAPABILITY_MODELS['photo'])
            weight=cache/'Xenova/detr-resnet-50/onnx/model_quantized.onnx'
            weight.write_bytes(b'partial')
            with patch('local_ai.model_cache',return_value=cache):
                self.assertTrue(LocalAI().ready_for('vision'))
                self.assertFalse(LocalAI().detector_ready())
                self.assertFalse(LocalAI().ready_for('photo'))
    def test_valid_small_speech_model_is_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=Path(folder)/'models';make_models(cache,['Xenova/whisper-tiny'])
            with patch('local_ai.model_cache',return_value=cache):
                self.assertTrue(LocalAI().ready_for('speech'))
                self.assertFalse(LocalAI().ready())
    def test_photo_setup_does_not_request_unrelated_models_and_shares_marker(self):
        from setup_runtime import install_models
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cache=root/'runtime/models';make_models(cache,CAPABILITY_MODELS['photo'])
            module=root/'modules/@huggingface/transformers/dist/transformers.node.mjs'
            module.parent.mkdir(parents=True);module.touch()
            process=Mock();process.poll.return_value=0;process.wait.return_value=0
            process.stdout=io.StringIO('{"stage":"objects","status":"ready"}\n{"setup":"complete"}\n')
            with patch('local_ai.model_cache',return_value=cache),patch('setup_runtime.model_cache',return_value=cache), \
                 patch('setup_runtime.runtime_root',return_value=cache.parent),patch('setup_runtime.DATA',root/'profile'), \
                 patch('setup_runtime.runtime_modules',return_value=root/'modules'),patch('setup_runtime.node_path',return_value='node'), \
                 patch('setup_runtime.subprocess.Popen',return_value=process) as popen:
                self.assertTrue(install_models(capability='photo'))
                self.assertIn('--setup-photo',popen.call_args.args[0])
                marker=json.loads((cache.parent/'models-ready.json').read_text('utf-8'))
                self.assertEqual(set(marker['models']),set(CAPABILITY_MODELS['photo']))
                self.assertTrue((root/'profile/models-ready.json').is_file())
    def test_interrupt_releases_pending_call_without_waiting_for_lock(self):
        ai=LocalAI();process=Mock();process.poll.return_value=None;ai.process=process
        sent=threading.Event();process.stdin.flush.side_effect=sent.set
        errors=[]
        def request():
            try:ai.call('embed',texts=['test'],timeout=5)
            except RuntimeError as error:errors.append(str(error))
        with patch.object(ai,'start'),patch.object(ai,'_require_operation'):
            thread=threading.Thread(target=request);thread.start()
            self.assertTrue(sent.wait(2));ai.interrupt();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertIn('중단',errors[0])
        process.terminate.assert_called_once()
    def test_cancelled_queued_request_does_not_restart_worker(self):
        ai=LocalAI();errors=[];waiting=threading.Event()
        # This lock explicitly confirms that call() captured its generation
        # before blocking, so the regression does not depend on sleep timing.
        held=threading.Lock();held.acquire()
        class BlockingLock:
            def __enter__(self):waiting.set();held.acquire()
            def __exit__(self,*args):held.release()
        ai.lock=BlockingLock()
        def request():
            try:ai.call('embed',texts=['test'])
            except RuntimeError as error:errors.append(str(error))
        with patch.object(ai,'start') as start,patch.object(ai,'_require_operation') as require:
            thread=threading.Thread(target=request);thread.start()
            self.assertTrue(waiting.wait(2));ai.interrupt();held.release();thread.join(2)
            start.assert_not_called();require.assert_not_called()
        self.assertFalse(thread.is_alive());self.assertIn('중단',errors[0])
    def test_cancel_callback_blocks_fresh_request_after_interrupt(self):
        ai=LocalAI();ai.cancelled=lambda:True
        with patch.object(ai,'start') as start:
            with self.assertRaisesRegex(RuntimeError,'중단'):ai.call('image',path='sample.jpg')
            start.assert_not_called()
    def test_cancel_during_startup_stops_new_child_before_sending_work(self):
        ai=LocalAI();process=Mock();process.poll.return_value=None
        def start():
            ai.interrupt()  # The UI cancels while Popen has not returned yet.
            ai.process=process
        with patch.object(ai,'start',side_effect=start),patch.object(ai,'_require_operation'):
            with self.assertRaisesRegex(RuntimeError,'중단'):ai.call('image',path='sample.jpg')
        process.terminate.assert_called_once();process.stdin.write.assert_not_called()
