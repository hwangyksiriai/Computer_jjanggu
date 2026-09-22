"""Non-UI client for the isolated, offline speech engine."""
import hashlib,json,os,queue,re,subprocess,threading,time
from pathlib import Path

from app_paths import BASE, DATA, runtime_root, ai_environment

class QwenVoiceClient:
    def __init__(self):
        self.process=None;self.lock=threading.RLock();self.idle_timer=None;self.verifier=None

    def close(self):
        with self.lock:
            if self.idle_timer:self.idle_timer.cancel();self.idle_timer=None
            if self.process and self.process.poll() is None:
                self.process.terminate()
            self.process=None
            if self.verifier:self.verifier.close();self.verifier=None

    def generate(self,text,reference,cancelled=lambda:False):
        from voice_quality import content_check,transcribe_wav,speech_text
        from voice_runtime import voice_python
        from local_ai import LocalAI
        text=speech_text(text)
        with self.lock:
            if cancelled():raise RuntimeError('이전 음성 요청이 취소됐어요.')
            profile=Path(reference).with_suffix('.json')
            ref_text=json.loads(profile.read_text('utf-8')).get('text','').strip() if profile.is_file() else ''
            if not ref_text:raise RuntimeError('목소리 설정에서 참고 음성의 대사를 확인해 주세요.')
            signature=hashlib.sha256(Path(reference).read_bytes()).hexdigest()
            engine='verified-1.7b-int8-v4' if os.environ.get('JJANGGU_VOICE_EXPERIMENTAL')=='1' and (runtime_root()/'qwen3-tts-base-1.7b/provenance.json').is_file() else 'verified-icl-v3'
            identity=json.dumps([signature,ref_text,text,engine],ensure_ascii=False)
            cache=DATA/'voice-generated';cache.mkdir(parents=True,exist_ok=True)
            receipt=cache/(hashlib.sha256(identity.encode()).hexdigest()+'.json')
            if receipt.is_file():
                saved=json.loads(receipt.read_text('utf-8'));cached=Path(saved['path'])
                if cached.is_relative_to(cache.resolve()) and cached.is_file() and saved.get('ok') and hashlib.sha256(cached.read_bytes()).hexdigest()==saved.get('sha256'):return str(cached)
            if self.idle_timer:self.idle_timer.cancel()
            runtime=runtime_root()
            if not self.process or self.process.poll() is not None:
                log=(DATA/'qwen-voice.log').open('a',encoding='utf-8')
                self.responses=queue.Queue()
                self.process=subprocess.Popen([str(voice_python(runtime)),'-X','utf8',str(BASE/'qwen_voice_worker.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',creationflags=0x08000000,env=ai_environment())
                log.close();process=self.process;responses=self.responses
                def read():
                    for line in process.stdout:
                        try:responses.put(json.loads(line))
                        except ValueError:continue
                    responses.put({'ok':False,'error':'음성 엔진이 종료됐어요.'})
                threading.Thread(target=read,daemon=True).start()
            try:
                for seed in (1234,4321):
                    self.process.stdin.write(json.dumps({'text':text,'reference':reference,'reference_text':ref_text,'seed':seed},ensure_ascii=False)+'\n');self.process.stdin.flush()
                    deadline=time.monotonic()+600
                    while True:
                        if cancelled():self.close();raise RuntimeError('이전 음성 요청이 취소됐어요.')
                        if time.monotonic()>deadline:raise queue.Empty()
                        try:response=self.responses.get(timeout=.2);break
                        except queue.Empty:continue
                    if not response.get('ok'):raise RuntimeError(response.get('error','음성 생성 실패'))
                    path=Path(response['path']).resolve()
                    if not path.is_relative_to(cache.resolve()) or not path.is_file():raise RuntimeError('음성 파일 경로가 올바르지 않아요.')
                    if cancelled():raise RuntimeError('이전 음성 요청이 취소됐어요.')
                    self.verifier=self.verifier or LocalAI()
                    check=content_check(text,transcribe_wav(self.verifier,path))
                    path.with_suffix('.quality.json').write_text(json.dumps(dict(check,expected=text),ensure_ascii=False),encoding='utf-8')
                    if check['ok']:
                        receipt.write_text(json.dumps(dict(check,path=str(path),text=text,sha256=hashlib.sha256(path.read_bytes()).hexdigest()),ensure_ascii=False),encoding='utf-8')
                        return str(path)
                raise RuntimeError('생성한 말이 답변과 달라 재생하지 않았어요. 참고 음성의 대사를 확인해 주세요.')
            except (queue.Empty,BrokenPipeError):
                self.close();raise RuntimeError('음성 생성 시간이 초과됐어요.')
            finally:
                self.idle_timer=threading.Timer(900,self.close);self.idle_timer.daemon=True;self.idle_timer.start()

def speech_chunks(text,limit=90):
    chunks=[];pending=''
    for part in re.split(r'(?<=[.!?。！？])\s*|\n+',text.strip()):
        if not part:continue
        while len(part)>limit:
            if pending:chunks.append(pending);pending=''
            split=part.rfind(' ',0,limit+1)
            if split<limit//2:split=limit
            chunks.append(part[:split]);part=part[split:].strip()
        if len(pending)+len(part)+1>limit:chunks.append(pending);pending=''
        pending=(pending+' '+part).strip()
    if pending:chunks.append(pending)
    return chunks
