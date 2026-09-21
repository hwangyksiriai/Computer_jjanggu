"""Small, task-first interface. File operations still use the existing safety checks."""
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog
from enhancements import EnhancedApp
from app import BG, WHITE, INK, MUTED, GREEN, ORANGE, FONT, button, label


class EasyApp(EnhancedApp):
    def __init__(self,root,data=None):
        super().__init__(root,data)
        def voice_notice(text):
            def show():
                self.status.set(text)
                if self.bubble and self.bubble.alive(): self.bubble.message.set(text)
            self.events.put(show)
        self.voice.error_callback=voice_notice

    def open_desktop_search(self):
        if self.bubble and self.bubble.alive():
            self.bubble.win.lift(); self.bubble.entry.focus_force(); return
        self.toggle_bubble()

    def focus_search(self):
        self.open_desktop_search()

    def toggle_bubble(self):
        if self.bubble and self.bubble.alive():
            self.bubble.close(); return
        from quick_bubble import QuickBubble
        self.walk_route=None
        self.search_fx={'active':False,'interactive':True,'until':time.monotonic()+2,'outcome':''}
        self.pet.action=2; self.pet.action_until=time.time()+1.2
        self.bubble=QuickBubble(self)

    def connect_from_bubble(self):
        hidden=self.root.state()=='withdrawn'
        self.root.pet_only_start=hidden
        try: self.choose_source(True)
        finally:
            self.root.pet_only_start=False
            if hidden: self.root.withdraw()
        if self.bubble and self.bubble.alive():
            self.bubble.message.set('연결했어! 이제 기억나는 단어로 찾아봐.' if self.settings['source'] else '연습용 파일에서 먼저 찾아봐!')
            if self.settings['source']:
                self.bubble.clear(); self.bubble.resize(185)

    def voice_settings(self):
        dialog=tk.Toplevel(self.root); dialog.title('클릭 반응과 목소리'); dialog.geometry('490x420')
        dialog.attributes('-topmost',True)
        label(dialog,'클릭하면 반응하고, 짧게 인사해요.',17,bold=True).pack(pady=15)
        label(dialog,'등록 음성이 없으면 Windows 기본 음성으로 말해요.\n원본 짱구 목소리는 아직 포함되어 있지 않아요.',
              12,GREEN,justify='left').pack(padx=18,pady=8)
        status=tk.StringVar(value='사용할 짧은 음성이나 영상 파일을 연결하세요.')
        def connect(event):
            path=filedialog.askopenfilename(parent=dialog,title='사용할 음성 또는 영상',filetypes=[('음성·영상','*.wav *.mp3 *.m4a *.mp4 *.webm'),('모든 파일','*.*')])
            if not path:return
            start=simpledialog.askfloat('시작 위치','몇 초부터 사용할까요?',parent=dialog,initialvalue=0,minvalue=0)
            if start is None:return
            end=simpledialog.askfloat('끝 위치','몇 초까지 사용할까요? (최대 15초 길이)',parent=dialog,initialvalue=start+3,minvalue=start+.2,maxvalue=start+15)
            if end is None:return
            from voice_clips import make_clip
            import uuid
            target=self.data/'voice-clips'/f'{event}-{uuid.uuid4().hex}.wav'
            try: make_clip(path,start,end,target)
            except Exception as error:
                messagebox.showerror('음성 확인',str(error),parent=dialog); return
            clips=dict(self.settings.get('voice_clips',{})); clips[event]=str(target)
            self.settings.update(voice_clips=clips,sound=True); self.save()
            self.voice.play_clip(target,self.settings)
            status.set('연결했어요. 아래에서 다시 들어볼 수 있어요.')
        for event,title in [('greeting','클릭 인사'),('search','검색 시작'),('found','찾기 성공')]:
            row=tk.Frame(dialog); row.pack(fill='x',padx=18,pady=5)
            button(row,title+' 연결',lambda e=event:connect(e)).pack(side='left')
            def listen(e=event):
                path=self.settings.get('voice_clips',{}).get(e)
                if path:self.voice.play_clip(path,self.settings,force=True)
                else:status.set('아직 연결된 음성이 없어요.')
            button(row,'들어보기',listen).pack(side='right')
        label(dialog,'',11,GREEN,textvariable=status,wraplength=450).pack(pady=10)
        button(dialog,'소리 켜기 / 끄기',self.toggle_sound).pack()

    def shell(self):
        self.root.minsize(900, 620)
        self.root.geometry(f'1000x{min(720, max(620, self.root.winfo_screenheight()-100))}')
        side = tk.Frame(self.root, bg='#F0EEE6', width=190)
        side.pack(side='left', fill='y'); side.pack_propagate(False)
        label(side, '짱구 주머니', 20, bold=True).pack(pady=(28, 8))
        label(side, '찾고, 정리하고, 되돌려요.', 11, GREEN).pack(pady=(0, 24))
        self.nav = {}
        for key, title in [('home', '파일 찾기'), ('organize', '파일 정리'),
                           ('history', '정리 기록'), ('more', '더보기')]:
            b = button(side, title, lambda k=key: self.show(k), bg='#F0EEE6', anchor='w')
            b.configure(font=(FONT, 13, 'bold'), pady=16)
            b.pack(fill='x', padx=12, pady=5); self.nav[key] = b
        button(side, '앱 종료', self.quit).pack(side='bottom', fill='x', padx=12, pady=14)
        button(side, '창 접기', self.hide).pack(side='bottom', fill='x', padx=12)
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side='left', fill='both', expand=True, padx=24, pady=24)
        self.content = tk.Frame(self.main, bg=BG); self.content.pack(fill='both', expand=True)
        label(self.main, '', 11, GREEN, textvariable=self.status, wraplength=690,
              anchor='w', justify='left').pack(fill='x', pady=(12, 0))

    def show_upgrade_hint(self):
        self.status.set('기본 찾기와 정리는 바로 쓸 수 있어요. AI는 추가 기능이에요.')

    def page_home(self):
        label(self.content, '어떤 파일을 찾으세요?', 25, bold=True).pack(anchor='w', pady=(0, 10))
        label(self.content, '기억나는 이름이나 단어를 적어 주세요.', 13, GREEN).pack(anchor='w')
        panel = tk.Frame(self.content, bg='#FFF0DF'); panel.pack(fill='x', pady=16)
        if not self.settings['source']:
            label(panel, '지금은 연습용 파일만 보여요.', 14, bold=True).pack(anchor='w', padx=16, pady=(14, 4))
            label(panel, '내 파일을 찾으려면 아래 버튼을 눌러 주세요.', 12).pack(anchor='w', padx=16)
            button(panel, '내 바탕화면 연결하기', lambda: self.choose_source(True), primary=True).pack(anchor='w', padx=16, pady=12)
        else:
            label(panel, '찾는 곳: ' + self.source.name, 13, bold=True).pack(anchor='w', padx=16, pady=12)
        search = tk.Frame(self.content, bg=WHITE); search.pack(fill='x', pady=(0, 8))
        self.entry = tk.Entry(search, textvariable=self.query, font=(FONT, 16), relief='flat')
        self.entry.pack(side='left', fill='x', expand=True, padx=12, ipady=14)
        self.entry.bind('<Return>', lambda e: self.do_search(True))
        button(search, '찾기', lambda: self.do_search(True), primary=True).pack(side='right', padx=6, pady=6)
        examples = tk.Frame(self.content, bg=BG); examples.pack(fill='x', pady=8)
        for text in ['사진', '영수증', '최근 파일']:
            button(examples, text, lambda t=text: self.quick(t)).pack(side='left', padx=(0, 8))
        self.reply_label = label(self.content, self.last_reply, 12, GREEN, wraplength=650, justify='left')
        self.reply_label.pack(anchor='w', pady=6)
        top = tk.Frame(self.content, bg=BG); top.pack(fill='x')
        self.result_count = label(top, '', 12, bold=True); self.result_count.pack(side='left')
        button(top, '목록 새로고침', self.reindex).pack(side='right')
        self.results = self.scroll(self.content)
        self.render_results()

    def render_results(self):
        if self.page != 'home':
            return
        for child in self.results.winfo_children(): child.destroy()
        if self.query.get().strip():
            total = len(self.result_cache)
            rows = self.result_cache[self.search_offset:self.search_offset+30]
        else:
            total = self.library.stats([self.source, self.vault])['total']
            rows = self.library.rows([self.source, self.vault], limit=30, offset=self.search_offset)
        self.last_count = total
        mode = '연습용 파일' if not self.settings['source'] else self.source.name
        self.result_count.configure(text=f'{mode} · {total}개')
        if not rows:
            label(self.results, '아직 찾은 파일이 없어요.\n다른 단어로 찾아보거나, 찾을 폴더를 연결해 주세요.',
                  13, GREEN, justify='left').pack(anchor='w', pady=20)
        for row in rows:
            card = tk.Frame(self.results, bg=WHITE); card.pack(fill='x', pady=5)
            actions = tk.Frame(card, bg=WHITE); actions.pack(side='right', padx=8, pady=12)
            button(actions, '열기', lambda r=row: self.open_file(r['path']), primary=True).pack(side='left', padx=4)
            button(actions, '미리보기', lambda r=row: self.preview(r)).pack(side='left')
            info = tk.Frame(card, bg=WHITE); info.pack(side='left', fill='both', expand=True, padx=14, pady=12)
            label(info, row['name'], 13, bold=True, wraplength=360, justify='left', anchor='w').pack(fill='x')
            label(info, Path(row['path']).parent.name, 11, GREEN, anchor='w').pack(fill='x', pady=(4, 0))
        if total > 30:
            nav = tk.Frame(self.results, bg=BG); nav.pack(fill='x', pady=10)
            if self.search_offset: button(nav, '이전', lambda: self.paginate(-30)).pack(side='left')
            label(nav, f'{self.search_offset+1}–{min(total,self.search_offset+30)} / {total}', 12).pack(side='left', padx=12)
            if self.search_offset+30 < total: button(nav, '다음', lambda: self.paginate(30)).pack(side='left')

    def do_search(self, speak=True, reply_surface=None):
        query = self.query.get().strip()
        if reply_surface is not None and query.replace(' ','') in {'춤춰줘','훌라훌라','정리해줘','꾸미기','조용히해줘','우리방','작업트레이'}:
            return super().do_search(speak, reply_surface)
        if self.busy:
            return self.status.set('찾는 중이에요. 잠시만 기다려 주세요.')
        query = self.query.get().strip(); self.search_offset = 0
        if not query:
            self.result_cache = []; self.last_submitted = ''; self.render_results(); return
        source, vault = self.source, self.vault
        context=self.query_context if reply_surface is not None and reply_surface.refining else ''
        within={r['path'] for r in reply_surface.rows} if reply_surface is not None and reply_surface.refining else None
        self.search_fx={'active':True,'interactive':bool(reply_surface or speak),'started':time.monotonic(),
                        'phase':'파일 이름과 내용을 찾고 있어','activity':'search','outcome':'','until':0}
        if reply_surface is not None: reply_surface.start_search()
        if speak:self.pet_event('돋보기 들고 찾아볼게!',2,speak=True,explicit=True)
        def work():
            try:
                rows,resolved=self.library.smart_search(query,[source,vault],context)
                if within is not None:rows=[r for r in rows if r['path'] in within]
                return rows,resolved
            except Exception:
                self.events.put(lambda:self.search_fx.update(active=False,outcome='error',until=0))
                raise
        def finish(result):
            self.result_cache, self.query_context = result
            self.last_submitted = query
            self.last_reply = f'{len(self.result_cache)}개 찾았어요. 파일 옆의 “열기”를 눌러 주세요.'
            self.status.set(self.last_reply)
            self.search_fx.update(active=False,outcome='found' if self.result_cache else 'empty',until=time.monotonic()+3)
            if reply_surface is not None:reply_surface.results(self.result_cache)
            elif self.bubble and self.bubble.alive() and self.bubble.refinement and self.bubble.refinement.query==query:
                self.bubble.results(self.result_cache)
            if speak:
                self.pet_event(f'{len(self.result_cache)}개 찾았어!' if self.result_cache else '다른 단서로도 찾아볼까?',2 if self.result_cache else 1,speak=True,explicit=True)
                self.pet.action=2 if self.result_cache else 0; self.pet.action_until=time.time()+1.5
            if self.page == 'home':
                self.render_results(); self.reply_label.configure(text=self.last_reply)
        self.run_job(work, finish, '파일을 찾고 있어요…')

    def page_organize(self):
        label(self.content, '파일 정리, 두 번만 누르세요.', 24, bold=True).pack(anchor='w', pady=(0, 12))
        label(self.content, '먼저 이동할 목록을 보고, 괜찮으면 정리해요.', 13, GREEN).pack(anchor='w')
        panel = tk.Frame(self.content, bg=WHITE); panel.pack(fill='x', pady=20)
        sample = not self.settings['source']
        label(panel, '정리할 곳: ' + ('연습용 폴더' if sample else self.source.name), 16, bold=True).pack(anchor='w', padx=20, pady=(20, 8))
        label(panel, '연습 중이에요. 내 파일은 건드리지 않아요.' if sample else str(self.source),
              12, GREEN, wraplength=620, justify='left').pack(anchor='w', padx=20)
        bar = tk.Frame(panel, bg=WHITE); bar.pack(anchor='w', padx=20, pady=12)
        button(bar, '내 바탕화면 선택', lambda: self.choose_source(True)).pack(side='left', padx=(0, 8))
        button(bar, '다른 폴더 선택', lambda: self.choose_source(False)).pack(side='left')
        label(panel, '정리한 파일은 여기에 모여요', 13, bold=True).pack(anchor='w', padx=20, pady=(8, 4))
        label(panel, str(self.vault), 12, MUTED, wraplength=620, justify='left').pack(anchor='w', padx=20)
        button(panel, '보관 폴더 바꾸기', self.choose_vault).pack(anchor='w', padx=20, pady=12)
        button(self.content, '① 정리할 파일 확인하기', self.plan_clean, primary=True).pack(fill='x', pady=8)
        button(self.content, '방금 정리한 것 되돌리기', self.undo).pack(fill='x', pady=8)
        label(self.content, '파일을 삭제하지 않고 보관 폴더로 옮겨요.\n폴더와 바로가기는 그대로 두어요.',
              13, GREEN, justify='left').pack(anchor='w', pady=14)
        self.auto_var = tk.BooleanVar(value=self.settings['auto'])
        tk.Checkbutton(self.content, text='다음부터 자동으로 정리하기 (앱이 켜져 있을 때)',
                       variable=self.auto_var, command=self.toggle_auto, bg=BG,
                       font=(FONT, 12)).pack(anchor='w', pady=8)

    def page_more(self):
        label(self.content, '필요할 때만 꺼내 쓰세요.', 24, bold=True).pack(anchor='w', pady=(0, 20))
        for key, title in [('closet', '짱구 꾸미기'), ('room', '우리 방'), ('tray', '모아 둔 파일'),
                           ('collection', '성장과 보상'), ('tools', '추가 기능과 설정')]:
            button(self.content, title, lambda k=key: self.show(k)).pack(fill='x', pady=7)
        label(self.content, 'AI 없이도 이름·본문 검색과 파일 정리를 사용할 수 있어요.',
              12, GREEN, wraplength=640).pack(anchor='w', pady=20)

    def cleaned(self, result):
        super().cleaned(result)
        done, errors = result
        if done:
            messagebox.showinfo('정리 끝', f'{len(done)}개 파일을 보관 폴더로 옮겼어요.\n\n'
                                '되돌리고 싶으면 “방금 정리한 것 되돌리기”를 누르세요.', parent=self.root)

    def page_tools(self):
        if not getattr(sys, 'frozen', False):
            return super().page_tools()
        label(self.content, '사용 설정', 24, bold=True).pack(anchor='w', pady=(0, 20))
        self.check(self.content, 'sound', '짱구 목소리 듣기')
        self.check(self.content, 'topmost', '짱구를 다른 창 위에 표시')
        self.check(self.content, 'startup', '컴퓨터를 켜면 짱구도 시작')
        label(self.content, '이 간편판은 파일 찾기와 정리를 바로 사용할 수 있어요.\n'
              'AI 대화와 음성 검색은 포함되어 있지 않아요.', 13, GREEN,
              justify='left', wraplength=620).pack(anchor='w', pady=24)
