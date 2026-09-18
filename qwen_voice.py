"""Non-UI client for the isolated, offline speech engine."""
import json,queue,subprocess,threading
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
                response=self.responses.get(timeout=300)
                if not response.get('ok'):raise RuntimeError(response.get('error','음성 생성 실패'))
                path=Path(response['path']).resolve()
                if not path.is_relative_to((BASE/'.local/voice-generated').resolve()) or not path.is_file():raise RuntimeError('음성 파일 경로가 올바르지 않아요.')
                return str(path)
            except (queue.Empty,BrokenPipeError):
                self.close();raise RuntimeError('음성 생성 시간이 초과됐어요.')
            finally:
                self.idle_timer=threading.Timer(120,self.close);self.idle_timer.daemon=True;self.idle_timer.start()
