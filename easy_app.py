"""Small, task-first interface. File operations still use the existing safety checks."""
from pathlib import Path
import sys
import time
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import messagebox, filedialog, simpledialog
from enhancements import EnhancedApp
from app import BG, WHITE, INK, MUTED, GREEN, ORANGE, FONT, button, label
from app_version import VERSION, source_stamp
from search_status import summary, coverage_text
from photo_controller import PhotoController


class EasyApp(PhotoController,EnhancedApp):
    def save_search_state(self,mode,coverage,count,error=None):
        """Latest diagnostic snapshot only; no file contents or result names."""
        import json
        state=dict(mode=mode,source=str(self.source),query=self.last_submitted,
                   results=count,coverage=coverage,error=error,updated=time.time())
        try:(self.data/'search-status.json').write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
        except OSError:pass

    def __init__(self,root,data=None):
        self.started_stamp=source_stamp()
        self.is_photo_search=False
        super().__init__(root,data)
        self.active_library=self.library
        self.initialize_photo_search()
        def voice_notice(text):
            def show():
                # Voice setup notices must never overwrite the search answer.
                self.last_voice_notice=text
                if not self.query.get().strip() and not self.busy:self.status.set(text)
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
                self.bubble.clear(); self.bubble.resize(round(185*self.bubble.text_scale))

    def voice_settings(self):
        from voice_setup_ui import show
        return show(self)

    def recorded_voice_settings(self):
        dialog=tk.Toplevel(self.root); dialog.title('클릭 반응과 목소리'); dialog.geometry('490x420')
        dialog.attributes('-topmost',True)
        label(dialog,'클릭하면 반응하고, 짧게 인사해요.',17,bold=True).pack(pady=15)
        label(dialog,'짧은 녹음을 연결해 직접 들어볼 수 있어요.\n새 답변 말하기는 목소리 설정에서 준비해 주세요.',
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
        from accessibility import fit_window
        fit_window(self.root,1000,720,640,420)
        self.settings.setdefault('text_scale',1.0)
        side = tk.Frame(self.root, bg='#F0EEE6', width=190)
        side.pack(side='left', fill='y'); side.pack_propagate(False)
        label(side, '짱구 주머니', 20, bold=True).pack(pady=(28, 8))
        label(side, '찾고, 정리하고, 되돌려요.', 11, GREEN).pack(pady=(0, 24))
        self.sidebar=side
        self.nav = {}
        for key, title in [('home', '파일 찾기'), ('organize', '파일 정리'),
                           ('history', '정리 기록'), ('more', '더보기')]:
            b = button(side, title, lambda k=key: self.show(k), bg='#F0EEE6', anchor='w')
            b.configure(font=(FONT, 13, 'bold'), pady=16)
            b.pack(fill='x', padx=12, pady=5); self.nav[key] = b
        button(side, '앱 종료', self.quit).pack(side='bottom', fill='x', padx=12, pady=14)
        self.restart_button=button(side,'다시 시작',self.restart)
        self.restart_button.pack(side='bottom',fill='x',padx=12,pady=4)
        label(side,'버전 '+VERSION,9,GREEN).pack(side='bottom',pady=4)
        button(side, '창 접기', self.hide).pack(side='bottom', fill='x', padx=12)
        button(side,'글씨 크게 / 기본',self.toggle_text_size).pack(side='bottom',fill='x',padx=12,pady=4)
        button(side,'도움말 · 상태 확인',lambda:self.show('help')).pack(side='bottom',fill='x',padx=12,pady=4)
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side='left', fill='both', expand=True, padx=24, pady=24)
        from accessibility import scroll_page
        self.content=scroll_page(self.main,BG)
        label(self.main, '', 11, GREEN, textvariable=self.status, wraplength=690,
              anchor='w', justify='left').pack(fill='x', pady=(12, 0))

    def show(self,page):
        super().show(page)
        self.apply_readability()

    def apply_readability(self):
        from accessibility import apply_fonts
        scale=self.settings.get('text_scale',1.0)
        apply_fonts(self.root,scale)
        if hasattr(self,'sidebar'):self.sidebar.configure(width=round(190*min(scale,1.25)))

    def toggle_text_size(self):
        self.settings['text_scale']=1.0 if self.settings.get('text_scale',1.0)>1 else 1.3
        self.save();self.show(self.page)
        if self.bubble and self.bubble.alive():self.bubble.close();self.toggle_bubble()
        for browser in list(getattr(self,'result_browsers',[])):
            if browser.win.winfo_exists():browser.apply_readability()

    def page_help(self):
        from diagnostics import inspect_environment
        label(self.content,'처음부터 같이 해봐요',22,bold=True).pack(anchor='w',pady=(0,14))
        for text,action in [('1. 찾을 폴더 연결',lambda:self.choose_source(False)),
                            ('2. 연습용 급여명세서 찾아보기',self.practice_search),
                            ('3. 이름·위치 몰라도 사진 찾기',lambda:self.quick('사진 보여줘')),
                            ('4. 짱구 목소리 설정',self.voice_settings)]:
            button(self.content,text,action).pack(fill='x',pady=5)
        label(self.content,'문서는 연결한 폴더와 보관함에서 찾아요. 사진은 바탕화면·다운로드·사진·문서 폴더를 함께 확인해요.\n사진 모음에서 찾을 폴더를 더 추가할 수 있어요. 파일은 PC 안에서 읽어요.\n정리는 이동할 목록을 확인한 다음 실행하며 되돌릴 수 있어요.',12,GREEN,wraplength=480,justify='left').pack(anchor='w',pady=18)
        checks=inspect_environment(self.data)
        for item in checks:
            label(self.content,('✓ ' if item['ok'] else '확인: ')+item['message'],12,GREEN if item['ok'] else ORANGE,wraplength=480,justify='left').pack(anchor='w',pady=5)
        if getattr(self,'last_voice_notice',''):
            label(self.content,'목소리: '+self.last_voice_notice,12,ORANGE,wraplength=480,justify='left').pack(anchor='w',pady=5)
        def save_report():
            import json
            path=self.data/'diagnostics.json';path.write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.set('파일 내용·검색어·목소리를 포함하지 않은 진단 기록을 저장했어요.')
            self.reveal(str(path))
        button(self.content,'진단 기록 저장',save_report).pack(anchor='w',pady=12)

    def practice_search(self):
        from knowledge import Knowledge
        from result_browser import ResultBrowser
        path=self.demo/'첨부_연습용.txt'
        path.write_text('연습용 급여명세서\n기본급 3000000원\n공제합계 200000원\n실수령액 2800000원',encoding='utf-8')
        library=Knowledge(self.data/'practice-index');library.index([self.demo])
        rows,_=library.smart_search('급여명세서 찾아줘',[self.demo])
        browser=ResultBrowser(self,rows);browser.search_query='연습'
        browser.win.title('연습 · 이름 없는 급여명세서 찾기')
        browser.update_coverage(dict(library.search_coverage,roots=['연습용 폴더 · 내 파일과 별개']))

    def show_upgrade_hint(self):
        if source_stamp()!=self.started_stamp:
            self.restart_button.configure(text='새 버전 적용 · 다시 시작',bg='#FFE4B9')
            self.status.set('개선된 버전이 준비됐어요. 다시 시작하면 적용돼요.')
        self.root.after(30000,self.show_upgrade_hint)

    def choose_source(self,desktop=False):
        super().choose_source(desktop)
        if self.settings['source'] and not getattr(self.root,'pet_only_start',False):self.show('home')

    def page_home(self):
        label(self.content, '어떤 파일을 찾으세요?', 25, bold=True).pack(anchor='w', pady=(0, 10))
        label(self.content, '이름을 몰라도 괜찮아요. 문서 내용이나 사진 속 모습을 말해 주세요.', 13, GREEN).pack(anchor='w')
        panel = tk.Frame(self.content, bg='#FFF0DF'); panel.pack(fill='x', pady=16)
        if not self.settings['source']:
            label(panel, '문서 검색은 아직 연습용이에요.', 14, bold=True).pack(anchor='w', padx=16, pady=(14, 4))
            label(panel, '내 문서는 폴더를 연결해 주세요. 사진은 아래에서 바로 찾아볼 수 있어요.', 12,wraplength=620).pack(anchor='w', padx=16)
            button(panel, '내 바탕화면 연결하기', lambda: self.choose_source(True), primary=True).pack(anchor='w', padx=16, pady=12)
        else:
            label(panel, '문서 찾는 곳: ' + str(self.source), 12, bold=True,wraplength=620,justify='left').pack(anchor='w', padx=16, pady=(12,4))
            line=tk.Frame(panel,bg='#FFF0DF'); line.pack(fill='x',padx=16,pady=(0,10))
            label(line,'보관함도 함께 찾아요. 다른 폴더는 연결해야 검색해요.',10,GREEN).pack(side='left')
            button(line,'찾을 폴더 바꾸기',lambda:self.choose_source(False)).pack(side='right')
        search = tk.Frame(self.content, bg=WHITE); search.pack(fill='x', pady=(0, 8))
        self.entry = tk.Entry(search, textvariable=self.query, font=(FONT, 16), relief='flat')
        self.entry.pack(side='left', fill='x', expand=True, padx=12, ipady=14)
        from ime_entry import attach
        self.search_ime=attach(self.entry)
        def submit():
            action=lambda:self.do_search(True)
            return self.search_ime.commit_then(action) if self.search_ime else action()
        self.entry.bind('<Return>', lambda e: submit())
        button(search, '찾기', submit, primary=True).pack(side='right', padx=6, pady=6)
        examples = tk.Frame(self.content, bg=BG); examples.pack(fill='x', pady=8)
        for text in ['급여명세서 찾아줘', '사진 보여줘', '전반적으로 파란 사진에 의자가 있어 찾아줘']:
            button(examples, text, lambda t=text: self.quick(t)).pack(anchor='w',pady=3)
        prepare=not self.ai.ready_for('photo')
        button(self.content, '사진 찾기 준비' if prepare else '새 사진도 분석하기',
               self.setup_photo_ai if prepare else self.resume_photo_analysis).pack(anchor='w')
        self.coverage_label=label(self.content,'',10,GREEN,wraplength=650,justify='left')
        self.coverage_label.pack(anchor='w',pady=4)
        self.reply_label = label(self.content, self.last_reply, 12, GREEN, wraplength=650, justify='left')
        self.reply_label.pack(anchor='w', pady=6)
        top = tk.Frame(self.content, bg=BG); top.pack(fill='x')
        self.result_count = label(top, '', 12, bold=True); self.result_count.pack(side='left')
        button(top, '목록 새로고침', self.reindex).pack(side='right')
        button(top,'결과 크게 보기',self.open_result_browser).pack(side='right',padx=6)
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
        mode = '내 PC 사진' if self.is_photo_search else ('연습용 파일' if not self.settings['source'] else self.source.name)
        self.result_count.configure(text=f'{mode} · {total}개')
        self.coverage_label.configure(text=self.photo_summary() if self.is_photo_search else coverage_text(getattr(self.library,'search_coverage',{})))
        if not rows:
            label(self.results, '현재 확인한 파일에서는 찾지 못했어요.\n찾을 폴더와 분석 상태를 확인해 주세요.',
                  13, GREEN, justify='left').pack(anchor='w', pady=20)
            actions=tk.Frame(self.results,bg=BG); actions.pack(anchor='w')
            button(actions,'다른 폴더 찾기',self.choose_photo_folder if self.is_photo_search else lambda:self.choose_source(False)).pack(side='left',padx=(0,8))
            button(actions,'분석 이어하기',self.resume_photo_analysis if self.is_photo_search else self.reindex).pack(side='left')
        for row in rows:
            card = tk.Frame(self.results, bg=WHITE); card.pack(fill='x', pady=5)
            if row.get('thumbnail'):
                try:
                    with Image.open(row['thumbnail']) as original:
                        picture=original.convert('RGB'); picture.thumbnail((100,100))
                    photo=ImageTk.PhotoImage(picture)
                    tile=tk.Label(card,image=photo,bg=WHITE,cursor='hand2'); tile.image=photo
                    tile.pack(side='left',padx=8,pady=8)
                    tile.bind('<Button-1>',lambda e,r=row:self.preview(r))
                except (OSError,ValueError): pass
            actions = tk.Frame(card, bg=WHITE); actions.pack(side='right', padx=8, pady=12)
            button(actions, '열기', lambda r=row: self.open_file(r['path']), primary=True).pack(side='left', padx=4)
            button(actions, '미리보기', lambda r=row: self.preview(r)).pack(side='left')
            info = tk.Frame(card, bg=WHITE); info.pack(side='left', fill='both', expand=True, padx=14, pady=12)
            label(info, row['name'], 13, bold=True, wraplength=360, justify='left', anchor='w').pack(fill='x')
            label(info, Path(row['path']).parent.name, 11, GREEN, anchor='w').pack(fill='x', pady=(4, 0))
            if row.get('reason'):
                label(info,row['reason'],10,GREEN,wraplength=330,justify='left',anchor='w').pack(fill='x',pady=4)
        if total > 30:
            nav = tk.Frame(self.results, bg=BG); nav.pack(fill='x', pady=10)
            if self.search_offset: button(nav, '이전', lambda: self.paginate(-30)).pack(side='left')
            label(nav, f'{self.search_offset+1}–{min(total,self.search_offset+30)} / {total}', 12).pack(side='left', padx=12)
            if self.search_offset+30 < total: button(nav, '다음', lambda: self.paginate(30)).pack(side='left')
        self.apply_readability()

    def refresh_index_results(self):
        if self.restarting or self.busy:return
        query=self.query.get().strip()
        from reactions import quick_intent
        if query and query==self.last_submitted and quick_intent(query) not in {'CLEAN','CLOSET','DANCE','QUIET','ROOM','TRAY','LAUNCHER'}:
            self.do_search(speak=False,refresh=True)
        elif not query and self.page=='home':self.render_results()

    def do_search(self, speak=True, reply_surface=None, refresh=False):
        query = self.query.get().strip()
        from visual_query import visual_intent
        from photo_query import is_photo_followup
        compact=''.join(query.split()).rstrip('.!?')
        if compact in {'안녕','안녕하세요','고마워','고마워요','도와줘','뭐할수있어','뭘할수있어','무엇을할수있어','사용법'}:
            text='문서 내용으로 찾기, 사진 모습으로 찾기, 바탕화면 정리를 도와줄게! “급여명세서 찾아줘”, “파란 의자가 있는 사진”, “정리해줘”처럼 말해 줘.'
            self.last_reply=text;self.status.set(text)
            if reply_surface is not None:reply_surface.reply(text)
            if speak:self.pet_event(text,2,speak=True,explicit=True)
            return
        if visual_intent(query)['active'] or (self.is_photo_search and is_photo_followup(query,self.photo_resolved)):
            return self.handle_photo_search(query,speak,reply_surface,refresh)
        from reactions import quick_intent
        if quick_intent(query) in {'CLEAN','CLOSET','DANCE','QUIET','ROOM','TRAY','LAUNCHER'}:
            return super().do_search(speak, reply_surface)
        if self.busy:
            return self.status.set('찾는 중이에요. 잠시만 기다려 주세요.')
        self.is_photo_search=False;self.active_library=self.library
        query = self.query.get().strip()
        if not refresh:self.search_offset = 0
        if not query:
            self.result_cache = []; self.last_submitted = ''; self.render_results(); return
        source, vault = self.source, self.vault
        context=self.query_context if reply_surface is not None and reply_surface.refining else ''
        within={r['path'] for r in reply_surface.rows} if reply_surface is not None and reply_surface.refining else None
        search_query=self.query_context if refresh and query==self.last_submitted else query
        self.search_fx={'active':True,'interactive':bool(reply_surface or speak),'started':time.monotonic(),
                        'phase':'파일 이름과 내용을 찾고 있어','activity':'search','outcome':'','until':0}
        if reply_surface is not None: reply_surface.start_search()
        if speak:self.pet_event('돋보기 들고 찾아볼게!',2,speak=True,explicit=True)
        def work():
            try:
                rows,resolved=self.library.smart_search(search_query,[source,vault],context)
                if within is not None:rows=[r for r in rows if r['path'] in within]
                return rows,resolved
            except Exception:
                self.events.put(lambda:self.search_fx.update(active=False,outcome='error',until=0))
                raise
        def finish(result):
            self.result_cache, self.query_context = result
            self.result_cache=[row for row in self.result_cache if row['path'] not in getattr(self,'photo_removed',set())]
            self.last_submitted = query
            notice=getattr(self.library,'search_notice','')
            if self.library.ai_error:notice='AI 분석에 문제가 있어 지금은 읽은 본문 결과만 보여요. 다시 분석을 눌러 주세요.'
            self.last_reply=summary(len(self.result_cache),getattr(self.library,'search_coverage',{}),notice,self.indexing,not self.settings['source'])
            self.save_search_state('documents',self.library.search_coverage,len(self.result_cache),self.library.ai_error)
            self.status.set(self.last_reply)
            self.search_fx.update(active=False,outcome='found' if self.result_cache else 'empty',until=time.monotonic()+3)
            if reply_surface is not None:reply_surface.results(self.result_cache)
            elif self.bubble and self.bubble.alive() and self.bubble.refinement and self.bubble.refinement.query==query and not self.bubble.refinement.steps:
                self.bubble.results(self.result_cache)
            for browser in list(getattr(self,'result_browsers',())):
                browser.update_results(self.result_cache,query)
            if speak:
                self.pet_event(f'{len(self.result_cache)}개 후보를 찾았어. 미리보기로 확인해 봐!' if self.result_cache else '지금 확인한 파일에서는 못 찾았어. 찾는 곳부터 확인해 볼까?',2 if self.result_cache else 1,speak=True,explicit=True)
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
        button(self.content,'사진·음성 인식 준비',self.setup_ai).pack(anchor='w',pady=10)
        button(self.content,'짱구 목소리 설정',self.voice_settings).pack(anchor='w',pady=6)
        label(self.content, '파일 찾기와 정리를 바로 사용할 수 있어요.\n'
              '사진 속 사물 찾기와 음성 기능은 모델을 처음 한 번 준비한 뒤 사용해요.', 13, GREEN,
              justify='left', wraplength=620).pack(anchor='w', pady=24)
