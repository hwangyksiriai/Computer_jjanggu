"""Install/download explicitly, then run the app strictly from the local cache."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import threading
import os
import uuid

from app_paths import BASE, DATA, node_path, runtime_root, ai_environment, model_cache, runtime_modules
from local_ai import CAPABILITY_MODELS, CAPABILITY_ALIASES, available_models

def record_available_models():
    """Share capability state beside the cache; a missing profile marker is harmless."""
    models=available_models()
    marker={'ready':bool(models),'verified_at':time.time(),'models':models,
            'capabilities':[cap for cap,names in CAPABILITY_MODELS.items() if all(name in models for name in names)]}
    text=json.dumps(marker,ensure_ascii=False,indent=2)
    for target in {DATA/'models-ready.json',model_cache().parent/'models-ready.json'}:
        temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.part')
        try:
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_text(text,encoding='utf-8');temporary.replace(target)
        except OSError:
            # Cache readiness is determined from actual files, so read-only shared metadata
            # must not disable a valid model in another profile.
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
    return marker

def _watch_cancel(process,cancelled):
    finished=threading.Event()
    def watch():
        while not finished.wait(.2):
            if cancelled():
                if process.poll() is None:process.terminate()
                return
    threading.Thread(target=watch,daemon=True).start()
    return finished

def install_models(progress=None,cancelled=lambda:False,capability='all'):
    capability=CAPABILITY_ALIASES.get(capability,capability)
    if capability not in CAPABILITY_MODELS:raise ValueError('알 수 없는 AI 준비 항목: '+str(capability))
    if cancelled():raise RuntimeError('AI 준비를 중단했어요. 다음에 준비 버튼을 누르면 이어서 받을 수 있어요.')
    node=node_path()
    if not node: raise RuntimeError('사진 찾기 준비에 Node.js가 필요해요. Node.js를 설치한 뒤 이 버튼을 다시 눌러 주세요.')
    runtime=runtime_root()
    DATA.mkdir(parents=True,exist_ok=True)
    flags=0x08000000 if os.name=='nt' else 0
    if not (runtime_modules()/'@huggingface/transformers/dist/transformers.node.mjs').is_file():
        npm=shutil.which('npm.cmd') or shutil.which('npm')
        if not npm: raise RuntimeError('Node.js 설치를 완료한 뒤 앱을 다시 실행해 주세요.')
        runtime.mkdir(parents=True,exist_ok=True)
        if runtime!=BASE:
            for name in ('package.json','package-lock.json'): shutil.copy2(BASE/name,runtime/name)
        if progress:progress('AI 실행 구성 요소 준비 중…')
        with (DATA/'model-component-setup.log').open('w',encoding='utf-8') as log:
            installed=subprocess.Popen([npm,'ci','--no-audit','--no-fund'],cwd=runtime,
                                       stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
            finished=_watch_cancel(installed,cancelled)
            try:code=installed.wait()
            finally:finished.set()
        if cancelled():raise RuntimeError('AI 준비를 중단했어요. 다음에 준비 버튼을 누르면 이어서 받을 수 있어요.')
        if code:raise RuntimeError('AI 실행 구성 요소 설치를 완료하지 못했어요. 인터넷과 저장 공간을 확인해 주세요.')
    with (DATA/'model-setup.log').open('w',encoding='utf-8') as log:
        option='--setup' if capability=='all' else '--setup-'+capability
        process=subprocess.Popen([node,'--expose-gc',str(BASE/'ai_worker.mjs'),option],cwd=BASE,
                                 stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',creationflags=flags,env=ai_environment())
        finished=_watch_cancel(process,cancelled)
        complete=False
        labels={'semantic':'문서의 의미','vision':'사진의 특징','objects':'사진 속 사물 위치','conversation':'대화','speech':'말로 찾기'}
        try:
            for line in process.stdout:
                try: event=json.loads(line)
                except ValueError:continue
                complete=complete or event.get('setup')=='complete'
                if event.get('status')=='ready':record_available_models()
                if progress and event.get('stage'):
                    detail=' 준비 완료' if event.get('status')=='ready' else ' 준비 중…'
                    if event.get('status')=='progress':detail=f" 다운로드 {event.get('percent',0)}% · {int(event.get('loaded',0)/1024**2)}MB"
                    progress(labels.get(event['stage'],event['stage'])+detail)
            code=process.wait()
        finally:
            finished.set()
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill()
            record_available_models()
    if cancelled():raise RuntimeError('AI 준비를 중단했어요. 다음에 준비 버튼을 누르면 이어서 받을 수 있어요.')
    if code: raise RuntimeError('다운로드를 완료하지 못했어요. 인터넷과 저장 공간을 확인하고 다시 준비해 주세요.')
    if not complete: raise RuntimeError('모델 준비 확인을 받지 못했어요.')
    present=available_models()
    if not all(name in present for name in CAPABILITY_MODELS[capability]):
        raise RuntimeError('준비한 모델의 파일 확인에 실패했어요. 같은 준비 버튼을 다시 눌러 주세요. 이미 준비된 다른 기능은 사용할 수 있어요.')
    return True

if __name__=='__main__':
    install_models(); print('All local models ready. Inference is offline.')
