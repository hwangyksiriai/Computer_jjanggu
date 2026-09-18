"""Private JSON pipe for offline speech generation. No network during inference."""
import contextlib,hashlib,json,os,sys
from pathlib import Path
base=Path(__file__).resolve().parent
runtime=Path(json.loads((base/'.local/runtime.json').read_text('utf-8-sig'))['runtime_root'])
sys.path.insert(0,str(runtime/'qwen-packages'))
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HOME=str(runtime/'qwen-cache'),NUMBA_CACHE_DIR=str(runtime/'qwen-numba-cache'))
model=None; prompts={}

def synthesize(request):
    global model
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(4)
    reference=Path(request['reference']).resolve()
    if not reference.is_file():raise ValueError('참고 음성 파일이 없습니다.')
    text=request['text'].strip()
    if not text or len(text)>300:raise ValueError('음성 문장은 1~300자로 입력해 주세요.')
    signature=hashlib.sha256(reference.read_bytes()).hexdigest()
    identity=json.dumps([signature,text,'qwen-0.6b-fp32-v2'],ensure_ascii=False)
    cache=base/'.local/voice-generated';cache.mkdir(exist_ok=True)
    output=cache/(hashlib.sha256(identity.encode()).hexdigest()+'.wav')
    if output.exists():return str(output)
    if model is None:
        model=Qwen3TTSModel.from_pretrained(str(runtime/'qwen3-tts-base'),device_map='cpu',dtype=torch.float32,attn_implementation='sdpa',local_files_only=True)
        print('VOICE_ENGINE_READY',file=sys.stderr,flush=True)
    if signature not in prompts:
        prompts[signature]=model.create_voice_clone_prompt(ref_audio=str(reference),x_vector_only_mode=True)
    print('VOICE_GENERATING',file=sys.stderr,flush=True)
    torch.manual_seed(1234)
    wavs,rate=model.generate_voice_clone(text=text,language='Korean',voice_clone_prompt=prompts[signature],non_streaming_mode=True,max_new_tokens=180)
    if not len(wavs[0]):raise ValueError('생성된 음성이 비어 있습니다.')
    temp=output.with_suffix('.tmp.wav');sf.write(temp,wavs[0],rate);temp.replace(output)
    return str(output)

if __name__=='__main__':
    for line in sys.stdin.buffer:
        try:
            request=json.loads(line.decode('utf-8'))
            with contextlib.redirect_stdout(sys.stderr):path=synthesize(request)
            reply={'ok':True,'path':path,'text':request['text']}
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr);reply={'ok':False,'error':str(e)}
        print(json.dumps(reply,ensure_ascii=False),flush=True)
