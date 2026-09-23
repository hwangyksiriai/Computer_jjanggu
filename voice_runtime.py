"""Optional self-contained CPU voice runtime; no system Python or PATH changes."""
import json,os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile
import time
from app_paths import DATA,runtime_root
from setup_qwen_voice import check_cancelled,download_file,install_weights,weights_ready

def voice_python(runtime=None):
    runtime=Path(runtime or runtime_root())
    for path in (runtime/'voice-python/python.exe',runtime/'voice-env/Scripts/python.exe'):
        if path.is_file():return path
    raise RuntimeError('목소리 설정에서 음성 엔진 준비를 먼저 눌러 주세요.')

def _components_ready(runtime):
    roots=(runtime/'qwen-packages',runtime/'voice-env/Lib/site-packages')
    modules=('qwen_tts','torch','torchaudio','transformers','numpy','accelerate','librosa')
    return all(any((root/name/'__init__.py').is_file() for root in roots) for name in modules) and any(
        (root/'soundfile.py').is_file() or (root/'soundfile/__init__.py').is_file() for root in roots)

def ready(runtime=None):
    runtime=Path(runtime or runtime_root())
    try:return voice_python(runtime).is_file() and _components_ready(runtime) and weights_ready(runtime)
    except (OSError,RuntimeError):return False

def _run(command,log,env,cancelled,timeout=None):
    check_cancelled(cancelled)
    process=subprocess.Popen(command,stdout=log,stderr=log,creationflags=0x08000000 if os.name=='nt' else 0,env=env)
    started=time.monotonic()
    try:
        while process.poll() is None:
            check_cancelled(cancelled)
            if timeout is not None and time.monotonic()-started>timeout:raise RuntimeError('음성 실행 환경 검사가 너무 오래 걸려 중단했어요.')
            time.sleep(.25)
        check_cancelled(cancelled)
        return process.returncode
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

def _verify_command(python,runtime):
    code='import sys; sys.path.insert(0, '+repr(str(runtime/'qwen-packages'))+'); '
    code+='import qwen_tts,torch,torchaudio,transformers,numpy,soundfile; '
    code+='assert transformers.__version__ == "4.57.3"; assert torch.__version__.startswith("2.8.0"); assert torchaudio.__version__.startswith("2.8.0")'
    return [str(python),'-c',code]

def _install_path(path):
    value=str(Path(path).resolve())
    if os.name!='nt' or value.startswith('\\\\?\\'):return value
    if value.startswith('\\\\'):return '\\\\?\\UNC\\'+value[2:]
    return '\\\\?\\'+value

def _package_command(python,runtime):
    # One resolver run avoids a second --target install silently mixing Torch versions.
    # Local version pins prevent PyPI CUDA wheels from replacing CPU wheels.
    command=[str(python),'-m','pip','install','--upgrade','--target',_install_path(runtime/'qwen-packages'),
            'torch==2.8.0+cpu','torchaudio==2.8.0+cpu','qwen-tts==0.1.1','transformers==4.57.3',
            '--extra-index-url','https://download.pytorch.org/whl/cpu','--no-warn-script-location']
    # Embedded Python's _pth ignores the isolated build environment used for SoX.
    if python.parent==runtime/'voice-python':command.append('--no-build-isolation')
    return command

def install(progress=lambda text:None,cancelled=lambda:False):
    check_cancelled(cancelled)
    runtime=runtime_root();runtime.mkdir(parents=True,exist_ok=True)
    DATA.mkdir(parents=True,exist_ok=True)
    # An existing Python executable does not mean its libraries/models are installed.
    if not ready(runtime) and shutil.disk_usage(runtime).free<8*1024**3:
        raise RuntimeError('음성 엔진을 준비하려면 여유 공간 8GB가 필요해요.')
    try:python=voice_python(runtime)
    except RuntimeError:python=None
    embedded=runtime/'voice-python'
    if python is None or python.parent==embedded and not all((embedded/name).is_file() for name in ('python312.dll','python312.zip')):
        progress('음성 실행 환경 준비 중…')
        embedded.mkdir(exist_ok=True)
        archive=runtime/'python-3.12.10-embed-amd64.zip'
        download_file('https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip',archive,cancelled)
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                check_cancelled(cancelled)
                if not (embedded/item.filename).resolve().is_relative_to(embedded.resolve()):raise ValueError('설치 압축 경로를 확인할 수 없어요.')
            for item in source.infolist():
                check_cancelled(cancelled);source.extract(item,embedded)
        python=embedded/'python.exe'
    if python.parent==embedded:
        (embedded/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\n../qwen-packages\nimport site\n',encoding='utf-8')
    install_env=os.environ.copy()
    # Torch has deeply nested headers. A workspace-relative pip temp folder can
    # exceed Windows MAX_PATH before files even reach the extended-length target.
    # Packaged Windows hosts may redirect even this short-looking temp root to
    # a longer LocalCache path. Keep the prefix for creation, pip and cleanup.
    with tempfile.TemporaryDirectory(prefix='JjangguVoice-',dir=_install_path(tempfile.gettempdir())) as temp,(DATA/'voice-setup.log').open('a',encoding='utf-8') as log:
        install_env.update(PIP_CACHE_DIR=_install_path(runtime/'pip-cache'),TEMP=_install_path(temp),TMP=_install_path(temp))
        valid=_components_ready(runtime) and _run(_verify_command(python,runtime),log,install_env,cancelled,timeout=180)==0
        if not valid:
            if python.parent==embedded:
                bootstrap=embedded/'get-pip.py'
                if not bootstrap.is_file():download_file('https://bootstrap.pypa.io/get-pip.py',bootstrap,cancelled)
                progress('음성 설치 도구 준비 중…')
                if _run([str(python),str(bootstrap),'--no-warn-script-location'],log,install_env,cancelled):
                    raise RuntimeError('음성 설치 도구를 준비하지 못했어요. 진단 기록을 확인해 주세요.')
                if _run([str(python),'-m','pip','install','--upgrade','setuptools==84.0.0','wheel','packaging','--no-warn-script-location'],log,install_env,cancelled):
                    raise RuntimeError('음성 구성 요소의 설치 도구를 준비하지 못했어요. 진단 기록을 확인해 주세요.')
            progress('음성 구성 요소 설치 중 · 처음 한 번만 필요해요.')
            if _run(_package_command(python,runtime),log,install_env,cancelled):
                raise RuntimeError('음성 구성 요소 설치가 중단됐어요. 설정의 진단 기록을 확인해 주세요.')
            if _run(_verify_command(python,runtime),log,install_env,cancelled,timeout=180):
                raise RuntimeError('음성 실행 환경 검사에 실패했어요. 음성 엔진 준비를 다시 눌러 주세요.')
        if python.parent==embedded:
            (embedded/'ready.json').write_text(json.dumps({'qwen_tts':'0.1.1','torch':'2.8.0+cpu','transformers':'4.57.3'}),encoding='utf-8')
    progress('참고 목소리 합성 모델 준비 중…')
    install_weights(runtime,progress,quality=False,cancelled=cancelled)
    return True
