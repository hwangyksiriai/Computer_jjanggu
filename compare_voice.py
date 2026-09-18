"""Bounded local voice diagnosis. Outputs are previews, never auto-installed."""
import os,json,wave,time
from pathlib import Path
base=Path(__file__).parent
runtime=Path(json.loads((base/'.local/runtime.json').read_text('utf-8-sig'))['runtime_root'])
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HOME=str(runtime/'voice-hf-cache'),NUMBA_CACHE_DIR=str(runtime/'voice-numba-cache'),MPLCONFIGDIR=str(runtime/'voice-mpl-cache'))
import torch
torch.set_num_threads(2)
import soundfile as sf
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts
folder=base/'.local/voice-reference/comparison'; folder.mkdir(exist_ok=True)
source,rate=sf.read(base/'.local/voice-reference/new-reference.wav')
config=XttsConfig(); config.load_json(str(runtime/'xtts-v2/config.json'))
model=Xtts.init_from_config(config); model.load_checkpoint(config,checkpoint_dir=str(runtime/'xtts-v2'),use_deepspeed=False)
model.eval()
text='알았어. 인보이스 찾아볼게.'
print('Tokenizer:',model.tokenizer.preprocess_text(text,'ko'),flush=True)
print('Tokens:',model.tokenizer.encode(text,lang='ko'),flush=True)
reports=[]
for i,(start,end,penalty,sample) in enumerate([(0,8,2.,True),(20,28,2.,True),(30,40,5.,False)]):
    reference=folder/f'reference-{i}.wav'; sf.write(reference,source[int(start*rate):int(end*rate)],rate)
    torch.manual_seed(1234); started=time.monotonic()
    output=model.synthesize(text,speaker_wav=str(reference),language='ko',gpt_cond_len=6,gpt_cond_chunk_len=3,max_ref_len=10,temperature=.75,repetition_penalty=penalty,do_sample=sample)
    target=folder/f'reply-{i}.wav'; sf.write(target,output['wav'],24000)
    report={'reference_start':start,'reference_end':end,'text':text,'path':str(target),'repetition_penalty':penalty,'sampling':sample,'seconds':len(output['wav'])/24000,'elapsed':time.monotonic()-started}
    reports.append(report); print(json.dumps(report,ensure_ascii=False),flush=True)
    (folder/'report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
