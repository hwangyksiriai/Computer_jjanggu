"""Generate a local, explicitly synthetic voice preview from the user's reference."""
import json,os,time
from pathlib import Path

base=Path(__file__).parent
runtime=Path(json.loads((base/'.local/runtime.json').read_text('utf-8-sig'))['runtime_root'])
os.environ['HF_HUB_OFFLINE']='1'; os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HOME']=str(runtime/'voice-hf-cache')
os.environ['NUMBA_CACHE_DIR']=str(runtime/'voice-numba-cache')
os.environ['MPLCONFIGDIR']=str(runtime/'voice-mpl-cache')
import torch
torch.set_num_threads(2)
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts
import soundfile as sf

folder=runtime/'xtts-v2'
config=XttsConfig(); config.load_json(str(folder/'config.json'))
model=Xtts.init_from_config(config)
model.load_checkpoint(config,checkpoint_dir=str(folder),use_deepspeed=False)
print('local voice model loaded',flush=True)
started=time.monotonic()
output=model.synthesize('뭘 찾아줄까? 찾고 싶은 파일을 말해 줘.',config,
    speaker_wav=str(base/'.local/voice-reference/reference.wav'),language='ko',
    gpt_cond_len=12,gpt_cond_chunk_len=4,max_ref_len=30)
path=base/'.local/voice-reference/synthetic-preview.wav'
sf.write(path,output['wav'],24000)
report={'synthetic':True,'reference':str(base/'.local/voice-reference/reference.wav'),
        'text':'뭘 찾아줄까? 찾고 싶은 파일을 말해 줘.','seconds':len(output['wav'])/24000,
        'elapsed':time.monotonic()-started,'output':str(path)}
(path.parent/'preview-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False),flush=True)
