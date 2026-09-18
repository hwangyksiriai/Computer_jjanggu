import bootstrap
import json
from pathlib import Path
import subprocess
import wave
import numpy as np
from local_ai import LocalAI,BASE

folder=BASE/'.local/verification'; folder.mkdir(parents=True,exist_ok=True)
target=folder/'voice-search-test.wav'
request={'text':'인보이스 찾아줘.','voice':'Microsoft Heami Desktop','rate':0,'volume':80}
r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(BASE/'speech.ps1'),'-OutputPath',str(target)],
    input=json.dumps(request,ensure_ascii=False).encode('utf-8'),capture_output=True,timeout=30,creationflags=0x08000000)
assert r.returncode==0,r.stderr
with wave.open(str(target),'rb') as wav:
    assert wav.getsampwidth()==2
    samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype=np.int16).astype(np.float32)/32768
    if wav.getnchannels()>1: samples=samples.reshape(-1,wav.getnchannels()).mean(axis=1)
    rate=wav.getframerate()
samples=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype(np.float32)
ai=LocalAI()
try:
    result=ai.call('transcribe',audio=samples.tolist())
    print(json.dumps({'synthesized':'인보이스 찾아줘.','recognized':result},ensure_ascii=False))
    (folder/'voice-input-result.json').write_text(json.dumps({'recognized':result},ensure_ascii=False),encoding='utf-8')
    assert '찾' in result,result
finally: ai.close()
