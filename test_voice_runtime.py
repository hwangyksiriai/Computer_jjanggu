import hashlib,io,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import setup_qwen_voice as weights
import voice_runtime as runtime

def fake_runtime(root,legacy=False):
    python=root/('voice-env/Scripts/python.exe' if legacy else 'voice-python/python.exe')
    python.parent.mkdir(parents=True);python.touch()
    if not legacy:
        for name in ('python312.dll','python312.zip','get-pip.py','ready.json'):(python.parent/name).touch()
    packages=root/('voice-env/Lib/site-packages' if legacy else 'qwen-packages')
    for name in ('qwen_tts','torch','torchaudio','transformers','numpy','accelerate','librosa'):
        path=packages/name/'__init__.py';path.parent.mkdir(parents=True);path.touch()
    (packages/'soundfile.py').touch()
    model=root/'qwen3-tts-base';model.mkdir()
    for name in ('config.json','generation_config.json','tokenizer_config.json','vocab.json','merges.txt','speech_tokenizer/config.json'):
        path=model/name;path.parent.mkdir(exist_ok=True);path.write_text('{}',encoding='utf-8')
    for name in ('model.safetensors','speech_tokenizer/model.safetensors'):
        with (model/name).open('wb') as output:output.truncate(2*1024**2)
    repo,revision,_=weights._model_info(False)
    (model/'provenance.json').write_text(json.dumps({'repository':repo,'revision':revision}),encoding='utf-8')
    return python

