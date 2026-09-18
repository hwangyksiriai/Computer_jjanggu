"""Generate three novel Korean phrases, then transcribe after freeing TTS RAM."""
import bootstrap,json,subprocess,time,wave
from pathlib import Path
import numpy as np
from local_ai import LocalAI
base=Path(__file__).parent
runtime=Path(json.loads((base/'.local/runtime.json').read_text('utf-8-sig'))['runtime_root'])
reference=base/'.local/voice-reference/comparison/reference-0.wav'
phrases=['알았어. 파일을 찾아볼게.','파일 세 개를 찾았어.','오늘은 어떤 일을 도와줄까?']
report=[]
log=(base/'.local/qwen-voice-test.log').open('w',encoding='utf-8')
proc=subprocess.Popen([str(runtime/'voice-env/Scripts/python.exe'),'-X','utf8',str(base/'qwen_voice_worker.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',creationflags=0x08000000)
try:
    for text in phrases:
        started=time.monotonic();print('Synthesizing: '+text,flush=True)
        proc.stdin.write(json.dumps({'text':text,'reference':str(reference)},ensure_ascii=False)+'\n');proc.stdin.flush()
        line=proc.stdout.readline()
        if not line:raise RuntimeError('Speech worker exited; see .local/qwen-voice-test.log')
        result=json.loads(line)
        if not result['ok']:raise RuntimeError(result['error'])
        result['seconds_to_generate']=round(time.monotonic()-started,2);report.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
finally:
    proc.stdin.close()
    try:proc.wait(timeout=10)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
    log.close()
ai=LocalAI()
try:
    for result in report:
        with wave.open(result['path'],'rb') as w:
            rate=w.getframerate();a=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)/32768
        x=np.interp(np.arange(int(len(a)*16000/rate))*rate/16000,np.arange(len(a)),a).astype(np.float32)
        result['recognized']=ai.call('transcribe',audio=x.tolist())
        print(json.dumps(result,ensure_ascii=False),flush=True)
finally:ai.close()
(base/'.local/voice-reference/qwen-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
