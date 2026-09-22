"""Private JSON pipe for offline speech generation. No network during inference."""
import contextlib,hashlib,json,os,sys
from pathlib import Path
base=Path(__file__).resolve().parent
state=Path(os.environ.get('JJANGGU_STATE_ROOT',str(base/'.local')))
runtime=Path(os.environ['JJANGGU_RUNTIME_ROOT']) if os.environ.get('JJANGGU_RUNTIME_ROOT') else Path(json.loads((state/'runtime.json').read_text('utf-8-sig'))['runtime_root'])
sys.path.insert(0,str(runtime/'qwen-packages'))
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HOME=str(runtime/'qwen-cache'),NUMBA_CACHE_DIR=str(runtime/'qwen-numba-cache'))
model=None; prompts={}
# A larger model failed the Korean content benchmark. Keep it behind an explicit
# diagnostic flag rather than silently selecting it just because it was downloaded.
quality=os.environ.get('JJANGGU_VOICE_EXPERIMENTAL')=='1' and (runtime/'qwen3-tts-base-1.7b/provenance.json').is_file()
engine='qwen-1.7b-int8-icl-v4' if quality else 'qwen-icl-v3'

def synthesize(request):
    global model
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel,VoiceClonePromptItem
    torch.set_num_threads(4)
    reference=Path(request['reference']).resolve()
    if not reference.is_file():raise ValueError('참고 음성 파일이 없습니다.')
    text=request['text'].strip()
    if not text or len(text)>300:raise ValueError('음성 문장은 1~300자로 입력해 주세요.')
    signature=hashlib.sha256(reference.read_bytes()).hexdigest()
    ref_text=request.get('reference_text','').strip()
    if not ref_text:raise ValueError('참고 음성에서 말하는 대사를 등록해 주세요.')
    seed=int(request.get('seed',1234))
    identity=json.dumps([signature,ref_text,text,seed,engine],ensure_ascii=False)
    cache=state/'voice-generated';cache.mkdir(parents=True,exist_ok=True)
    output=cache/(hashlib.sha256(identity.encode()).hexdigest()+'.wav')
    if output.exists():return str(output)
    if model is None:
        model=Qwen3TTSModel.from_pretrained(str(runtime/('qwen3-tts-base-1.7b' if quality else 'qwen3-tts-base')),device_map='cpu',dtype=torch.float32,attn_implementation='sdpa',local_files_only=True)
        if quality:
            # Linear layers use dynamic int8 on CPU; embeddings and acoustic
            # tokenizer keep their trained precision. In-place avoids a second model copy.
            torch.ao.quantization.quantize_dynamic(model.model,{torch.nn.Linear},dtype=torch.qint8,inplace=True)
        print('VOICE_ENGINE_READY',file=sys.stderr,flush=True)
    prompt_key=signature+ref_text
    if prompt_key not in prompts:
        prompts[prompt_key]=model.create_voice_clone_prompt(ref_audio=str(reference),ref_text=ref_text,x_vector_only_mode=False)
    print('VOICE_GENERATING',file=sys.stderr,flush=True)
    torch.manual_seed(seed)
    wavs,rate=model.generate_voice_clone(text=text,language='Korean',voice_clone_prompt=prompts[prompt_key],non_streaming_mode=False,
                                       do_sample=True,temperature=.7,top_p=.9,repetition_penalty=1.1,max_new_tokens=max(100,min(900,len(text)*6)))
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
