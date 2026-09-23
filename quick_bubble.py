"""One search field, one short status, clickable filenames. Extras live in the pet menu."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import time
from pet_bubble import PetBubble
from app import label, WHITE, GREEN, INK, FONT
from monitors import DesktopSpace
from search_status import summary
from file_transfer import bind_file_drag, COPY_MESSAGE


class QuickBubble(PetBubble):
    WIDTH = 400
    HEIGHT = 185

    def __init__(self, app):
        self.app=app; self.pending=False; self.rows=[]; self.offset=0; self.loading_token=0
        self.text_scale=app.settings.get('text_scale',1.0)
        self.WIDTH=round(400*self.text_scale)
        self.refinement=None; self.refining=False; self.result_buttons=[];self.copy_buttons=[];self.pin_buttons=[]
        self._showing_home=False
        self.win=tk.Toplevel(app.root); self.win.withdraw(); self.win.overrideredirect(True)
        self.win.bind('<Destroy>',self._release_variables,add='+')
        key='#010203'; self.win.configure(bg=key); self.win.attributes('-transparentcolor',key)
        self.win.attributes('-topmost',True)
        self.canvas=tk.Canvas(self.win,bg=key,highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        body=tk.Frame(self.canvas,bg=WHITE)
        self.body=body
        self.body_item=self.canvas.create_window(22,18,window=body,anchor='nw',width=self.WIDTH-44,height=142)
        head=tk.Frame(body,bg=WHITE); head.pack(fill='x',pady=(0,12))
        label(head,'뭐 찾아줄까?',18,INK,bold=True).pack(side='left')
        tk.Button(head,text='×',command=self.close,font=(FONT,16),bg=WHITE,fg=GREEN,bd=0,
                  cursor='hand2',takefocus=True).pack(side='right')
        self.text=tk.StringVar()
        search=tk.Frame(body,bg='#F4F4F0',highlightthickness=1,highlightbackground='#D8DDD3')
        search.pack(fill='x')
        self.entry=tk.Entry(search,textvariable=self.text,font=(FONT,15),bg='#F4F4F0',fg=INK,
                            relief='flat',insertbackground=INK)
        self.entry.pack(side='left',fill='x',expand=True,padx=10,ipady=11)
        from ime_entry import attach
        self.ime=attach(self.entry)
        self.send_button=tk.Button(search,text='찾기',command=self.submit_composed,font=(FONT,12,'bold'),
            bg=GREEN,fg=WHITE,activebackground=GREEN,activeforeground=WHITE,bd=0,padx=14,pady=11,cursor='hand2')
        self.send_button.pack(side='right',padx=3,pady=3)
        self.entry.bind('<Return>',lambda e:self.submit_composed()); self.win.bind('<Escape>',lambda e:self.close())
        self.message=tk.StringVar(value='이름을 몰라도 돼! 문서 내용이나 사진 속 모습을 말해 줘.')
        self.note=label(body,'',11,GREEN,textvariable=self.message,wraplength=self.WIDTH-50,justify='left')
        self.note.pack(anchor='w',pady=(10,5))
        from accessibility import scroll_page
        self.content=scroll_page(body,WHITE)
        self.resize(round(230*self.text_scale))
        from accessibility import apply_fonts
        apply_fonts(body,self.text_scale)
        self.show_home(reset=False)
        self.win.update_idletasks(); self.space=DesktopSpace(self.win)
        self.win.deiconify(); self.win.attributes('-alpha',.25); self.follow(); self.pop(0)
        self.win.after(180,self.entry.focus_force)
        app.pet_event('뭘 찾아줄까?',speak=True,explicit=True)
        if getattr(app,'search_fx',None) and app.search_fx['active']: self.start_search()
        elif getattr(app,'bubble_refinement',None):
            self.refinement=app.bubble_refinement; self.rows=self.refinement.rows
            self.draw_results()

    def submit_composed(self):
        if self.ime:return self.ime.commit_then(self.submit)
        self.submit(); return 'break'

    def _release_variables(self,event):
        if event.widget is self.win:
            self.text=None;self.message=None;self.ime=None

    def show_home(self,reset=True):
        self._showing_home=True
        if reset:
            self.refinement=None;self.refining=False;self.app.bubble_refinement=None;self.pending=False;self.rows=[]
        self.clear();self.result_buttons=[];self.copy_buttons=[];self.pin_buttons=[]
        self.send_button.configure(state='normal',text='찾기')
        sample=self.app.is_demo_search() or not self.app.document_roots()
        self.message.set('내 문서를 찾을 폴더를 연결해 줘.' if sample else '이름을 몰라도 돼! 기억나는 말로 찾아봐.')
        shortcuts=tk.Frame(self.content,bg=WHITE);shortcuts.pack(fill='x',pady=(0,6))
        for title,action in [('내 모음',self.app.show_collections),('찾을 폴더',self.app.choose_document_locations)]:
            tk.Button(shortcuts,text=title,command=action,font=(FONT,11),bg=WHITE,fg=GREEN,
                      bd=0,padx=8,pady=7,cursor='hand2').pack(side='left' if title=='내 모음' else 'right')
        pinned=self.app.file_memory.pinned_files(limit=3,include_missing=False)
        recent=pinned or self.app.file_memory.recent(3)
        if recent:
            title=tk.Frame(self.content,bg=WHITE);title.pack(fill='x',pady=(6,4))
            label(title,'★ 고정한 파일' if pinned else '최근 연 파일',10,GREEN).pack(side='left')
            tk.Button(title,text='모두 보기',command=self.app.show_pinned if pinned else self.app.show_recent,
                      font=(FONT,10),bg=WHITE,fg=GREEN,bd=0,cursor='hand2').pack(side='right')
            for row in recent:
                name=row['name']
                if len(name)>30:name=Path(name).stem[:24]+'…'+Path(name).suffix
                tk.Button(self.content,text=name,command=lambda p=row['path']:self.app.open_file(p),
                          font=(FONT,12),anchor='w',wraplength=self.WIDTH-80,justify='left',bg='#F3F5EE',fg=INK,bd=0,padx=10,pady=8,
                          cursor='hand2').pack(fill='x',pady=2)
        self.resize(round((235+(30+len(recent)*44 if recent else 0))*self.text_scale))
        from accessibility import apply_fonts
        apply_fonts(self.body,self.text_scale)
        self.body.update_idletasks()
        needed=self.content.winfo_reqheight()-self.content.master.winfo_height()
        if needed>0:self.resize(self.HEIGHT+needed+8)

    def resize(self,height):
        self.HEIGHT=height; self.win.geometry(f'{self.WIDTH}x{height}')
        c=self.canvas; c.delete('outline'); bottom=height-22
        right=self.WIDTH-2;center=self.WIDTH/2
        c.create_polygon(22,2,right-20,2,right,22,right,bottom-20,right-20,bottom,22,bottom,2,bottom-20,2,22,
                         smooth=True,splinesteps=20,fill=WHITE,outline='#CDD3C8',width=1,tags='outline')
        c.create_polygon(center-18,bottom-2,center,height-2,center+18,bottom-2,fill=WHITE,outline='#CDD3C8',tags='outline')
        c.create_line(center-18,bottom-2,center+18,bottom-2,fill=WHITE,width=3,tags='outline')
        c.tag_lower('outline'); c.itemconfigure(self.body_item,height=height-48)

    def start_search(self):
        if not self.alive(): return
        self._showing_home=False
        self.pending=True; self.clear(); self.resize(round(280*self.text_scale))
        self.message.set('접수했어. 파일 내용을 확인하고 있어!')
        label(self.content,'✓ '+self.app.query.get(),11,INK,wraplength=340,justify='left').pack(anchor='w',pady=5)
        progress=ttk.Progressbar(self.content,mode='indeterminate'); progress.pack(fill='x',pady=8); progress.start(35)
        started=time.monotonic()
        def tick():
            if not self.alive() or not self.pending or not progress.winfo_exists():return
            elapsed=int(time.monotonic()-started)
            self.message.set(f'문서 내용과 사진을 찾고 있어… {elapsed}초'+('\n처음 분석은 시간이 더 걸려. 계속 확인 중이야.' if elapsed>=10 else ''))
            self.win.after(500,tick)
        self.win.after(500,tick)
        self.send_button.configure(state='disabled',text='찾는 중')
        from accessibility import apply_fonts
        apply_fonts(self.body,self.text_scale)

    def draw_results(self):
        if not self.alive(): return
        self._showing_home=False
        self.clear(); self.result_buttons=[];self.copy_buttons=[];self.pin_buttons=[]
        if getattr(self.app,'is_photo_search',False):
            self.message.set(self.app.photo_summary())
            self.resize(round(330*self.text_scale))
            for title,command in [('사진을 크게 모아 보기',self.app.open_photo_gallery),('찾을 폴더 더 추가',self.app.choose_photo_folder),('사진 분석 멈추기',self.app.cancel_photo_search)]:
                tk.Button(self.content,text=title,command=command,font=(FONT,12,'bold'),bg='#F2F5EF',fg=GREEN,bd=0,pady=9).pack(fill='x',pady=4)
            return
        notice=getattr(self.app.library,'search_notice','')
        if self.app.library.ai_error:notice='AI 분석을 완료하지 못했어요. 다시 분석을 눌러 주세요.'
        self.message.set(summary(len(self.rows),getattr(self.app.library,'search_coverage',{}),notice,self.app.indexing,self.app.is_demo_search()))
        page_size=2 if self.text_scale>1 else 3
        shown=self.rows[self.offset:self.offset+page_size]
        self.resize(round((275+len(shown)*68+(38 if len(self.rows)>page_size else 0)+(45 if notice else 0))*self.text_scale))
        toolbar=tk.Frame(self.content,bg=WHITE); toolbar.pack(fill='x',pady=(0,6))
        tk.Button(toolbar,text='미리보기로 크게 보기',command=self.open_results,
                  font=(FONT,11),bd=0,bg='#F2F5EF',fg=GREEN).pack(side='left')
        tk.Button(toolbar,text='처음으로',command=self.show_home,
                  font=(FONT,11),bd=0,bg=WHITE,fg=GREEN).pack(side='right')
        if notice:
            tk.Button(toolbar,text='사진 찾기 준비' if not self.app.ai.ready() else '다시 분석',
                      command=self.app.setup_ai if not self.app.ai.ready() else self.app.reindex,
                      font=(FONT,11),bd=0,bg='#F2F5EF',fg=GREEN).pack(side='right')
        if not self.rows:
            tk.Button(self.content,text='찾을 폴더',command=self.app.choose_document_locations,
                      font=(FONT,12),bg='#F2F5EF',fg=GREEN,bd=0,pady=8).pack(fill='x',pady=4)
            self.resize(self.HEIGHT+50)
        for row in shown:
            name=row['name'] if len(row['name'])<=36 else Path(row['name']).stem[:27]+'…'+Path(row['name']).suffix
            if row.get('group')=='관련 후보': name+=' · 비슷한 문서'
            file_row=tk.Frame(self.content,bg='#F3F5EE');file_row.pack(fill='x',pady=(0,7))
            copy=tk.Button(file_row,text='복사',command=lambda p=row['path']:self.copy_file(p),
                font=(FONT,11),bg='#F3F5EE',fg=GREEN,bd=0,padx=10,pady=10,cursor='hand2')
            copy.pack(side='right');self.copy_buttons.append(copy)
            pin=tk.Button(file_row,text='★' if self.app.is_pinned(row['path']) else '☆',
                command=lambda p=row['path']:self.toggle_pin(p),font=(FONT,16),bg='#F3F5EE',fg=GREEN,
                bd=0,padx=5,pady=8,cursor='hand2')
            pin.pack(side='right');self.pin_buttons.append((row['path'],pin))
            opener=tk.Button(file_row,text=name,command=lambda p=row['path']:self.app.open_file(p),
                font=(FONT,14,'bold'),bg='#F3F5EE',fg=INK,activebackground='#E4EAD8',relief='flat',
                anchor='w',justify='left',wraplength=self.WIDTH-178,padx=12,pady=10,cursor='hand2')
            opener.pack(side='left',fill='x',expand=True); self.result_buttons.append(opener)
            opener.bind('<Button-3>',lambda e,r=row:self.file_menu(e,r))
            opener.bind('<Control-c>',lambda e,p=row['path']:self.copy_file(p))
            bind_file_drag(opener,lambda p=row['path']:[p],self.transfer_status)
        if len(self.rows)>page_size:
            nav=tk.Frame(self.content,bg=WHITE); nav.pack(fill='x')
            for title,delta,enabled in [('이전',-page_size,bool(self.offset)),('다음',page_size,self.offset+page_size<len(self.rows))]:
                tk.Button(nav,text=title,command=lambda d=delta:self.page(d),state='normal' if enabled else 'disabled',
                    font=(FONT,11),bg=WHITE,fg=GREEN,bd=0,padx=12,pady=5).pack(side='left' if delta<0 else 'right')
        from accessibility import apply_fonts
        apply_fonts(self.body,self.text_scale)

    def file_menu(self,event,row):
        previous=getattr(self,'file_context_menu',None)
        if previous is not None:previous.destroy()
        menu=tk.Menu(self.win,tearoff=False,font=(FONT,12))
        self.file_context_menu=menu
        menu.add_command(label='미리보기',command=lambda:self.app.preview(row))
        menu.add_command(label='파일 복사',command=lambda:self.copy_file(row['path']))
        menu.add_command(label='고정 해제' if self.app.is_pinned(row['path']) else '고정하기',command=lambda:self.toggle_pin(row['path']))
        menu.add_command(label='내 모음에 담기',command=lambda:self.app.add_to_collection([row['path']]))
        menu.add_command(label='폴더에서 보기',command=lambda:self.app.reveal(row['path']))
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()

    def transfer_status(self,text):
        if self.alive() and self.message is not None:self.message.set(text)

    def copy_file(self,path):
        if self.app.copy_result_files([path],parent=self.win):
            self.transfer_status(COPY_MESSAGE)
        return 'break'

    def refresh_pins(self):
        for path,button in self.pin_buttons:
            if button.winfo_exists():button.configure(text='★' if self.app.is_pinned(path) else '☆')

    def toggle_pin(self,path):
        state=self.app.toggle_pinned(path,parent=self.win)
        if state is not None:
            self.transfer_status('고정했어요. 처음 화면에서 바로 열 수 있어요.' if state else '고정을 해제했어요. 원본은 그대로예요.')
        return 'break'

    def open_results(self):
        self.app.open_result_browser()

    def results(self,rows):
        if not self.alive():return
        super().results(rows)
        self.send_button.configure(text='찾기')

    def reply(self,text,sources=None):
        if not self.alive(): return
        self._showing_home=False
        self.pending=False; self.clear(); self.resize(round(220*self.text_scale))
        self.message.set(text[:120]); self.send_button.configure(state='normal',text='찾기')