class VoiceRuntimeTests(unittest.TestCase):
    def test_executable_and_one_weight_do_not_mean_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'voice-python').mkdir();(root/'voice-python/python.exe').touch()
            (root/'qwen3-tts-base').mkdir();(root/'qwen3-tts-base/model.safetensors').touch()
            (root/'voice-python/ready.json').write_text('{}')
            self.assertFalse(runtime.ready(root))

    def test_complete_legacy_runtime_stays_compatible_and_missing_tokenizer_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);python=fake_runtime(root,legacy=True)
            self.assertEqual(runtime.voice_python(root),python)
            self.assertTrue(runtime.ready(root))
            (root/'qwen3-tts-base/speech_tokenizer/model.safetensors').unlink()
            self.assertFalse(runtime.ready(root))

    def test_manifest_detects_a_truncated_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fake_runtime(root);model=root/'qwen3-tts-base';receipt=model/'provenance.json'
            data=json.loads(receipt.read_text());data['files']=[{'name':p.relative_to(model).as_posix(),'size':p.stat().st_size} for p in model.rglob('*') if p.is_file() and p!=receipt]
            receipt.write_text(json.dumps(data));self.assertTrue(weights.weights_ready(root))
            (model/'model.safetensors').write_bytes(b'incomplete')
            self.assertFalse(weights.weights_ready(root))

    def test_low_disk_blocks_partial_existing_runtime_before_download(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'voice-env/Scripts').mkdir(parents=True);(root/'voice-env/Scripts/python.exe').touch()
            with patch.object(runtime,'runtime_root',return_value=root),patch.object(runtime,'DATA',root/'profile'), \
                 patch.object(runtime.shutil,'disk_usage',return_value=SimpleNamespace(free=1024)),patch.object(runtime,'download_file') as download:
                with self.assertRaisesRegex(RuntimeError,'8GB'):runtime.install()
                download.assert_not_called()

    def test_stale_ready_marker_does_not_skip_failed_imports_or_pin_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);python=fake_runtime(root);cancelled=lambda:False
            with patch.object(runtime,'runtime_root',return_value=root),patch.object(runtime,'DATA',root/'profile'), \
                 patch.object(runtime,'_run',side_effect=[1,0,0,0,0]) as run,patch.object(runtime,'install_weights') as install_weights:
                self.assertTrue(runtime.install(cancelled=cancelled))
            commands=[call.args[0] for call in run.call_args_list]
            package_commands=[cmd for cmd in commands if '--target' in cmd]
            self.assertEqual(len(package_commands),1)
            self.assertTrue({'torch==2.8.0+cpu','torchaudio==2.8.0+cpu','qwen-tts==0.1.1','transformers==4.57.3'}<=set(package_commands[0]))
            self.assertIn('--no-build-isolation',package_commands[0])
            self.assertTrue(any('setuptools==84.0.0' in cmd and '--target' not in cmd for cmd in commands))
            install_weights.assert_called_once_with(root,unittest.mock.ANY,quality=False,cancelled=cancelled)

    def test_valid_home_runtime_is_verified_without_reinstalling(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fake_runtime(root,legacy=True)
            with patch.object(runtime,'runtime_root',return_value=root),patch.object(runtime,'DATA',root/'profile'), \
                 patch.object(runtime,'_run',return_value=0) as run,patch.object(runtime,'install_weights'):
                self.assertTrue(runtime.install())
            self.assertEqual(run.call_count,1)
            self.assertNotIn('--target',run.call_args.args[0])

    def test_install_uses_short_unique_temp_and_cleans_it_after_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fake_runtime(root);observed=[]
            def failed_run(command,log,env,cancelled,**kwargs):
                temporary=Path(env['TEMP']);observed.append(temporary)
                self.assertTrue(temporary.is_dir());self.assertEqual(env['TEMP'],env['TMP'])
                self.assertFalse(temporary.is_relative_to(root));self.assertTrue(temporary.name.startswith('JjangguVoice-'))
                if os.name=='nt':
                    self.assertTrue(env['TEMP'].startswith('\\\\?\\'))
                    # Exercise extraction and cleanup beyond MAX_PATH, including
                    # hosts whose short temp path is redirected to LocalCache.
                    nested=temporary/('torch-header-'*12)/('nested-header-'*8)
                    nested.mkdir(parents=True);(nested/'tensor.h').write_text('header')
                raise RuntimeError('interrupted')
            with patch.object(runtime,'runtime_root',return_value=root),patch.object(runtime,'DATA',root/'profile'), \
                 patch.object(runtime,'_run',side_effect=failed_run):
                with self.assertRaisesRegex(RuntimeError,'interrupted'):runtime.install()
            self.assertFalse(observed[0].exists())

    @unittest.skipUnless(os.name=='nt','Windows path handling')
    def test_package_target_accepts_long_windows_paths_and_unc(self):
        root=Path(r'C:\Users\Name\Documents')/('long-folder-'*10)
        command=runtime._package_command(root/'voice-python/python.exe',root)
        target=command[command.index('--target')+1]
        self.assertEqual(target,'\\\\?\\'+str(root/'qwen-packages'))
        self.assertEqual(runtime._install_path(target),target)
        with patch.object(runtime.Path,'resolve',return_value=Path(r'\\server\share\voice')):
            self.assertEqual(runtime._install_path('unused'),r'\\?\UNC\server\share\voice')

class VoiceDownloadTests(unittest.TestCase):
    def test_cancelled_setup_makes_no_network_request(self):
        with patch.object(weights.urllib.request,'urlopen') as request:
            with self.assertRaisesRegex(RuntimeError,'중단'):weights.install_weights('unused',cancelled=lambda:True)
            request.assert_not_called()

    def test_cancel_during_download_does_not_publish_partial_audio_model(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'model';response=io.BytesIO(b'x'*(2*1024**2))
            with patch.object(weights.urllib.request,'urlopen',return_value=response):
                with self.assertRaisesRegex(RuntimeError,'중단'):
                    weights.download_file('https://example.test/model',path,lambda:response.tell()>1024**2,size=2*1024**2)
            self.assertFalse(path.exists());self.assertFalse(path.with_suffix('.part').exists())

    def test_bad_checksum_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'model';path.write_bytes(b'previous')
            with patch.object(weights.urllib.request,'urlopen',return_value=io.BytesIO(b'broken')):
                with self.assertRaisesRegex(RuntimeError,'확인에 실패'):
                    weights.download_file('https://example.test/model',path,size=6,sha256=hashlib.sha256(b'correct').hexdigest())
            self.assertEqual(path.read_bytes(),b'previous');self.assertFalse(path.with_suffix('.part').exists())

    def test_path_traversal_is_rejected_before_creating_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);metadata={'siblings':[{'rfilename':'speech_tokenizer/../../outside/nested/file','size':4}]}
            with patch.object(weights.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(metadata).encode())):
                with self.assertRaisesRegex(RuntimeError,'경로 오류'):weights.install_weights(root,quality=False)
            self.assertFalse((root/'outside').exists())

    def test_cancelled_installer_terminates_child_process(self):
        process=Mock();process.poll.return_value=None
        with patch.object(runtime.subprocess,'Popen',return_value=process),patch.object(runtime.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'중단'):
                runtime._run(['python'],None,{},Mock(side_effect=[False,True]))
        process.terminate.assert_called_once();process.wait.assert_called_once_with(timeout=5)

if __name__=='__main__':unittest.main()
