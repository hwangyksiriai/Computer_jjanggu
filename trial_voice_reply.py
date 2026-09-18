"""Offline quality trial; never installs its output as the app voice."""
import json,os,time,argparse
from pathlib import Path
base=Path(__file__).parent
parser=argparse.ArgumentParser()
parser.add_argument('--reference')
parser.add_argument('--output',default='.local/voice-reference/search-trial.wav')
parser.add_argument('--text',default='알았어. 파일을 찾아볼게.')
args=parser.parse_args()
runtime=Path(json.loads((base/'.local/runtime.json').read_text('utf-8-sig'))['runtime_root'])
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HOME=str(runtime/'voice-hf-cache'),NUMBA_CACHE_DIR=str(runtime/'voice-numba-cache'),MPLCONFIGDIR=str(runtime/'voice-mpl-cache'))
import torch
torch.set_num_threads(2); torch.manual_seed(42)
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts
import soundfile as sf
config=XttsConfig(); config.load_json(str(runtime/'xtts-v2/config.json'))
model=Xtts.init_from_config(config); model.load_checkpoint(config,checkpoint_dir=str(runtime/'xtts-v2'),use_deepspeed=False)
print('Model ready',flush=True)
settings=json.loads((base/'.local/settings.json').read_text('utf-8'))
output=model.synthesize(args.text,config,speaker_wav=args.reference or settings['voice_clips']['greeting'],language='ko',gpt_cond_len=6,gpt_cond_chunk_len=3,max_ref_len=12,temperature=.65,repetition_penalty=10.)
target=base/args.output; sf.write(target,output['wav'],24000)
print(str(target),flush=True)
