"""Resizable results list with a persistent side preview."""
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from datetime import datetime
import weakref
from PIL import Image, ImageTk, ImageDraw
from result_refinement import ResultRefinement
from search_status import coverage_text


class ResultBrowser:
    def __init__(self,app,rows):
        self.app=app; self.rows=list(rows); self.visible=[]; self.photo=None; self.row_ids={}
        self.search_query=getattr(app,'last_submitted','')
        if not hasattr(app,'result_browsers'):app.result_browsers=weakref.WeakSet()
        app.result_browsers.add(self)
        self.win=tk.Toplevel(app.root); self.win.title('찾은 파일 · 목록과 미리보기')
        from accessibility import fit_window
        fit_window(self.win,1050,720,620,400)
        top=ttk.Frame(self.win,padding=12); top.pack(fill='x')
        self.query=tk.StringVar(); self.mode=tk.StringVar(value='전체 결과')
        top.columnconfigure(1,weight=1)
        ttk.Label(top,text='결과 안에서 찾기').grid(row=0,column=0,sticky='w')
        entry=ttk.Entry(top,textvariable=self.query,width=16); entry.grid(row=0,column=1,sticky='ew',padx=8)
        from ime_entry import attach
        self.ime=attach(entry)
        def apply_query():
            return self.ime.commit_then(self.refresh) if self.ime else self.refresh()
        entry.bind('<Return>',lambda e:apply_query())
        combo=ttk.Combobox(top,textvariable=self.mode,state='readonly',width=14,values=['전체 결과','일치하는 파일','관련 후보','PDF만'])
        combo.grid(row=1,column=0,sticky='w',pady=(8,0)); combo.bind('<<ComboboxSelected>>',lambda e:self.refresh())
        ttk.Button(top,text='적용',command=apply_query).grid(row=0,column=2,padx=5)
        ttk.Button(top,text='초기화',command=self.reset).grid(row=1,column=2,pady=(8,0))
        summary=tk.Frame(self.win,bg='#F4F6F2'); summary.pack(fill='x',padx=12,pady=(0,8))
        self.match_count=sum(r.get('group')=='일치하는 파일' for r in self.rows)
        self.filter_buttons={}
        for mode,title,count,bg,fg in [
            ('일치하는 파일','✓  일치하는 파일',self.match_count,'#DFF2E6','#175E3C'),
            ('관련 후보','?  관련 후보',len(self.rows)-self.match_count,'#F2F0E9','#685F4A'),
            ('전체 결과','전체 결과',len(self.rows),'#EEF0F3','#465363')]:
            b=tk.Button(summary,text=f'{title}  {count}개',command=lambda m=mode:self.choose_mode(m),
                        bg=bg,fg=fg,activebackground=bg,activeforeground=fg,
                        font=('맑은 고딕',12,'bold'),relief='flat',bd=2,padx=8,pady=12,cursor='hand2',wraplength=170)
            b.pack(side='left',fill='x',expand=True,padx=(0,6)); self.filter_buttons[mode]=b
        ttk.Label(self.win,text='초록색 파일부터 확인하세요. 관련 후보는 제목 옆 ▶를 눌러 펼칠 수 있어요.',
                  padding=(12,0)).pack(anchor='w')
        self.count=ttk.Label(self.win,padding=(12,0)); self.count.pack(anchor='w')
        coverage=getattr(getattr(app,'library',None),'search_coverage',{})
        self.coverage=ttk.Label(self.win,wraplength=980,padding=(12,4))
        self.coverage.pack(anchor='w')
        self.update_coverage(coverage)
        pane=tk.PanedWindow(self.win,orient='horizontal',sashwidth=8,bd=0); pane.pack(fill='both',expand=True,padx=12,pady=12)
        left=ttk.Frame(pane); right=ttk.Frame(pane,padding=10); pane.add(left,stretch='always'); pane.add(right,stretch='always')
        style=ttk.Style(self.win)
        style.configure('SearchResults.Treeview',rowheight=34,font=('맑은 고딕',10))
        self.tree=ttk.Treeview(left,columns=('date',),selectmode='browse',style='SearchResults.Treeview')
        self.tree.heading('#0',text='파일명'); self.tree.column('#0',width=250,minwidth=150)
        for key,title,width in [('date','수정한 날짜',100)]:
            self.tree.heading(key,text=title); self.tree.column(key,width=width,minwidth=65)
        self.tree.tag_configure('match',background='#EDF8F0',foreground='#175E3C',font=('맑은 고딕',10,'bold'))
        self.tree.tag_configure('candidate',background='#FAFAF7',foreground='#686B64')
        self.tree.tag_configure('match_header',background='#CBE8D5',foreground='#11492E',font=('맑은 고딕',11,'bold'))
        self.tree.tag_configure('candidate_header',background='#EAE8DF',foreground='#575649',font=('맑은 고딕',11,'bold'))
        bar=ttk.Scrollbar(left,command=self.tree.yview); bar.pack(side='right',fill='y')
        self.tree.configure(yscrollcommand=bar.set); self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',lambda e:self.preview())
        self.tree.bind('<Double-1>',lambda e:self.open())
        self.tree.bind('<Return>',lambda e:self.open())
        self.title=ttk.Label(right,text='파일을 고르면 여기에 표시돼요',wraplength=340); self.title.pack(fill='x')
        self.badge=tk.Label(right,text='',font=('맑은 고딕',11,'bold'),anchor='w',padx=10,pady=8)
        self.badge.pack(fill='x',pady=(8,0))
        self.image=ttk.Label(right); self.image.pack(pady=5)
        self.body=tk.Text(right,wrap='word',font=('맑은 고딕',10),relief='flat',height=10)
        bodybar=ttk.Scrollbar(right,command=self.body.yview); bodybar.pack(side='right',fill='y')
        self.body.configure(yscrollcommand=bodybar.set); self.body.pack(fill='both',expand=True)
        actions=ttk.Frame(self.win,padding=10); actions.pack(fill='x')
        ttk.Label(actions,text='한 번 클릭: 미리보기 · 두 번 클릭 또는 Enter: 원본 열기').pack(anchor='w',pady=(0,6))
        self.open_button=ttk.Button(actions,text='원본 열기',command=self.open); self.open_button.pack(side='right')
        self.preview_button=ttk.Button(actions,text='전체 미리보기',command=lambda:self.open(True)); self.preview_button.pack(side='right',padx=6)
        self.refresh()
        self.pane=pane
        self.apply_readability()
        def fit_content(event):
            if event.widget is not self.win:return
            self.coverage.configure(wraplength=max(220,event.width-30))
            for control in self.filter_buttons.values():control.configure(wraplength=max(90,(event.width-66)//3))
            pane.configure(orient='vertical' if event.width<850 else 'horizontal')
        self.win.bind('<Configure>',fit_content,add='+')

    def apply_readability(self):
        from accessibility import apply_fonts
        scale=self.app.settings.get('text_scale',1.0) if hasattr(self.app,'settings') else 1.0
        apply_fonts(self.win,scale)
        ttk.Style(self.win).configure('SearchResults.Treeview',rowheight=round(34*scale),font=('맑은 고딕',round(10*scale)))
        for tag,size in [('match',10),('match_header',11),('candidate_header',11)]:
            self.tree.tag_configure(tag,font=('맑은 고딕',round(size*scale),'bold'))

    def update_coverage(self,coverage):
        scopes=' · '.join(coverage.get('roots',[]))
        self.coverage.configure(text=('찾은 곳: '+scopes+'\n' if scopes else '')+coverage_text(coverage)+
                                ('\n내용을 분석 중이에요. 새로 찾은 파일은 이 목록에 자동으로 추가돼요.' if getattr(self.app,'indexing',False) else ''))

    def update_results(self,rows,query):
        if not self.win.winfo_exists() or query!=self.search_query:return
        self.update_coverage(getattr(self.app.library,'search_coverage',{}))
        if self.rows==rows:return
        self.rows=list(rows)
        self.match_count=sum(r.get('group')=='일치하는 파일' for r in self.rows)
        for mode,title,count in [('일치하는 파일','✓  일치하는 파일',self.match_count),
                                 ('관련 후보','?  관련 후보',len(self.rows)-self.match_count),
                                 ('전체 결과','전체 결과',len(self.rows))]:
            self.filter_buttons[mode].configure(text=f'{title}  {count}개')
        self.refresh(preserve_view=True)

    def choose_mode(self,mode):
        self.mode.set(mode); self.refresh()

    def reset(self):
        self.query.set(''); self.mode.set('전체 결과'); self.refresh()

    def refresh(self,preserve_view=False):
        selected=self.selected()
        scroll=self.tree.yview()
        opened={iid:self.tree.item(iid,'open') for iid in self.tree.get_children()} if preserve_view else {}
        model=ResultRefinement(self.rows); model.facet('text',self.query.get())
        mode=self.mode.get()
        if mode=='PDF만': model.facet('pdf')
        elif mode!='전체 결과': model.select(mode,lambda r:(r.get('group')=='일치하는 파일')==(mode=='일치하는 파일'))
        self.visible=model.rows
        children=self.tree.get_children()
        if children:self.tree.delete(*children)
        self.row_ids={}
        matched=[r for r in self.visible if r.get('group')=='일치하는 파일']
        candidates=[r for r in self.visible if r.get('group')!='일치하는 파일']
        if matched:
            self.tree.insert('', 'end',iid='matches',text=f'✓  일치하는 파일  {len(matched)}개 · 먼저 확인',open=opened.get('matches',True),tags=('match_header',))
        if candidates:
            self.tree.insert('', 'end',iid='candidates',text=f'?  관련 후보  {len(candidates)}개 · 더 살펴보기',
                             open=opened.get('candidates',not matched or mode=='관련 후보'),tags=('candidate_header',))
        for i,row in enumerate(self.visible):
            direct=row.get('group')=='일치하는 파일'; self.row_ids[str(i)]=row
            self.tree.insert('matches' if direct else 'candidates','end',iid=str(i),text=('✓  ' if direct else '   ')+row['name'],
                             values=(datetime.fromtimestamp(row['mtime']).strftime('%Y.%m.%d') if row.get('mtime') else '—',),tags=('match' if direct else 'candidate',))
        self.count.configure(text=f'현재 조건: 일치 {len(matched)}개 · 관련 후보 {len(candidates)}개  /  검색 결과 총 {len(self.rows)}개')
        for key,b in self.filter_buttons.items(): b.configure(relief='sunken' if mode==key else 'flat')
        preferred=next((key for key,row in self.row_ids.items() if selected and row['path']==selected['path']),None)
        if preferred is None:
            preferred=next((key for key,row in self.row_ids.items() if row.get('group')=='일치하는 파일'),next(iter(self.row_ids),None))
        if preferred is not None:
            if not preserve_view:self.tree.item(self.tree.parent(preferred),open=True)
            self.tree.selection_set(preferred)
            if preserve_view:self.tree.yview_moveto(scroll[0])
            else:self.tree.see(preferred)
        self.preview()

    def selected(self):
        selection=self.tree.selection()
        return self.row_ids.get(selection[0]) if selection else None

    def preview(self):
        row=self.selected(); self.image.configure(image=''); self.photo=None
        self.body.configure(state='normal'); self.body.delete('1.0','end')
        for button in (self.open_button,self.preview_button): button.configure(state='normal' if row else 'disabled')
        if row:
            self.title.configure(text=row['name'])
            direct=row.get('group')=='일치하는 파일'
            self.badge.configure(text='✓  일치하는 파일' if direct else '?  관련 후보 · 내용을 확인해 주세요',
                                 bg='#DFF2E6' if direct else '#F2F0E9',fg='#175E3C' if direct else '#685F4A')
            thumb=row.get('thumbnail')
            if thumb and Path(thumb).is_file():
                try:
                    with Image.open(thumb) as im:
                        im=im.convert('RGB');im.thumbnail((320,240))
                        draw=ImageDraw.Draw(im)
                        for detection in row.get('detected_objects',[]):
                            box=detection['box'];w,h=im.size
                            draw.rectangle((max(0,box['xmin'])*w,max(0,box['ymin'])*h,min(1,box['xmax'])*w,min(1,box['ymax'])*h),outline='#167449',width=3)
                        self.photo=ImageTk.PhotoImage(im)
                    self.image.configure(image=self.photo)
                except OSError: pass
            self.body.insert('1.0','찾은 이유\n'+row.get('reason','이름 또는 본문에서 검색 조건을 확인했어요.')+'\n\n관련 내용\n'+row.get('evidence','')+'\n\n위치: '+row['path']+'\n\n'+(row.get('body','')[:16000] or '추출된 본문이 없어요. 원본을 열어 확인해 주세요.'))
        else:
            self.badge.configure(text='',bg=self.win.cget('background'))
            self.title.configure(text='그룹 아래 파일을 선택하면 여기에 보여요.' if self.visible else '조건에 맞는 파일이 없어요. 초기화를 눌러 보세요.')
        self.body.configure(state='disabled')

    def open(self,preview=False):
        row=self.selected()
        if row:
            if preview:self.app.preview(row)
            else:self.app.open_file(row['path'])
