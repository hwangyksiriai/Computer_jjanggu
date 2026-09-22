"""Optional self-contained CPU voice runtime; no system Python or PATH changes."""
import json,os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile
import time
from app_paths import DATA,runtime_root

def voice_python(runtime=None):
    runtime=Path(runtime or runtime_root())
    for path in (runtime/'voice-python/python.exe',runtime/'voice-env/Scripts/python.exe'):
        if path.is_file():return path
    raise RuntimeError('목소리 설정에서 음성 엔진 준비를 먼저 눌러 주세요.')

def ready():
    try:return voice_python().is_file() and (runtime_root()/'qwen3-tts-base/model.safetensors').is_file()
    except RuntimeError:return False

def install(progress=lambda text:None,cancelled=lambda:False):
    runtime=runtime_root();runtime.mkdir(parents=True,exist_ok=True)
    DATA.mkdir(parents=True,exist_ok=True)
    try:python=voice_python(runtime)
    except RuntimeError:python=None
    if python is None:
        if shutil.disk_usage(runtime).free<8*1024**3:raise RuntimeError('음성 엔진을 준비하려면 여유 공간 8GB가 필요해요.')
        progress('음성 실행 환경 준비 중…')
        target=runtime/'voice-python';target.mkdir(exist_ok=True)
        archive=runtime/'python-3.12.10-embed-amd64.zip'
        urllib.request.urlretrieve('https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip',archive)
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                if not (target/item.filename).resolve().is_relative_to(target.resolve()):raise ValueError('설치 압축 경로를 확인할 수 없어요.')
            source.extractall(target)
        (target/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\n../qwen-packages\nimport site\n',encoding='utf-8')
        bootstrap=target/'get-pip.py'
        urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py',bootstrap)
        python=target/'python.exe'
    if (runtime/'voice-python/python.exe').is_file() and not (runtime/'voice-python/ready.json').is_file():
        bootstrap=runtime/'voice-python/get-pip.py'
        if not bootstrap.is_file():urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py',bootstrap)
        install_env=os.environ.copy()
        temp=runtime/'voice-temp';temp.mkdir(exist_ok=True)
        install_env.update(PIP_CACHE_DIR=str(runtime/'pip-cache'),TEMP=str(temp),TMP=str(temp))
        with (DATA/'voice-setup.log').open('a',encoding='utf-8') as log:
            commands=[[str(python),str(bootstrap),'--no-warn-script-location'],
                      [str(python),'-m','pip','install','--target',str(runtime/'qwen-packages'),'torch==2.8.0','torchaudio==2.8.0','--index-url','https://download.pytorch.org/whl/cpu'],
                      [str(python),'-m','pip','install','--target',str(runtime/'qwen-packages'),'qwen-tts==0.1.1','transformers==4.57.3']]
            for i,command in enumerate(commands):
                progress(f'음성 구성 요소 설치 {i+1}/3 · 처음 한 번만 필요해요.')
                process=subprocess.Popen(command,stdout=log,stderr=log,creationflags=0x08000000,env=install_env)
                while process.poll() is None:
                    if cancelled():process.terminate();process.wait();raise RuntimeError('음성 준비를 중단했어요.')
                    time.sleep(.25)
                if process.returncode:raise RuntimeError('음성 구성 요소 설치가 중단됐어요. 설정의 진단 기록을 확인해 주세요.')
            result=subprocess.run([str(python),'-c','import qwen_tts,torch,transformers,numpy,soundfile; assert transformers.__version__ == "4.57.3"'],stdout=log,stderr=log,creationflags=0x08000000,env=install_env)
            if result.returncode:raise RuntimeError('음성 실행 환경 검사에 실패했어요. 음성 엔진 준비를 다시 눌러 주세요.')
        (runtime/'voice-python/ready.json').write_text(json.dumps({'qwen_tts':'0.1.1'}),encoding='utf-8')
    progress('참고 목소리 합성 모델 준비 중…')
    from setup_qwen_voice import install_weights
    install_weights(runtime,progress,quality=False)
    return True
