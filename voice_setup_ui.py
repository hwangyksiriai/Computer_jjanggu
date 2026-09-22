"""Guided, local reference registration and verified reply speech."""
import json,threading,uuid
from pathlib import Path
import tkinter as tk
from tkinter import filedialog,simpledialog,messagebox
from app import label,button,BG,GREEN
from accessibility import fit_window,scroll_page,apply_fonts

def show(app):
    win=tk.Toplevel(app.root);win.title('짱구 목소리 · 새 문장 말하기');win.configure(bg=BG)
    fit_window(win,640,650)
    body=scroll_page(win,BG)
    label(body,'목소리를 준비하고 직접 들어보세요',20,bold=True).pack(anchor='w',pady=12)
    label(body,'참고 음성과 대사를 함께 사용해 새 답변을 만들어요.\n답변과 다른 말로 인식된 음성은 재생하지 않아요. 처음 생성은 시간이 걸릴 수 있어요.',12,GREEN,wraplength=540,justify='left').pack(anchor='w')
    status=tk.StringVar(value='1. 음성 엔진 준비 → 2. 음성 연결과 대사 확인 → 3. 들어보고 적용')
    reference=tk.StringVar(value=app.settings.get('voice_reference',''))
    transcript=tk.StringVar()
    state={'busy':False,'verified':None,'closed':False};client=[None]
    def closed(event):
        if event.widget is not win:return
        state['closed']=True
        if client[0]:threading.Thread(target=client[0].close,daemon=True).start()
    win.bind('<Destroy>',closed,add='+')
    if reference.get():
        meta=Path(reference.get()).with_suffix('.json')
        if meta.is_file():
            try:transcript.set(json.loads(meta.read_text('utf-8')).get('text',''))
            except (OSError,ValueError):pass
    def tell(text):
        if state['closed']:raise RuntimeError('목소리 준비를 중단했어요.')
        app.events.put(lambda:status.set(text) if win.winfo_exists() else None)
    def work(fn,done):
        if state['busy']:return
        state['busy']=True
        def worker():
            try:result=fn();error=None
            except Exception as exc:result=None;error=str(exc)
            def finish():
                state['busy']=False
                if not win.winfo_exists():return
                if error:status.set(error)
                else:done(result)
            app.events.put(finish)
        threading.Thread(target=worker,daemon=True).start()
    def prepare():
        from voice_runtime import install
        from setup_runtime import install_models
        from app_paths import runtime_root,DATA
        import shutil
        target=runtime_root();probe=target
        while not probe.exists():probe=probe.parent
        if shutil.disk_usage(probe).free<8*1024**3:
            chosen=filedialog.askdirectory(parent=win,title='음성 모델 저장 폴더 · 여유 공간 8GB 이상')
            if not chosen:return
            if shutil.disk_usage(chosen).free<8*1024**3:status.set('여유 공간이 8GB 이상인 드라이브를 골라 주세요.');return
            (DATA/'runtime.json').write_text(json.dumps({'runtime_root':str(Path(chosen)/'JjangguAI')}),encoding='utf-8')
        def install_all():
            if not app.ai.ready_for('speech'):install_models(tell,lambda:state['closed'],capability='speech')
            install(tell,lambda:state['closed'])
        work(install_all,lambda result:status.set('엔진 준비 완료! 참고 음성을 연결해 주세요.'))
    button(body,'1. 음성 엔진 준비',prepare).pack(fill='x',pady=12)
    def connect():
        source=filedialog.askopenfilename(parent=win,title='짱구가 혼자 또렷하게 말하는 음성·영상',filetypes=[('음성·영상','*.wav *.mp3 *.mp4 *.m4a *.webm'),('모든 파일','*.*')])
        if not source:return
        start=simpledialog.askfloat('시작 위치','몇 초부터 사용할까요?',parent=win,initialvalue=0,minvalue=0)
        if start is None:return
        end=simpledialog.askfloat('끝 위치','몇 초까지 사용할까요? 다른 목소리가 없는 3~10초 구간을 권장해요.',parent=win,initialvalue=start+5,minvalue=start+.5,maxvalue=start+15)
        if end is None:return
        destination=app.data/'voice-reference'/(uuid.uuid4().hex+'.wav')
        def extract():
            from voice_clips import make_clip
            from voice_quality import transcribe_wav
            from local_ai import LocalAI
            make_clip(source,start,end,destination)
            ai=LocalAI()
            try:heard=transcribe_wav(ai,destination) if ai.ready() else ''
            finally:ai.close()
            return heard
        def connected(heard):
            reference.set(str(destination));transcript.set(heard);state['verified']=None
            status.set('참고 음성을 들어보고 아래 대사가 실제 말과 같은지 고쳐 주세요.')
        work(extract,connected)
    button(body,'2. 참고 음성·영상 연결',connect).pack(fill='x',pady=6)
    def listen_reference():
        if reference.get():app.voice.play_clip(reference.get(),app.settings,force=True)
    button(body,'참고 음성 듣기',listen_reference).pack(anchor='w')
    label(body,'참고 음성에서 말한 정확한 대사',12,bold=True).pack(anchor='w',pady=(14,4))
    entry=tk.Entry(body,textvariable=transcript,font=('맑은 고딕',13));entry.pack(fill='x',ipady=10)
    from ime_entry import attach
    ime=attach(entry)
    def sample():
        path=Path(reference.get())
        if not path.is_file() or not transcript.get().strip():status.set('참고 음성과 정확한 대사를 먼저 입력해 주세요.');return
        path.with_suffix('.json').write_text(json.dumps({'text':transcript.get().strip()},ensure_ascii=False),encoding='utf-8')
        from qwen_voice import QwenVoiceClient
        client[0]=client[0] or QwenVoiceClient()
        snapshot=(str(path),transcript.get().strip())
        status.set('새 답변을 만들고 말이 맞는지 확인 중이에요…')
        def finished(audio):
            state['verified']=snapshot;app.voice.play_clip(audio,app.settings,force=True)
            status.set('말의 내용 검사를 통과했어요. 목소리를 들어보고 마음에 들면 적용해 주세요.')
        work(lambda:client[0].generate('알았어. 인보이스를 찾아볼게.',str(path),cancelled=lambda:state['closed']),finished)
    button(body,'3. 새 답변 들어보기',lambda:ime.commit_then(sample) if ime else sample(),primary=True).pack(fill='x',pady=12)
    def apply():
        if state['verified']!=(reference.get(),transcript.get().strip()):status.set('변경한 음성으로 새 답변 들어보기를 먼저 눌러 주세요.');return
        app.voice.stop();app.settings.update(sound=True,voice_mode='qwen_local',voice_reference=reference.get());app.save()
        status.set('적용했어요. 이제 요청에 맞는 새 답변을 이 목소리로 만들어요.')
    button(body,'이 목소리로 모든 답변 말하기',apply).pack(fill='x',pady=5)
    def stop():app.voice.stop();app.settings['sound']=False;app.save();status.set('말소리를 껐어요.')
    button(body,'말소리 끄기',stop).pack(anchor='w',pady=6)
    label(body,'',12,GREEN,textvariable=status,wraplength=540,justify='left').pack(anchor='w',pady=16)
    apply_fonts(body,app.settings.get('text_scale',1.0))
    return win
