"""Install/download explicitly, then run the app strictly from the local cache."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

BASE=Path(__file__).resolve().parent
def install_models():
    node=shutil.which('node')
    if not node: raise RuntimeError('Node.js가 필요해요.')
    result=subprocess.run([node,'--expose-gc',str(BASE/'ai_worker.mjs'),'--setup'],cwd=BASE,
                          capture_output=True,text=True,encoding='utf-8',creationflags=0x08000000)
    if result.returncode: raise RuntimeError(result.stderr[-1500:])
    if '"setup":"complete"' not in result.stdout: raise RuntimeError('모델 준비 확인을 받지 못했어요.')
    marker=BASE/'.local/models-ready.json'
    marker.parent.mkdir(parents=True,exist_ok=True)
    marker.write_text(json.dumps({'ready':True,'verified_at':time.time(),'models':[
      'Xenova/multilingual-e5-small','Xenova/clip-vit-base-patch32','onnx-community/Qwen3-1.7B-ONNX','Xenova/whisper-tiny']},indent=2),encoding='utf-8')
    return True

if __name__=='__main__':
    install_models(); print('All local models ready. Inference is offline.')
