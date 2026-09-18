"""Compact desktop conversation, anchored to the pet on any monitor."""
import random
import time,math
import tkinter as tk
from app import label,FONT,INK,MUTED,GREEN,WHITE,PALE,ORANGE
from monitors import DesktopSpace,fit_position
from reactions import ACTIVITY_LABELS
from result_refinement import ResultRefinement
from pathlib import Path
from datetime import datetime

def button(parent,text,command,primary=False,bg=None,**kwargs):
    kwargs.setdefault('padx',7)
    return tk.Button(parent,text=text,command=command,font=(FONT,9),bg=ORANGE if primary else (bg or WHITE),
                     fg=WHITE if primary else INK,relief='flat',bd=0,pady=5,cursor='hand2',**kwargs)

class PetBubble:
    WIDTH=390
    HEIGHT=250
    def __init__(self,app):
        self.app=app; self.pending=False; self.rows=[]; self.offset=0; self.loading_token=0
        self.refinement=None; self.refining=False; self.result_buttons=[]
        self.win=tk.Toplevel(app.root); self.win.withdraw(); self.win.overrideredirect(True)
        key='#010203'; self.win.configure(bg=key); self.win.attributes('-transparentcolor',key)
        self.win.attributes('-topmost',True); self.win.geometry(f'{self.WIDTH}x{self.HEIGHT}')
        canvas=tk.Canvas(self.win,bg=key,highlightthickness=0); canvas.pack(fill='both',expand=True)
        self.canvas=canvas
        body=tk.Frame(canvas,bg=WHITE); self.body_item=canvas.create_window(28,26,window=body,anchor='nw',width=334,height=self.HEIGHT-76)
        self.resize(self.HEIGHT)
        head=tk.Frame(body,bg=WHITE); head.pack(fill='x')
        greeting=random.choice(['뭘 찾아줄까?','오늘은 무슨 일을 도와줄까?','뭐 찾고 있어? 내가 도와줄게!'])
        if app.settings.get('voice_mode')=='qwen_local':greeting='오늘은 어떤 일을 도와줄까?'
        label(head,greeting,12,INK,bold=True,bg=WHITE).pack(side='left')
        button(head,'×',self.close,bg=WHITE).pack(side='right')
        self.text=tk.StringVar(); entryrow=tk.Frame(body,bg=WHITE,highlightbackground='#303030',highlightthickness=1); entryrow.pack(fill='x',pady=(10,6))
        self.entry=tk.Entry(entryrow,textvariable=self.text,font=(FONT,11),relief='flat',bg=WHITE,fg=INK)
        self.entry.pack(side='left',fill='x',expand=True,padx=8,ipady=9)
        self.send_button=button(entryrow,'보내기',self.submit,primary=True); self.send_button.pack(side='right')
        self.entry.bind('<Return>',lambda e:self.submit()); self.win.bind('<Escape>',lambda e:self.close())
        quick=tk.Frame(body,bg=WHITE); quick.pack(fill='x')
        for text in ('인보이스 찾아줘','춤춰줘','정리해줘'):
            button(quick,text,lambda q=text:self.submit(q),bg='#F2F2F2').pack(side='left',padx=(0,4))
        self.message=tk.StringVar(value='찾고 싶은 파일이나 하고 싶은 일을 말해 줘.')
        label(body,'',11,GREEN,bold=True,textvariable=self.message,wraplength=328,justify='left',bg=WHITE).pack(anchor='w',pady=7)
        self.content=tk.Frame(body,bg=WHITE); self.content.pack(fill='both',expand=True)
        bottom=tk.Frame(body,bg=WHITE); bottom.pack(fill='x')
        button(bottom,'주머니 크게 열기',self.open_main,bg=WHITE).pack(side='right')
        button(bottom,'결과 넓게 보기',self.open_results,bg=PALE).pack(side='left')
        self.win.update_idletasks(); self.space=DesktopSpace(self.win)
        self.win.deiconify(); self.win.attributes('-alpha',0.25); self.follow()
        self.pop(0); self.win.after(180,self.entry.focus_force)
        app.pet_event(greeting,speak=True,explicit=True)
        if getattr(app,'search_fx',None) and app.search_fx['active']: self.start_search()
        elif getattr(app,'bubble_refinement',None):
            self.refinement=app.bubble_refinement; self.rows=self.refinement.rows
            self.resize(650); self.draw_results()

    def resize(self,height):
        self.HEIGHT=height; self.win.geometry(f'{self.WIDTH}x{height}')
        c=self.canvas; c.delete('outline'); bottom=height-30
        c.create_polygon(70,3,320,3,387,38,387,bottom-40,340,bottom,50,bottom,3,bottom-40,3,38,
                         smooth=True,splinesteps=24,fill=WHITE,outline='#292929',width=2,tags='outline')
        c.create_polygon(215,bottom-4,238,height-3,268,bottom-5,fill=WHITE,outline='#292929',width=2,tags='outline')
        c.create_line(215,bottom-4,268,bottom-5,fill=WHITE,width=4,tags='outline')
        c.tag_lower('outline'); c.itemconfigure(self.body_item,height=height-76)

    def alive(self):
        try: return bool(self.win.winfo_exists())
        except tk.TclError: return False
    def close(self):
        if self.alive(): self.win.destroy()
    def pop(self,step):
        if not self.alive(): return
        self.win.attributes('-alpha',min(1,.25+step*.15))
        if step<5: self.win.after(25,lambda:self.pop(step+1))
    def follow(self):
        if not self.alive(): return
        pet=self.app.pet; x,y=pet.space.position(); screens=pet.space.screens()
        center=(x+pet.width/2,y+pet.height/2)
        screen=next((s for s in screens if s[0]<=center[0]<s[2] and s[1]<=center[1]<s[3]),screens[0])
        # Keep the tail above the pet, even when it starts near the top edge.
        available=screen[3]-screen[1]-pet.height+50
        if self.HEIGHT>available: self.resize(max(250,int(available)))
        if y-self.HEIGHT+55<screen[1]:
            y=screen[1]+self.HEIGHT-55
            pet.space.move(x,y)
        bx=x+pet.width/2-self.WIDTH/2; by=y-self.HEIGHT+55
        self.space.move(*fit_position(bx,by,self.WIDTH,self.HEIGHT,[screen]))
        if self.pending and not self.app.busy:
            self.pending=False
            self.send_button.configure(state='normal',text='보내기')
            if self.app.status.get().startswith('작업 실패'):
                self.clear(); self.message.set(self.app.status.get())
            elif getattr(self.app,'search_fx',None):
                if self.app.search_fx['outcome'] in ('found','empty'): self.results(self.app.result_cache)
                else: self.reply(self.app.last_reply or '요청을 처리했어!')
        self.win.after(150,self.follow)
    def open_main(self):
        if self.refinement:
            self.app.result_cache=list(self.rows); self.app.search_offset=0
        self.close(); self.app.show('home'); self.app.render_results()
    def open_results(self):
        from result_browser import ResultBrowser
        ResultBrowser(self.app,self.rows)
    def submit(self,query=None):
        if not self.alive(): return
        query=(query if query is not None else self.text.get()).strip()
        if not query: return
        if self.app.busy:
            self.app.pet_event('지금 부탁한 일을 처리 중이야. 잠깐만 기다려 줘!',1,speak=True)
            self.message.set('지금 요청을 처리하고 있어. 잠깐만 기다려 줘!'); return
        self.refining=self.refinement is not None and any(t in query.replace(' ','') for t in ('그중','거기서','이중','결과중'))
        self.message.set('접수했어! 바로 도와줄게.'); self.pending=True
        self.app.query.set(query); self.app.do_search(speak=True,reply_surface=self)
        self.text.set('')
    def clear(self):
        for child in self.content.winfo_children(): child.destroy()
    def start_search(self):
        if not self.alive(): return
        self.pending=True; self.clear(); self.resize(400)
        self.send_button.configure(state='disabled',text='처리 중')
        label(self.content,'✓ 보낸 요청  '+self.app.query.get()[:60],11,INK,bold=True,bg='#FFF0DF',wraplength=310,justify='left',padx=9,pady=8).pack(fill='x',pady=(0,8))
        self.loading=tk.Canvas(self.content,height=96,bg=WHITE,highlightthickness=0)
        self.loading.pack(fill='x'); self.loading_token+=1; self.animate_search(self.loading_token)
    def animate_search(self,token):
        if token!=self.loading_token: return
        if not self.alive() or not self.pending or not self.loading.winfo_exists(): return
        fx=getattr(self.app,'search_fx',None)
        if not fx or not fx['active']: return
        elapsed=int(time.monotonic()-fx['started'])
        self.message.set(f"{fx['phase']}…  {elapsed}초"+('\n처음 분석이나 AI 준비 때문에 더 걸릴 수 있어.' if elapsed>=12 else ''))
        c=self.loading; c.delete('all'); t=time.monotonic()-fx['started']
        motion=self.app.settings['animate'] and (self.app.interaction_active() or (not self.app.focus_reason and not self.app.settings.get('focus_manual')))
        for i in range(5):
            x=33+i*54; y=12+(math.sin(t*4+i)*4 if motion else 0)
            c.create_rectangle(x,y,x+28,y+35,fill='#FFF3D7',outline='#C0AB83')
            for j in range(3): c.create_line(x+6,y+9+j*6,x+21,y+9+j*6,fill='#C0AB83')
        x=165+math.sin(t*2)*110 if motion else 165
        c.create_line(x+11,36,x+23,49,fill=INK,width=5)
        c.create_oval(x-14,11,x+14,39,outline=INK,width=3)
        activity=fx.get('activity','search')
        if activity!='search':
            c.delete('all')
            symbol={'think':'?','clean':'▤','carry':'▤','dress':'☆','dance':'♪','quiet':'쉿','room':'⌂','launch':'▦'}.get(activity,'…')
            for i in range(3): c.create_text(95+i*70,27+(math.sin(t*3+i)*6 if motion else 0),text=symbol,fill=ORANGE,font=(FONT,24,'bold'))
        title=ACTIVITY_LABELS.get(activity,'요청을 확인하고 있어!')
        c.create_text(165,68,text=title,fill=GREEN,font=(FONT,12,'bold'))
        if elapsed>=12: c.create_text(165,89,text='아직 처리 중이야. 기다려 줘서 고마워!',fill=MUTED,font=(FONT,9))
        self.win.after(100,lambda:self.animate_search(token))
    def results(self,rows):
        if not self.alive(): return
        if self.refining and self.refinement:
            self.refinement.narrow(self.app.query.get(),rows)
        else: self.refinement=ResultRefinement(rows,self.app.query.get())
        self.app.bubble_refinement=self.refinement
        self.refining=False
        self.pending=False; self.rows=self.refinement.rows; self.offset=0; self.resize(650)
        self.send_button.configure(state='normal',text='보내기')
        self.draw_results()
    def refine(self,kind,value=None):
        if self.pending or not self.refinement: return
        if kind=='back': self.refinement.back()
        elif kind=='reset': self.refinement.reset()
        else: self.refinement.facet(kind,value)
        self.rows=self.refinement.rows; self.offset=0; self.draw_results()
        self.app.pet_event(f'{len(self.rows)}개로 좁혔어! 미리보기로 확인해 봐.' if self.rows else '이 조건은 없네. 한 단계 뒤로 돌아가 볼까?',2 if self.rows else 1,speak=True)
    def draw_results(self):
        if not self.alive(): return
        self.clear(); self.result_buttons=[]
        model=self.refinement
        self.message.set(f'검색 결과 {len(self.rows)}개'+(f' · 전체 결과 {len(model.original)}개에서 좁힘' if model.steps else ' · 추가 조건 없음'))
        label(self.content,'처음 검색: '+model.query[:32],9,MUTED,bg=WHITE).pack(anchor='w')
        coverage=getattr(self.app.library,'search_coverage',{})
        if coverage:
            label(self.content,f"등록 {coverage['registered']} · 본문 {coverage['text']} · AI {coverage['semantic']}개"+(' · 분석 중' if self.app.indexing else ''),8,MUTED,bg=WHITE).pack(anchor='w')
        trail=' → '.join(s[0] for s in model.steps) or '전체 결과'
        label(self.content,trail,9,GREEN,bg=WHITE,wraplength=325,justify='left').pack(anchor='w')
        controls=tk.Frame(self.content,bg=WHITE); controls.pack(fill='x',pady=3)
        for title,kind,value in [('PDF','pdf',None),('달러','currency','USD'),('원화','currency','KRW'),('7일 내 수정','recent',None)]:
            probe=ResultRefinement(self.rows); probe.facet(kind,value)
            button(controls,f'{title} {len(probe.rows)}',lambda k=kind,v=value:self.refine(k,v),bg='#F2F2F2',padx=3).pack(side='left',padx=1)
        field=tk.Frame(self.content,bg=WHITE); field.pack(fill='x',pady=3)
        self.within=tk.StringVar()
        self.within_entry=tk.Entry(field,textvariable=self.within,font=(FONT,10),width=20)
        self.within_entry.pack(side='left',fill='x',expand=True,ipady=4)
        self.within_entry.bind('<Return>',lambda e:self.refine('text',self.within.get()))
        button(field,'결과 안에서 찾기',lambda:self.refine('text',self.within.get()),bg=PALE).pack(side='right')
        label(self.content,'거래처·금액·단어 입력 / 여러 단어는 모두 포함',8,MUTED,bg=WHITE).pack(anchor='w')
        tools=tk.Frame(self.content,bg=WHITE); tools.pack(fill='x')
        button(tools,'↶ 한 단계 뒤로',lambda:self.refine('back'),state='normal' if model.steps else 'disabled').pack(side='left')
        button(tools,'전체 결과',lambda:self.refine('reset')).pack(side='left')
        button(tools,'갱신',lambda:self.submit(model.query)).pack(side='left')
        button(tools,'최신순',self.sort_recent).pack(side='right')
        # Scrollable cards keep controls reachable on small displays and long names.
        pane=tk.Frame(self.content,bg=WHITE); pane.pack(fill='both',expand=True)
        view=tk.Canvas(pane,bg=WHITE,highlightthickness=0,height=170)
        scroll=tk.Scrollbar(pane,command=view.yview); scroll.pack(side='right',fill='y')
        view.configure(yscrollcommand=scroll.set); view.pack(side='left',fill='both',expand=True)
        cards=tk.Frame(view,bg=WHITE); item=view.create_window(0,0,window=cards,anchor='nw')
        cards.bind('<Configure>',lambda e:view.configure(scrollregion=view.bbox('all')))
        view.bind('<Configure>',lambda e:view.itemconfigure(item,width=e.width))
        if not self.rows: label(cards,'맞는 파일이 없어. 위에서 조건을 되돌려 봐.',10,MUTED,bg=WHITE,wraplength=295).pack(pady=12)
        for row in self.rows[self.offset:self.offset+3]:
            line=tk.Frame(cards,bg='#FFF8EB',padx=7,pady=5); line.pack(fill='x',pady=3)
            label(line,row['name'],10,INK,bold=True,bg='#FFF8EB',wraplength=288,justify='left').pack(anchor='w')
            fields=row.get('fields') or {}
            details=' · '.join(str(v) for v in [Path(row['path']).suffix.upper().lstrip('.'),fields.get('issuer'),fields.get('amount'),','.join(fields.get('currencies',[]))] if v)
            label(line,details[:90],9,GREEN,bg='#FFF8EB',wraplength=288,justify='left').pack(anchor='w')
            label(line,(row.get('group','관련 파일')+' · '+str(Path(row['path']).parent))[-85:],8,MUTED,bg='#FFF8EB',wraplength=288,justify='left').pack(anchor='w')
            evidence=row.get('evidence') or row.get('reason') or '내용 미확인 · 미리보기로 확인해 줘'
            label(line,evidence[:100],9,MUTED,bg='#FFF8EB',wraplength=288,justify='left').pack(anchor='w')
            actions=tk.Frame(line,bg='#FFF8EB'); actions.pack(fill='x')
            label(actions,datetime.fromtimestamp(row.get('mtime',0)).strftime('수정 %Y.%m.%d'),8,MUTED,bg='#FFF8EB').pack(side='left')
            button(actions,'미리보기',lambda r=row:self.app.preview(r),bg=PALE).pack(side='right')
            opener=button(actions,'열기',lambda p=row['path']:self.app.open_file(p)); opener.pack(side='right'); self.result_buttons.append(opener)
        nav=tk.Frame(self.content,bg=WHITE); nav.pack(fill='x')
        button(nav,'이전',lambda:self.page(-3),state='normal' if self.offset else 'disabled').pack(side='left')
        label(nav,f'{self.offset//3+1} / {max(1,(len(self.rows)+2)//3)} 페이지',9,MUTED,bg=WHITE).pack(side='left',padx=30)
        button(nav,'다음',lambda:self.page(3),state='normal' if self.offset+3<len(self.rows) else 'disabled').pack(side='right')
    def sort_recent(self):
        self.rows=sorted(self.rows,key=lambda r:r.get('mtime',0),reverse=True); self.offset=0; self.draw_results()
    def page(self,delta): self.offset+=delta; self.draw_results()
    def reply(self,text,sources=None):
        if not self.alive(): return
        self.pending=False; self.clear(); self.resize(350); self.message.set('짱구의 답장')
        self.send_button.configure(state='normal',text='보내기')
        area=tk.Text(self.content,font=(FONT,10),wrap='word',bg=WHITE,relief='flat',height=5)
        bar=tk.Scrollbar(self.content,command=area.yview); bar.pack(side='right',fill='y')
        area.configure(yscrollcommand=bar.set); area.pack(fill='both',expand=True)
        area.insert('1.0',text+(' 가끔 틀릴 수 있으니 중요한 내용은 원본도 확인해 줘.' if sources else '')); area.configure(state='disabled')
