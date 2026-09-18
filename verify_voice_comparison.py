"""Record offline ASR checks without treating ASR as a voice-likeness score."""
import bootstrap
import json,wave
from pathlib import Path
import numpy as np
from local_ai import LocalAI
folder=Path(__file__).parent/'.local/voice-reference/comparison'
reports=json.loads((folder/'report.json').read_text(encoding='utf-8'))
ai=LocalAI()
try:
    for report in reports:
        with wave.open(report['path'],'rb') as w:
            rate=w.getframerate(); a=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)/32768
        samples=np.interp(np.arange(int(len(a)*16000/rate))*rate/16000,np.arange(len(a)),a).astype(np.float32)
        report['recognized']=ai.call('transcribe',audio=samples.tolist())
        report['content_check']=all(t in report['recognized'].replace(' ','') for t in ('인보이스','찾'))
        print(json.dumps(report,ensure_ascii=False),flush=True)
finally:ai.close()
(folder/'verification.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
