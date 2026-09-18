"""Non-UI client for the isolated, offline speech engine."""
import hashlib,json,queue,re,subprocess,threading
from pathlib import Path

BASE=Path(__file__).resolve().parent

class QwenVoiceClient:
    def __init__(self):
        self.process=None;self.lock=threading.RLock();self.idle_timer=None

    def close(self):
        with self.lock:
            if self.idle_timer:self.idle_timer.cancel();self.idle_timer=None
            if self.process and self.process.poll() is None:
                self.process.terminate()
            self.process=None

    def generate(self,text,reference):
        with self.lock:
            signature=hashlib.sha256(Path(reference).read_bytes()).hexdigest()
            identity=json.dumps([signature,text,'qwen-0.6b-fp32-v2'],ensure_ascii=False)
            cached=BASE/'.local/voice-generated'/(hashlib.sha256(identity.encode()).hexdigest()+'.wav')
            if cached.is_file():return str(cached)
            if self.idle_timer:self.idle_timer.cancel()
            runtime=Path(json.loads((BASE/'.local/runtime.json').read_text('utf-8-sig'))['runtime_root'])
            if not self.process or self.process.poll() is not None:
                log=(BASE/'.local/qwen-voice.log').open('a',encoding='utf-8')
                self.responses=queue.Queue()
                self.process=subprocess.Popen([str(runtime/'voice-env/Scripts/python.exe'),'-X','utf8',str(BASE/'qwen_voice_worker.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',creationflags=0x08000000)
                log.close();process=self.process;responses=self.responses
                def read():
                    for line in process.stdout:
                        try:responses.put(json.loads(line))
                        except ValueError:continue
                    responses.put({'ok':False,'error':'음성 엔진이 종료됐어요.'})
                threading.Thread(target=read,daemon=True).start()
            try:
                self.process.stdin.write(json.dumps({'text':text,'reference':reference},ensure_ascii=False)+'\n');self.process.stdin.flush()
                response=self.responses.get(timeout=600)
                if not response.get('ok'):raise RuntimeError(response.get('error','음성 생성 실패'))
                path=Path(response['path']).resolve()
                if not path.is_relative_to((BASE/'.local/voice-generated').resolve()) or not path.is_file():raise RuntimeError('음성 파일 경로가 올바르지 않아요.')
                return str(path)
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
