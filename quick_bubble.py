"""One search field, one short status, clickable filenames. Extras live in the pet menu."""
from pathlib import Path
import tkinter as tk
from pet_bubble import PetBubble
from app import label, WHITE, GREEN, INK, FONT
from monitors import DesktopSpace


class QuickBubble(PetBubble):
    WIDTH = 400
    HEIGHT = 185

    def __init__(self, app):
        self.app=app; self.pending=False; self.rows=[]; self.offset=0; self.loading_token=0
        self.refinement=None; self.refining=False; self.result_buttons=[]
        self.win=tk.Toplevel(app.root); self.win.withdraw(); self.win.overrideredirect(True)
        key='#010203'; self.win.configure(bg=key); self.win.attributes('-transparentcolor',key)
        self.win.attributes('-topmost',True)
        self.canvas=tk.Canvas(self.win,bg=key,highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        body=tk.Frame(self.canvas,bg=WHITE)
        self.body_item=self.canvas.create_window(22,18,window=body,anchor='nw',width=356,height=142)
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
        self.message=tk.StringVar(value='파일 이름이나 기억나는 단어를 적어 줘.')
        self.note=label(body,'',11,GREEN,textvariable=self.message,wraplength=350,justify='left')
        self.note.pack(anchor='w',pady=(10,5))
        self.content=tk.Frame(body,bg=WHITE); self.content.pack(fill='both',expand=True)
        self.resize(185)
        if not app.settings['source']:
            self.message.set('처음 한 번만, 찾을 곳을 연결해 줘.')
            tk.Button(self.content,text='내 바탕화면 연결',command=app.connect_from_bubble,
                      font=(FONT,12,'bold'),bg='#F2F5EF',fg=GREEN,bd=0,pady=8,cursor='hand2').pack(fill='x')
            self.resize(240)
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

    def resize(self,height):
        self.HEIGHT=height; self.win.geometry(f'{self.WIDTH}x{height}')
        c=self.canvas; c.delete('outline'); bottom=height-22
        c.create_polygon(22,2,378,2,398,22,398,bottom-20,378,bottom,22,bottom,2,bottom-20,2,22,
                         smooth=True,splinesteps=20,fill=WHITE,outline='#CDD3C8',width=1,tags='outline')
        c.create_polygon(182,bottom-2,200,height-2,218,bottom-2,fill=WHITE,outline='#CDD3C8',tags='outline')
        c.create_line(182,bottom-2,218,bottom-2,fill=WHITE,width=3,tags='outline')
        c.tag_lower('outline'); c.itemconfigure(self.body_item,height=height-48)

    def start_search(self):
        if not self.alive(): return
        self.pending=True; self.clear(); self.resize(185)
        self.message.set('찾고 있어…')
        self.send_button.configure(state='disabled',text='찾는 중')

    def draw_results(self):
        if not self.alive(): return
        self.clear(); self.result_buttons=[]
        self.message.set(f'{len(self.rows)}개 찾았어. 이름을 누르면 열려!' if self.rows else
                         '못 찾았어. 다른 단어로 찾아볼까?')
        if self.app.indexing: self.message.set(f'{len(self.rows)}개 찾았어. 나머지도 확인 중…')
        shown=self.rows[self.offset:self.offset+3]
        self.resize(185+len(shown)*68+(38 if len(self.rows)>3 else 0))
        for row in shown:
            name=row['name'] if len(row['name'])<=36 else Path(row['name']).stem[:27]+'…'+Path(row['name']).suffix
            if row.get('group')=='관련 후보': name+=' · 비슷한 문서'
            opener=tk.Button(self.content,text=name,command=lambda p=row['path']:self.app.open_file(p),
                font=(FONT,14,'bold'),bg='#F3F5EE',fg=INK,activebackground='#E4EAD8',relief='flat',
                anchor='w',justify='left',wraplength=315,padx=12,pady=10,cursor='hand2')
            opener.pack(fill='x',pady=(0,7)); self.result_buttons.append(opener)
            opener.bind('<Button-3>',lambda e,r=row:self.file_menu(e,r))
        if len(self.rows)>3:
            nav=tk.Frame(self.content,bg=WHITE); nav.pack(fill='x')
            for title,delta,enabled in [('이전',-3,bool(self.offset)),('다음',3,self.offset+3<len(self.rows))]:
                tk.Button(nav,text=title,command=lambda d=delta:self.page(d),state='normal' if enabled else 'disabled',
                    font=(FONT,11),bg=WHITE,fg=GREEN,bd=0,padx=12,pady=5).pack(side='left' if delta<0 else 'right')

    def file_menu(self,event,row):
        menu=tk.Menu(self.win,tearoff=False,font=(FONT,12))
        menu.add_command(label='미리보기',command=lambda:self.app.preview(row))
        menu.add_command(label='폴더에서 보기',command=lambda:self.app.reveal(row['path']))
        menu.tk_popup(event.x_root,event.y_root)

    def results(self,rows):
        super().results(rows)
        self.send_button.configure(text='찾기')

    def reply(self,text,sources=None):
        if not self.alive(): return
        self.pending=False; self.clear(); self.resize(220)
        self.message.set(text[:120]); self.send_button.configure(state='normal',text='찾기')
