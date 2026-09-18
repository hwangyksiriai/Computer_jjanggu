"""Complete local companion workflow on top of the reversible organizer."""
import bootstrap
import ctypes
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor,CancelledError
import tkinter as tk
from tkinter import filedialog,messagebox
from PIL import Image,ImageTk,ImageDraw
from app import App,Pet,Sprites,button,label,BG,WHITE,INK,MUTED,GREEN,ORANGE,PALE,FONT,BASE
from core import eligible,desktop_path,fingerprint
from knowledge import Knowledge
from local_ai import LocalAI
from monitors import fit_position
from reactions import quick_intent,REACTIONS,COMPLETIONS,ACTIVITY_LABELS
from windows_features import Hotkey,foreground_focus_reason,copy_files,get_wallpaper,set_wallpaper,launchers,startup

class LivingSprites(Sprites):
    def __init__(self,settings):
        super().__init__(); self.settings=settings; self.actions=[]; self.cache={}
        sheet=Image.open(BASE/'assets/shinchan-actions.png').convert('RGBA'); w,h=sheet.size
        for row in range(4):
            frames=[]
            for col in range(4):
                tile=sheet.crop((col*w//4,row*h//4,(col+1)*w//4,(row+1)*h//4))
                frames.append(tile)
            self.actions.append(frames)
    def render(self,outfit,size,accessory='없음',angle=0,action=None,frame=0):
        shirt=self.settings.get('shirt','기본'); hat=self.settings.get('hat','없음')
        key=(outfit,size,accessory,angle,action,frame,shirt,hat)
        if key in self.cache: return self.cache[key]
        if action is None:
            im=super().render(outfit,size,accessory,angle).copy()
        else:
            tile=self.actions[action][frame%4].copy(); tile.thumbnail((size+25,size+25),Image.Resampling.LANCZOS)
            im=Image.new('RGBA',(size+32,size+42)); im.alpha_composite(tile,((im.width-tile.width)//2,im.height-tile.height-5))
        colors={'민트':(88,181,147),'라벤더':(166,129,204),'코랄':(247,132,112)}
        if shirt in colors:
            color=colors[shirt]; pixels=im.load()
            for y in range(im.height//3,im.height*4//5):
                for x in range(im.width):
                    r,g,b,a=pixels[x,y]
                    if a and r>110 and r>g*1.6 and r>b*1.45:
                        light=max(.55,r/255); pixels[x,y]=(*(int(c*light) for c in color),a)
        d=ImageDraw.Draw(im); cx=im.width//2
        if hat=='왕관':
            d.polygon([(cx-34,31),(cx-37,9),(cx-13,22),(cx,1),(cx+14,22),(cx+37,9),(cx+34,31)],fill='#FFD45B',outline='#6B572D',width=2)
        elif hat=='우주 모자':
            d.arc((cx-size*.37,8,cx+size*.37,size*.64),180,360,fill='#90CEDF',width=8)
            d.line((cx+size*.34,30,cx+size*.39,5),fill='#5F777F',width=3); d.ellipse((cx+size*.39-5,0,cx+size*.39+5,10),fill='#EB7E76')
        elif hat=='노란 모자':
            d.ellipse((cx-32,5,cx+32,44),fill='#FAD55F',outline='#7C602B',width=2)
            d.ellipse((cx-47,29,cx+47,43),fill='#F8CF4D',outline='#7C602B',width=2)
        if len(self.cache)>180: self.cache.clear()
        self.cache[key]=im; return im

class EnhancedApp(App):
    def __init__(self,root,data=None):
        self.ai=LocalAI(); self.result_cache=[]; self.chat_history=[]; self.focus_reason=''
        self.selected=set(); self.hotkey=None; self.recording=False; self.search_pending=False; self.busy_kind=''
        self.last_reply=''; self.query_context=''; self.last_submitted=''; self.voice_busy=False; self.dnd_available=False
        self.next_walk=time.monotonic()+25; self.walk_route=None
        self.bubble=None
        self.search_fx=None
        self.desktop_room=None
        self.watcher=None; self.watch_roots=None; self.changed_paths=set(); self.changed_lock=threading.Lock()
        self.index_pool=ThreadPoolExecutor(max_workers=1); self.indexing=False; self.index_cancel=threading.Event(); self.index_again=False
        super().__init__(root,data)
        for k,v in dict(shirt='기본',hat='없음',focus_auto=True,focus_manual=False,walk=False,startup=False).items(): self.settings.setdefault(k,v)
        self.sprites=LivingSprites(self.settings)
        self.pet.action=None; self.pet.action_until=0
        self.pet.event=self.pet_event
        self.dnd_available=False
        try:
            from tkinterdnd2 import TkinterDnD,DND_FILES,COPY
            TkinterDnD.require(root); self.dnd_available=True
            self.pet.canvas.drop_target_register(DND_FILES)
            self.pet.canvas.dnd_bind('<<Drop>>',self.on_drop)
        except Exception as e: self.status.set('파일 드롭 초기화 실패: '+str(e))
        self.hotkey=Hotkey(lambda:self.events.put(self.focus_search))
        self.root.after(1200,self.desktop_tick)
        self.root.after(600,self.show_upgrade_hint)
        self.show('home')
        self.root.after(1000,self.ensure_watcher)
        if self.settings.get('desktop_room'): self.root.after(500,self.start_desktop_room)

    def start_desktop_room(self):
        from desktop_room import DesktopRoom
        if self.desktop_room: self.desktop_room.close()
        self.desktop_room=DesktopRoom(self)

    def ensure_watcher(self):
        from file_watch import FileWatch
        roots=tuple(str(p.resolve()) for p in (self.source,self.vault) if p.is_dir())
        if roots!=self.watch_roots:
            if self.watcher: self.watcher.close()
            try:
                self.watcher=FileWatch(roots,self.files_changed); self.watch_roots=roots
            except Exception as e: self.status.set('자동 변경 감지 오류: '+str(e))
        self.root.after(3000,self.ensure_watcher)

    def files_changed(self,paths):
        with self.changed_lock: self.changed_paths.update(paths)
        self.events.put(self.consume_changes)

    def take_changes(self):
        with self.changed_lock:
            paths=self.changed_paths; self.changed_paths=set(); return paths

    def consume_changes(self):
        if self.indexing: return
        paths=self.take_changes()
        if paths: self.reindex(only_paths=None if None in paths else paths)

    def make_library(self): return Knowledge(self.data,self.ai)
    def shell(self):
        super().shell()
        side=next(iter(self.nav.values())).master
        for key,title in [('tray','▧   작업 트레이'),('room','⌂   우리 방'),('collection','☆   성장과 보상'),('tools','⚙   AI · 앱 · 설정')]:
            b=button(side,title,lambda k=key:self.show(k),bg='#F0EEE6',anchor='w'); b.pack(fill='x',padx=12,pady=2); self.nav[key]=b

    def show_upgrade_hint(self):
        if self.hotkey.error: self.status.set(self.hotkey.error)
        elif not self.ai.ready(): self.status.set('로컬 AI 모델을 준비해 주세요. AI · 앱 · 설정에서 설치할 수 있어요.')

    def page_home(self):
        super().page_home()
        controls=tk.Frame(self.content,bg=BG)
        controls.pack(fill='x',before=self.results.master,pady=(0,5))
        button(controls,'말로 찾기',self.record_voice,bg='#E6EEE3').pack(side='left')
        button(controls,'결과 넓게 보기',self.open_result_browser,bg='#E6EEE3').pack(side='left')
        button(controls,'선택한 파일 트레이에 담기',self.add_selected,bg='#F0EEE6').pack(side='left',padx=6)
        self.reply_label=label(controls,self.last_reply,9,GREEN,wraplength=390,justify='left')
        self.reply_label.pack(side='left',fill='x',expand=True,padx=8)

    def render_results(self):
        if self.page!='home': return
        for child in self.results.winfo_children(): child.destroy()
        self.preview_photos=[]
        stats=self.library.stats([self.source,self.vault])
        if not self.query.get().strip():
            rows=self.library.rows([self.source,self.vault],limit=30,offset=self.search_offset)
            for r in rows: r.update(reason=r['status'],group='내 파일',evidence='',score=0)
            total=stats['total']; displayed=rows
        else:
            rows=self.result_cache; total=len(rows); displayed=rows[self.search_offset:self.search_offset+30]
        self.last_count=total; missed=stats['pending']
        mode='샘플 체험' if not self.settings['source'] else self.source.name
        self.result_count.configure(text=f'{mode} · {total}개 · AI 미분석/미지원 {missed}개')
        if not rows: label(self.results,'찾은 파일이 없어요. 다른 단서를 알려줘!\n미분석 파일은 새로 읽기를 눌러 확인할 수 있어요.',11,MUTED).pack(pady=30)
        group=None
        for r in displayed:
            if r.get('group')!=group:
                group=r.get('group'); label(self.results,group or '파일',10,GREEN,bold=True).pack(anchor='w',pady=(10,3))
            card=tk.Frame(self.results,bg=WHITE,highlightbackground='#E9E5DC',highlightthickness=1); card.pack(fill='x',pady=4)
            selected=tk.BooleanVar(value=r['path'] in self.selected)
            def select(p=r['path'],v=selected):
                if v.get(): self.selected.add(p)
                else: self.selected.discard(p)
            tk.Checkbutton(card,variable=selected,command=select,bg=WHITE,activebackground=WHITE).pack(side='left')
            thumb=r.get('thumbnail')
            if thumb and Path(thumb).exists():
                with Image.open(thumb) as im:
                    im.thumbnail((65,85)); photo=ImageTk.PhotoImage(im.copy())
                self.preview_photos.append(photo); tk.Label(card,image=photo,bg=WHITE).pack(side='left',padx=8,pady=9)
            else: label(card,Path(r['name']).suffix.upper()[1:5] or 'FILE',10,GREEN,bold=True).pack(side='left',padx=15)
            info=tk.Frame(card,bg=WHITE); info.pack(side='left',fill='both',expand=True,pady=9)
            label(info,r['name'][:40],11,bold=True,anchor='w').pack(fill='x')
            label(info,r.get('reason','')[:85],8,MUTED,anchor='w').pack(fill='x',pady=3)
            fields=r.get('fields',{}); metadata=' · '.join(str(fields[k]) for k in ('issuer','amount','issued_date') if fields.get(k))
            tags=' '.join('#'+t for t in r.get('tags',[])[:4])
            if metadata or tags: label(info,(metadata+'  '+tags)[:90],8,GREEN,anchor='w').pack(fill='x')
            evidence=r.get('evidence','')
            if evidence: label(info,evidence[:90],8,MUTED,anchor='w',wraplength=380,justify='left').pack(fill='x')
            actions=tk.Frame(card,bg=WHITE); actions.pack(side='right',padx=7)
            button(actions,'보기',lambda row=r:self.preview(row),bg='#F4F1E8').pack(side='top',pady=2)
            button(actions,'학습',lambda row=r:self.teach(row),bg=WHITE).pack(side='top')
            if self.dnd_available:
                from tkinterdnd2 import DND_FILES,COPY
                card.drag_source_register(1,DND_FILES)
                card.dnd_bind('<<DragInitCmd>>',lambda e,p=r['path']:(COPY,DND_FILES,(p,)))
        if total>30:
            nav=tk.Frame(self.results,bg=BG); nav.pack(fill='x',pady=12)
            if self.search_offset: button(nav,'이전',lambda:self.paginate(-30)).pack(side='left')
            label(nav,f'{self.search_offset+1}–{min(total,self.search_offset+30)} / {total}').pack(side='left',padx=12)
            if self.search_offset+30<total: button(nav,'다음',lambda:self.paginate(30)).pack(side='left')

    def toggle_bubble(self):
        from pet_bubble import PetBubble
        if self.bubble and self.bubble.alive():
            self.bubble.close(); return
        self.walk_route=None
        self.bubble=PetBubble(self)

    def open_result_browser(self):
        from result_browser import ResultBrowser
        ResultBrowser(self,self.result_cache if self.query.get().strip() else self.library.rows([self.source,self.vault]))

    def do_search(self,speak=True,reply_surface=None):
        if self.busy:
            self.pet_event('방금 부탁한 일을 처리 중이야. 잠깐만 기다려 줘!',1,speak=speak)
            return self.status.set('현재 작업이 끝나면 다시 알려줘!')
        query=self.query.get().strip(); self.search_offset=0
        if not query: self.result_cache=[]; self.render_results(); return
        within_paths=None
        if reply_surface is not None and getattr(reply_surface,'refining',False):
            within_paths={r['path'] for r in reply_surface.rows}
        immediate=quick_intent(query)
        activity,ack=REACTIONS[immediate or 'CHAT']
        self.search_fx={'active':True,'interactive':bool(speak or reply_surface is not None),'started':time.monotonic(),'phase':'요청을 살펴보고 있어','activity':activity,'outcome':'','until':0}
        self.walk_route=None
        if reply_surface is not None: reply_surface.start_search()
        if immediate=='QUIET': self.voice.stop(); self.change('sound',False)
        self.pet_event(ack,2 if activity=='search' else 0,speak=speak and immediate!='QUIET')
        exact_commands={'정리해줘':'CLEAN','바탕화면정리해줘':'CLEAN','꾸미기':'CLOSET','춤춰줘':'DANCE','훌라훌라':'DANCE','조용히해줘':'QUIET','작업트레이':'TRAY','우리방':'ROOM','앱목록':'LAUNCHER'}
        compact=query.replace(' ',''); context=self.query_context
        search_query=self.query_context if query==self.last_submitted else query
        search_context='' if query==self.last_submitted else context
        def phase(text,activity):
            self.events.put(lambda:self.search_fx.update(phase=text,activity=activity) if self.search_fx and self.search_fx['active'] else None)
        def work_inner():
            intent=immediate or exact_commands.get(compact)
            if not intent:
                if any(t in query for t in ('찾','인보이스','계약서','제안서','파일','그중','그 중','사진','#')): intent='SEARCH'
                elif self.ai.ready(): intent=self.ai.intent(query)
                else: intent='CHAT'
            if intent not in REACTIONS: intent='CHAT'
            if intent=='SEARCH':
                phase('파일 이름과 내용에서 단서를 찾고 있어','search')
                started=time.monotonic()
                rows,resolved=self.library.smart_search(search_query,[self.source,self.vault],search_context)
                if within_paths is not None: rows=[r for r in rows if r['path'] in within_paths]
                self.library.record_usage('search',time.monotonic()-started,len(rows))
                return intent,rows,resolved
            if intent=='CHAT':
                phase('관련 파일을 읽고 답을 생각하는 중이야','think')
                if not self.ai.ready(): return intent,'말은 잘 들었어! 자유 대화는 AI 모델 준비가 필요해. 파일 찾기, 정리, 꾸미기, 춤은 도와줄 수 있어.',[]
                nearby,_=self.library.smart_search(query,[self.source,self.vault])
                data='\n'.join(f"파일 {i+1}: {r['name']}\n{r['body'][:1200]}" for i,r in enumerate(nearby[:3]))
                response=self.ai.chat(query,data,self.chat_history)
                return intent,response,nearby[:3]
            return intent,None,None
        def work():
            try: return work_inner()
            except Exception:
                def failed():
                    self.search_fx.update(active=False,outcome='error',until=time.monotonic()+4)
                    self.pet_event('앗, 처리하다 막혔어. 오류 안내를 확인해 줘.',1,speak=speak)
                self.events.put(failed)
                raise
        def finish(result):
            intent,value,extra=result
            self.search_fx.update(active=False,outcome=('found' if value else 'empty') if intent=='SEARCH' else 'done',until=time.monotonic()+4)
            if intent=='SEARCH':
                self.result_cache=value; self.query_context=extra; self.last_submitted=query
                if reply_surface is None: self.render_results()
                self.last_reply=f'{len(value)}개 찾았어! 관련 후보도 함께 볼래?'
                if value: self.play_action(2,self.last_reply,speak=speak)
                else: self.pet_event('다른 단서로도 찾아볼까?',1,speak=speak)
                if reply_surface is not None: reply_surface.results(value)
                date_note=' 수신일은 직접 기록되거나 문서에서 확인된 값만 사용해요.' if '받은' in query else ''
                self.status.set(('AI 분석 오류 · 본문 결과만 표시: '+self.library.ai_error[:80] if self.library.ai_error else ('로컬 AI 의미·이미지 검색 완료' if self.ai.ready() else '본문 검색 완료 · AI 모델 미준비'))+date_note)
            elif intent=='CHAT':
                self.chat_history.extend([{'role':'user','content':query},{'role':'assistant','content':value}])
                self.last_reply=value
                if reply_surface is not None: reply_surface.reply(value,extra)
                else: self.show_reply(value,extra)
                self.play_action(0,value[:160],speak=speak)
            elif intent=='CLEAN':
                if reply_surface is None: self.show('organize')
                self.plan_clean()
            elif intent=='CLOSET': self.show('closet')
            elif intent=='DANCE': pass
            elif intent=='QUIET': self.change('sound',False); self.pet_event('조용히 있을게.',speak=False)
            elif intent=='TRAY': self.show('tray')
            elif intent=='ROOM': self.show('room')
            elif intent=='LAUNCHER': self.show('tools')
            if intent in COMPLETIONS:
                text,action=COMPLETIONS[intent]; self.last_reply=text
                if action is not None: self.play_action(action,text,speak=speak)
                else: self.pet_event(text,1,speak=False)
            if reply_surface is not None and intent not in ('SEARCH','CHAT'):
                reply_surface.reply(self.last_reply)
            if self.page=='home' and hasattr(self,'reply_label'): self.reply_label.configure(text=self.last_reply[:150])
        self.run_job(work,finish,'로컬 AI가 요청을 이해하는 중…')

    def show_reply(self,text,sources):
        w=tk.Toplevel(self.root); w.title('짱구의 답장'); w.geometry('620x450'); w.configure(bg=BG)
        area=tk.Text(w,font=(FONT,12),wrap='word',bg=WHITE,relief='flat',padx=22,pady=20); area.pack(fill='both',expand=True,padx=18,pady=18)
        area.insert('1.0',text); area.configure(state='disabled')
        for r in sources: button(w,'참고 파일 · '+r['name'][:40],lambda p=r['path']:self.open_file(p),bg=BG).pack(anchor='w',padx=18)
        label(w,'로컬 AI 답변은 틀릴 수 있어요. 중요한 내용은 원본을 확인해 주세요.',9,MUTED).pack(pady=12)

    def preview(self,row):
        w=tk.Toplevel(self.root); w.title(row['name']); w.geometry('780x720'); w.configure(bg=BG)
        label(w,row['name'],16,bold=True,wraplength=720).pack(anchor='w',padx=22,pady=(18,6))
        label(w,row.get('reason',row['status']),9,GREEN,wraplength=720).pack(anchor='w',padx=22)
        content=tk.Frame(w,bg=BG); content.pack(fill='both',expand=True,padx=20,pady=12)
        if row.get('thumbnail') and Path(row['thumbnail']).exists():
            pic=tk.Label(content,bg=WHITE); pic.pack(side='left',fill='y',padx=(0,15)); images=[]
            page_number=[0]
            def show_page():
                if Path(row['path']).suffix.lower()=='.pdf':
                    import pymupdf
                    with pymupdf.open(row['path']) as doc:
                        p=doc[page_number[0]]; pix=p.get_pixmap(matrix=pymupdf.Matrix(.7,.7),alpha=False)
                        im=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
                else: im=Image.open(row['thumbnail']).convert('RGB')
                im.thumbnail((320,450)); photo=ImageTk.PhotoImage(im); pic.configure(image=photo); pic.image=photo
            show_page()
            if Path(row['path']).suffix.lower()=='.pdf':
                controls=tk.Frame(w,bg=BG); controls.pack(fill='x',padx=20)
                def turn(delta):
                    import pymupdf
                    with pymupdf.open(row['path']) as doc: page_number[0]=max(0,min(doc.page_count-1,page_number[0]+delta))
                    show_page(); page_label.configure(text=f'{page_number[0]+1}쪽')
                button(controls,'이전 쪽',lambda:turn(-1)).pack(side='left'); page_label=label(controls,'1쪽'); page_label.pack(side='left',padx=15)
                button(controls,'다음 쪽',lambda:turn(1)).pack(side='left')
        text=tk.Text(content,font=(FONT,10),wrap='word',bg=WHITE,fg=INK,relief='flat',padx=12,pady=12)
        text.pack(fill='both',expand=True); text.insert('1.0',row['body'] or '추출된 본문이 없어요. 원본을 확인해 주세요.')
        terms=re.findall(r'[\w가-힣]{2,}',self.query.get())
        for term in terms:
            start='1.0'
            while True:
                pos=text.search(term,start,stopindex='end',nocase=True)
                if not pos: break
                end=f'{pos}+{len(term)}c'; text.tag_add('hit',pos,end); start=end
        text.tag_configure('hit',background='#FFE7A4'); text.configure(state='disabled')
        actions=tk.Frame(w,bg=BG); actions.pack(fill='x',padx=20,pady=10)
        for title,fn in [('열기',lambda:self.open_file(row['path'])),('폴더',lambda:self.reveal(row['path'])),
                         ('담기',lambda:self.library.tray_add([row['path']])),('비슷한 파일',lambda:self.similar(row)),('분류 알려주기',lambda:self.teach(row))]:
            button(actions,title,fn,bg=WHITE).pack(side='left',padx=3)
        feedback=tk.Frame(w,bg=BG); feedback.pack(fill='x',padx=20,pady=(0,14))
        button(feedback,'맞아, 이 파일!',lambda:self.mark_found(row,True),bg='#E6EFDE').pack(side='left',padx=3)
        button(feedback,'찾던 파일이 아니야',lambda:self.mark_found(row,False),bg=WHITE).pack(side='left')

    def mark_found(self,row,accepted):
        self.library.feedback(row['path'],self.query.get(),accepted)
        self.pet_event('찾아서 다행이야!' if accepted else '알려줘서 고마워. 분류도 고쳐줄래?',0)
        if not accepted: self.teach(row)
    def similar(self,row):
        if self.busy: return
        def done(result):
            self.result_cache=result[0]; self.query.set('비슷한 파일: '+row['name']); self.show('home')
        self.run_job(lambda:self.library.smart_search('',[self.source,self.vault],similar=row['path']),done,'내용과 이미지 특징이 비슷한 파일을 찾는 중…')

    def teach(self,row):
        w=tk.Toplevel(self.root); w.title('내 기준 알려주기'); w.geometry('550x660'); w.configure(bg=BG)
        label(w,'이 파일은 이렇게 기억해 줘.',19,bold=True).pack(anchor='w',padx=24,pady=(20,6))
        label(w,row['name'],10,MUTED,wraplength=480).pack(anchor='w',padx=24,pady=(0,14))
        values={}
        fields=row.get('fields',{})
        specs=[('category','문서 분류',row['category']),('tags','태그 · 쉼표로 구분',', '.join(row.get('tags',[]))),
               ('issuer','거래처',fields.get('issuer','')),('issued_date','발행일 · YYYY-MM-DD',fields.get('issued_date','')),
               ('received_date','실제 받은 날짜 · 모르면 비워두기',fields.get('received_date','')),
               ('keyword','이 단어가 있는 파일에도 적용 · 선택',''),('note','내가 기억할 메모',row.get('note','') or '')]
        for key,title,value in specs:
            label(w,title,9,GREEN,bold=True).pack(anchor='w',padx=24,pady=(7,3)); var=tk.StringVar(value=value); values[key]=var
            tk.Entry(w,textvariable=var,font=(FONT,11),relief='flat',bg=WHITE).pack(fill='x',padx=24,ipady=6)
        def save():
            try:
                self.library.annotate(row['path'],values['category'].get(),values['tags'].get().split(','),
                    fields={k:values[k].get() for k in ('issuer','issued_date','received_date')},keyword=values['keyword'].get(),note=values['note'].get())
            except Exception as e: return messagebox.showerror('기준 확인',str(e),parent=w)
            w.destroy(); self.pet_event('기억했어! 다음에도 이 기준을 쓸게.',0); self.reindex()
        button(w,'기억해 줘',save,primary=True).pack(pady=18)

    def add_selected(self):
        if not self.selected: return self.status.set('파일 왼쪽 체크박스를 먼저 선택해 주세요.')
        self.library.tray_add(self.selected); self.status.set(f'{len(self.selected)}개를 작업 트레이에 담았어요.'); self.selected.clear(); self.show('tray')
    def page_tray(self):
        self.heading('READY FOR YOUR NEXT TASK','필요한 것만 모아두자.','여러 파일을 함께 복사하거나 다른 앱으로 끌어다 놓을 수 있어.')
        paths=self.library.tray_files(); bar=tk.Frame(self.content,bg=BG); bar.pack(fill='x',pady=(0,12))
        def copy():
            try: copy_files(paths); self.status.set('파일 목록을 복사했어요. 탐색기에서 붙여넣을 수 있어요.')
            except Exception as e: messagebox.showerror('복사',str(e))
        button(bar,'모두 복사',copy,primary=True).pack(side='left',padx=(0,8))
        drag=button(bar,'여기를 잡고 모두 끌어가기',lambda:None,bg='#E9EFD9'); drag.pack(side='left')
        if self.dnd_available:
            from tkinterdnd2 import DND_FILES,COPY
            drag.drag_source_register(1,DND_FILES); drag.dnd_bind('<<DragInitCmd>>',lambda e:(COPY,DND_FILES,tuple(paths)))
        area=self.scroll(self.content)
        if not paths: label(area,'검색 결과에서 파일을 선택해 담아줘!',12,MUTED).pack(pady=50)
        for path in paths:
            line=tk.Frame(area,bg=WHITE); line.pack(fill='x',pady=4)
            label(line,Path(path).name,11).pack(side='left',padx=15)
            button(line,'열기',lambda p=path:self.open_file(p)).pack(side='right')
            button(line,'빼기',lambda p=path:(self.library.tray_remove(p),self.show('tray'))).pack(side='right')

    def on_drop(self,event):
        from tkinterdnd2 import COPY
        paths=[Path(p).resolve() for p in self.root.tk.splitlist(event.data)]
        self.play_action(0,'받았어! 어디에 넣을지 확인해 줘.',speak=False)
        self.root.after(100,lambda:self.review_drop(paths)); return COPY
    def review_drop(self,paths):
        if self.busy: return self.status.set('현재 작업이 끝난 뒤 다시 맡겨줘!')
        valid=[p for p in paths if eligible(p,p.parent) and not p.is_relative_to(BASE) and not p.is_relative_to(self.vault.resolve())]
        if not valid: return messagebox.showinfo('파일 맡기기','일반 파일을 맡겨 주세요. 앱 내부·보관함 파일·바로가기·폴더는 이동하지 않아요.')
        w=tk.Toplevel(self.root); w.title('맡길 파일 확인'); w.geometry('610x440'); w.configure(bg=BG)
        label(w,f'{len(valid)}개 파일을 주머니에 넣을까?',18,bold=True).pack(pady=20)
        text=tk.Text(w,wrap='word',font=(FONT,10),bg=WHITE,relief='flat'); text.pack(fill='both',expand=True,padx=20)
        text.insert('1.0','\n'.join(str(p) for p in valid)+'\n\n보관 위치: '+str(self.vault)); text.configure(state='disabled')
        def execute():
            w.destroy()
            def work():
                done=[]; errors=[]
                for p in valid:
                    stat=p.stat(); item={'source':str(p),'dest':str(self.vault/'맡긴 파일'/p.name),'mtime':stat.st_mtime,'size':stat.st_size}
                    moved,err=self.library.move([item],p.parent,self.vault); done+=moved; errors+=err
                return done,errors
            self.run_job(work,self.cleaned,'맡긴 파일을 보관하는 중…')
        button(w,'확인한 파일 맡기기',execute,primary=True).pack(pady=18)

    def page_collection(self):
        data=self.library.progress(); self.heading('LITTLE MOMENTS, BIG FRIENDSHIP',f"우리의 친밀도 · Lv. {data['level']}",'정리하고, 찾고, 알려줄수록 함께한 기억이 쌓여요. 쉬어도 줄어들지 않아요.')
        label(self.content,f"{data['points']} 포인트  ·  다음 레벨까지 {50-data['points']%50}",15,GREEN,bold=True).pack(anchor='w',pady=(0,15))
        items=[('별 배지',0),('왕관',30),('반짝 축하',60),('우주 모자',100)]
        for name,needed in items:
            line=tk.Frame(self.content,bg=WHITE); line.pack(fill='x',pady=4)
            label(line,('✓  ' if data['points']>=needed else '○  ')+name,12,bold=True).pack(side='left',padx=18,pady=14)
            if name in ('왕관','우주 모자') and data['points']>=needed:
                button(line,'착용',lambda n=name:self.change('hat',n),bg=PALE).pack(side='right',padx=10)
            else: label(line,'해금 완료' if data['points']>=needed else f'{needed} 포인트',10,MUTED).pack(side='right',padx=18)
        label(self.content,'함께한 순간',12,bold=True).pack(anchor='w',pady=(20,8)); area=self.scroll(self.content)
        for event in data['events'][:40]: label(area,f"+{event['points']}   {event['label']}   ·   {datetime.fromtimestamp(event['created']).strftime('%m.%d')}",10,MUTED).pack(anchor='w',pady=3)

    def page_room(self):
        self.heading('WELCOME TO OUR ROOM','우리 방에 온 걸 환영해!','책상은 검색, 장난감 상자는 트레이, 서랍은 옷장, 침대는 집중 모드야.')
        canvas=tk.Canvas(self.content,bg=PALE,highlightthickness=0); canvas.pack(fill='both',expand=True)
        im=Image.open(BASE/'assets/shinchan-room-anime-v2.png').convert('RGB')
        self.room_regions=[]
        def draw(event=None):
            self.room_regions=[]
            cw,ch=canvas.winfo_width(),canvas.winfo_height(); ratio=min(cw/im.width,ch/im.height)
            size=(max(1,int(im.width*ratio)),max(1,int(im.height*ratio))); photo=ImageTk.PhotoImage(im.resize(size,Image.Resampling.LANCZOS))
            canvas.delete('all'); canvas.image=photo; ox=(cw-size[0])//2; oy=(ch-size[1])//2; canvas.create_image(ox,oy,image=photo,anchor='nw')
            regions=[(.04,.43,.31,.77,'침대 · 집중',lambda:self.change('focus_manual',not self.settings['focus_manual'])),
                     (.32,.30,.55,.59,'책상 · 파일 찾기',lambda:self.show('home')),
                     (.59,.34,.73,.58,'장난감 · 트레이',lambda:self.show('tray')),
                     (.72,.25,.84,.57,'서랍 · 꾸미기',lambda:self.show('closet'))]
            for x1,y1,x2,y2,title,fn in regions:
                tag=title; canvas.create_rectangle(ox+x1*size[0],oy+y1*size[1],ox+x2*size[0],oy+y2*size[1],outline='',tags=tag)
                canvas.create_text(ox+(x1+x2)/2*size[0],oy+y2*size[1]+12,text=title,fill=GREEN,font=(FONT,10,'bold'),tags=tag)
                self.room_regions.append((ox+x1*size[0],oy+y1*size[1],ox+x2*size[0],oy+y2*size[1]+24,fn))
        def click_room(event):
            for x1,y1,x2,y2,fn in self.room_regions:
                if x1<=event.x<=x2 and y1<=event.y<=y2:
                    fn(); break
        canvas.bind('<Button-1>',click_room)
        self.room_canvas=canvas
        canvas.bind('<Configure>',draw)
        bar=tk.Frame(self.content,bg=BG); bar.pack(fill='x',pady=15)
        button(bar,'이 방을 Windows 배경으로',self.apply_room,primary=True).pack(side='left',padx=(0,10))
        button(bar,'이전 배경으로 복원',self.restore_room).pack(side='left')
        button(bar,'바탕화면 가구 클릭 켜기/끄기',lambda:self.change('desktop_room',not self.settings.get('desktop_room',False))).pack(side='left',padx=8)
        button(bar,'짱구랑 놀기',lambda:self.play_action(2,'같이 놀자! 훌라훌라!')).pack(side='right')
    def apply_room(self):
        try:
            if not self.settings.get('wallpaper_previous'): self.settings['wallpaper_previous']=get_wallpaper()
            set_wallpaper(BASE/'assets/shinchan-room-anime-v2.png'); self.save(); self.status.set('우리 방을 바탕화면 배경으로 설정했어요.')
        except Exception as e: messagebox.showerror('배경 설정',str(e))
    def restore_room(self):
        old=self.settings.get('wallpaper_previous')
        if not old: return self.status.set('저장된 이전 배경이 없어요.')
        try: set_wallpaper(old); self.status.set('이전 배경을 복원했어요.')
        except Exception as e: messagebox.showerror('배경 복원',str(e))

    def page_closet(self):
        super().page_closet(); opts=self.closet_options
        label(opts,'03  나만의 색과 모자',12,bold=True).pack(anchor='w',pady=(15,8))
        self.option(opts,'shirt',['기본','민트','라벤더','코랄']).pack(fill='x',pady=4)
        hats=['없음','노란 모자']+[h for h in self.library.progress()['unlocked'] if h in ('왕관','우주 모자')]
        self.option(opts,'hat',hats).pack(fill='x',pady=4)
        self.check(opts,'walk','모니터 사이 자동 산책').pack(anchor='w',pady=5)
        label(opts,'보상 모자는 성장과 보상에서 해금해요.',9,MUTED).pack(anchor='w')

    def page_tools(self):
        self.heading('LOCAL, PERSONAL, ALWAYS NEAR','AI와 생활 설정','파일 내용은 밖으로 보내지 않아요. Ctrl+Shift+Space로 언제든 불러줘.')
        area=self.scroll(self.content)
        label(area,'로컬 AI  ·  '+('준비 완료' if self.ai.ready() else '설치 필요'),13,GREEN,bold=True).pack(anchor='w',pady=(0,8))
        label(area,'의미 검색 · 이미지 특징 · 자연어 대화 · 음성 인식\n모델 초기 다운로드 이후에는 오프라인으로 실행합니다.',10,MUTED,justify='left').pack(anchor='w')
        button(area,'AI 모델 설치 / 준비 확인',self.setup_ai,primary=True).pack(anchor='w',pady=10)
        button(area,'마이크 선택',self.choose_microphone).pack(anchor='w',pady=5)
        button(area,'원본 짱구 음성 연결',self.configure_voice_clips).pack(anchor='w',pady=5)
        label(area,self.settings.get('audio_device') or '음성 입력: Windows 기본 마이크',9,MUTED).pack(anchor='w')
        self.check(area,'focus_auto','전체화면 / 회의 창에서 자동 집중').pack(anchor='w')
        self.check(area,'focus_manual','지금 집중 모드').pack(anchor='w')
        self.check(area,'startup','Windows 로그인 시 짱구와 함께 시작').pack(anchor='w')
        label(area,'자동 집중은 전체화면 크기와 회의 앱 창 제목을 기준으로 감지해요.',9,MUTED).pack(anchor='w',pady=(4,12))
        stats=self.library.usage_summary()
        label(area,f"이 PC의 검색 기록 · {stats['searches']}회 · 평균 {stats['mean_seconds']:.1f}초 · 맞았다는 피드백 {stats['accepted']}/{stats['feedback']}",9,GREEN).pack(anchor='w',pady=(0,10))
        label(area,'내가 알려준 정리 규칙',12,bold=True).pack(anchor='w',pady=8)
        rules=self.library.rules()
        if not rules: label(area,'파일 미리보기의 “분류 알려주기”에서 기준을 만들 수 있어요.',9,MUTED).pack(anchor='w')
        for rule in rules:
            line=tk.Frame(area,bg=WHITE); line.pack(fill='x',pady=3)
            label(line,rule['keyword']+' → '+rule['category'],10).pack(side='left',padx=12)
            button(line,'규칙 해제',lambda i=rule['id']:(self.library.remove_rule(i),self.show('tools'))).pack(side='right')
        label(area,'자주 쓰는 곳과 앱',12,bold=True).pack(anchor='w',pady=(20,8))
        bar=tk.Frame(area,bg=BG); bar.pack(fill='x')
        for title,path in [('휴지통','shell:RecycleBinFolder'),('바탕화면',str(desktop_path())),('다운로드',str(Path.home()/'Downloads')),('보관함',str(self.vault))]:
            button(bar,title,lambda p=path:self.open_file(p)).pack(side='left',padx=(0,5))
        query=tk.StringVar(); entry=tk.Entry(area,textvariable=query,font=(FONT,11),relief='flat'); entry.pack(fill='x',pady=12,ipady=8)
        apps=tk.Frame(area,bg=BG); apps.pack(fill='x')
        shortcuts=launchers(desktop_path())
        def render(*args):
            for child in apps.winfo_children(): child.destroy()
            found=[r for r in shortcuts if query.get().lower() in r[0].lower()]
            for title,path in found[:60]: button(apps,title,lambda p=path:self.open_file(p),bg=WHITE,anchor='w').pack(fill='x',pady=2)
        query.trace_add('write',render); render()

    def setup_ai(self):
        if self.busy: return
        def work():
            from setup_runtime import install_models
            return install_models()
        def done(result):
            self.status.set('로컬 AI 준비 완료! 새로 읽기로 파일을 분석할게요.'); self.reindex()
        self.run_job(work,done,'AI 모델을 준비하는 중… 처음에는 다운로드가 필요해요.')

    def configure_voice_clips(self):
        from voice_clips import EVENTS,make_clip
        import uuid
        w=tk.Toplevel(self.root); w.title('원본 음성 연결'); w.geometry('620x440'); w.configure(bg=BG)
        label(w,'녹음된 목소리를 그대로 재생해요',17,bold=True).pack(pady=18)
        label(w,'인사·춤·정리 완료·발견 반응에 사용해요.\n새 문장을 합성하지 않으며, 긴 대화는 기존 음성으로 말해요.',10,MUTED,justify='left').pack()
        source=tk.StringVar(value=str(BASE/'.local/voice-reference/reference.wav') if (BASE/'.local/voice-reference/reference.wav').exists() else '')
        tk.Entry(w,textvariable=source).pack(fill='x',padx=22,pady=10)
        def choose():
            path=filedialog.askopenfilename(parent=w,title='음성 또는 영상 선택',filetypes=[('음성·영상','*.wav *.mp3 *.mp4 *.m4a'),('모든 파일','*.*')])
            if path: source.set(path)
        button(w,'파일 선택',choose).pack()
        row=tk.Frame(w,bg=BG); row.pack(pady=12)
        start=tk.StringVar(value='0'); end=tk.StringVar(value='4'); selected=tk.StringVar(value='greeting')
        for title,var in [('시작(초)',start),('끝(초)',end)]:
            label(row,title).pack(side='left',padx=6); tk.Entry(row,textvariable=var,width=7).pack(side='left')
        menu=tk.OptionMenu(row,selected,*EVENTS); menu.pack(side='left',padx=10)
        label(w,'greeting 인사 / dance 춤 / clean 정리 완료 / found 발견',9,MUTED).pack()
        feedback=tk.StringVar(value='구간을 먼저 들어본 뒤 반응에 연결해 주세요.')
        label(w,'',10,textvariable=feedback,wraplength=570).pack(pady=12)
        def prepare(assign):
            if self.busy: feedback.set('현재 작업이 끝나면 다시 눌러 주세요.'); return
            src,begin,finish,event=source.get(),start.get(),end.get(),selected.get()
            path=self.data/'voice-clips'/(uuid.uuid4().hex+'.wav')
            def done(clip):
                if assign:
                    mapping=self.settings.setdefault('voice_clips',{}); mapping[event]=clip; self.save()
                    feedback.set(EVENTS[event]+'에 원본 음성을 연결했어요.')
                self.voice.play_clip(clip,self.settings,force=True)
            self.run_job(lambda:make_clip(src,begin,finish,path),done,'음성 구간을 준비하는 중…')
        bar=tk.Frame(w,bg=BG); bar.pack()
        button(bar,'구간 들어보기',lambda:prepare(False)).pack(side='left',padx=5)
        button(bar,'이 반응에 연결',lambda:prepare(True),primary=True).pack(side='left',padx=5)
        def remove():
            self.settings.setdefault('voice_clips',{}).pop(selected.get(),None); self.save(); self.voice.stop(); feedback.set('기본 음성으로 복원했어요.')
        button(bar,'연결 해제',remove).pack(side='left',padx=5)

    def choose_microphone(self):
        from audio_input import input_devices,input_config
        try: devices=input_devices()
        except Exception as e: return messagebox.showerror('마이크 확인',str(e))
        w=tk.Toplevel(self.root); w.title('마이크 선택'); w.geometry('640x300')
        label(w,'실제 목소리를 받을 마이크를 선택해 주세요.',12).pack(pady=12)
        def choose(name):
            try: _,rate=input_config(name)
            except Exception as e: return messagebox.showerror('마이크 확인',str(e),parent=w)
            self.settings['audio_device']=name; self.save(); w.destroy()
            self.status.set(f'마이크 선택 완료 · {rate} Hz → 로컬 16000 Hz 변환')
            if self.page=='tools': self.show('tools')
        button(w,'Windows 기본 마이크',lambda:choose('')).pack(fill='x',padx=12,pady=4)
        for name,_ in devices: button(w,name,lambda n=name:choose(n)).pack(fill='x',padx=12,pady=4)
        if not devices: label(w,'연결된 입력 장치가 없어요.',11).pack(pady=12)

    def record_voice(self):
        if self.busy or self.recording: return self.status.set('현재 작업이 끝난 뒤 말해 주세요.')
        if not self.ai.ready(): return self.status.set('음성 입력을 쓰려면 AI 모델을 먼저 준비해 주세요.')
        from audio_input import input_config,to_16khz
        try: device,rate=input_config(self.settings.get('audio_device',''))
        except Exception as e: return messagebox.showinfo('마이크 선택 필요',str(e))
        self.recording=True; self.voice.stop()
        w=tk.Toplevel(self.root); w.title('말로 찾기'); w.geometry('360x150'); w.configure(bg=PALE)
        label(w,'6초 동안 듣고 있어요…',16,bold=True).pack(pady=25)
        label(w,'마이크는 이 버튼을 눌렀을 때만 사용해요.',9,MUTED).pack()
        cancelled=threading.Event()
        def cancel():
            cancelled.set()
            try:
                import sounddevice as sd
                sd.stop()
            except Exception: pass
            self.recording=False
            if w.winfo_exists(): w.destroy()
            self.status.set('음성 입력을 취소했어요.')
        w.protocol('WM_DELETE_WINDOW',cancel)
        button(w,'녹음 취소',cancel,bg=WHITE).pack(pady=5)
        def work():
            import sounddevice as sd
            audio=sd.rec(6*rate,samplerate=rate,device=device,channels=1,dtype='float32'); sd.wait()
            if cancelled.is_set(): return None
            return self.ai.call('transcribe',audio=to_16khz(audio[:,0],rate).tolist())
        def done(text):
            self.recording=False
            if w.winfo_exists(): w.destroy()
            if cancelled.is_set() or not text: return
            text=text.replace('인 보이스','인보이스')
            self.query.set(text); self.show('home'); self.do_search(True)
        def safe_work():
            try: return work()
            except Exception:
                self.events.put(lambda:(setattr(self,'recording',False),w.destroy() if w.winfo_exists() else None))
                raise
        self.run_job(safe_work,done,'마이크로 6초 녹음 후 PC에서 음성을 해석해요…')

    def interaction_active(self):
        fx=self.search_fx
        return bool(fx and fx.get('interactive') and (fx['active'] or time.monotonic()<fx.get('until',0)))

    def pet_event(self,text,pose=None,speak=True,explicit=False):
        token=object(); self.voice_reply_token=token
        explicit=explicit or self.interaction_active()
        self.pet.message=text; self.pet.pose=pose; self.pet.until=time.time()+5
        if pose==3: self.pet.action=2; self.pet.action_until=time.time()+4
        elif '꺼내' in text or '돌려' in text: self.pet.action=3; self.pet.action_until=time.time()+4
        if speak and (explicit or (not self.focus_reason and not self.settings.get('focus_manual'))):
            def say_when_ready():
                if self.voice_reply_token is not token or not self.settings['sound']: return
                if not explicit and (self.focus_reason or self.settings.get('focus_manual')): return
                process=self.voice.process
                if process and process.poll() is None and self.search_fx and not self.search_fx['active']:
                    self.root.after(100,say_when_ready); return
                self.voice.say(text,self.settings)
            say_when_ready()
    def play_action(self,action,text,speak=True):
        self.pet.action=action; self.pet.action_until=time.time()+4
        self.pet_event(text,3 if action==2 else 0,speak)
    def run_job(self,fn,finish,text):
        if self.busy: return super().run_job(fn,finish,text)
        action=None
        if hasattr(self,'pet'):
            if '옮기' in text or '보관' in text: action=1; self.play_action(1,'파일을 챙기는 중이야.',False)
            elif '돌려' in text: action=3; self.play_action(3,'원래 자리로 가져다줄게.',False)
            if action is not None: self.pet.action_until=time.time()+3600
        def completed(value):
            if action is not None: self.pet.action_until=0
            finish(value)
        return super().run_job(fn,completed,text)
    def cleaned(self,result):
        super().cleaned(result); self.play_action(2,f'{len(result[0])}개 정리 끝! 훌라훌라!',False)
    def change(self,key,value):
        if key=='desktop_room':
            if value: self.start_desktop_room(); self.hide()
            elif self.desktop_room: self.desktop_room.close(); self.desktop_room=None
        if key=='startup':
            try: startup(value,BASE/'app.py')
            except Exception as e: return messagebox.showerror('시작 설정',str(e))
        if key=='hat' and value in ('왕관','우주 모자') and value not in self.library.progress()['unlocked']:
            return self.status.set('아직 해금하지 않은 모자예요.')
        super().change(key,value)
        if key=='focus_manual' and value: self.voice.stop()

    def desktop_tick(self):
        try:
            reason='집중 모드' if self.settings.get('focus_manual') else ''
            if not reason and self.settings.get('focus_auto'):
                reason=foreground_focus_reason([self.pet.space.handle(),self.root.winfo_id()])
            if reason and not self.focus_reason:
                self.voice.stop(); self.pet.win.attributes('-topmost',False)
            elif not reason and self.focus_reason:
                self.pet.win.attributes('-topmost',self.settings['topmost'])
            self.focus_reason=reason
            if self.settings.get('walk') and not reason and not self.pet.dragging and not self.busy and not (self.bubble and self.bubble.alive()) and time.monotonic()>=self.next_walk:
                screens=self.pet.space.screens(); x,y=self.pet.space.position()
                current=next((i for i,s in enumerate(screens) if s[0]<=x<s[2] and s[1]<=y<s[3]),0)
                target=screens[(current+1)%len(screens)]
                tx,ty=fit_position(target[2]-self.pet.width-45,target[3]-self.pet.height-65,self.pet.width,self.pet.height,[target])
                self.walk_route=(time.monotonic(),x,y,tx,ty)
                self.pet.action=1; self.pet.action_until=time.time()+5; self.next_walk=time.monotonic()+30
        finally: self.root.after(2000,self.desktop_tick)

    def reindex(self,only_paths=None):
        if self.indexing:
            self.index_again=True; return
        self.indexing=True; self.index_cancel.clear()
        src,vault=self.source,self.vault
        def done(count):
            self.status.set(f'{count}개 파일 분석 완료'+(' · '+self.library.ai_error[:90] if self.library.ai_error else ''))
            if self.query.get().strip() and self.query.get().strip()==self.last_submitted and self.page=='home' and not self.busy: self.do_search(speak=False)
            elif self.page=='home': self.render_results()
        def progress(count,name):
            if self.index_cancel.is_set(): raise CancelledError()
            changes=self.take_changes()
            if changes:
                if None in changes: self.index_again=True
                self.library.index([src,vault],only_paths={p for p in changes if p})
            if count%10==0: self.events.put(lambda n=count:self.status.set(f'문서 분석 중 · {n}개 처리 · 처음에는 시간이 걸릴 수 있어요.'))
        self.status.set('백그라운드에서 파일 분석 중 · 먼저 분석된 파일부터 검색할 수 있어요.')
        future=self.index_pool.submit(lambda:self.library.index([src,vault],progress,only_paths=only_paths))
        def finished(f):
            def apply():
                self.indexing=False
                try: done(f.result())
                except CancelledError: pass
                except Exception as e: self.status.set('파일 분석 오류: '+str(e))
                if self.index_again:
                    self.index_again=False; self.reindex()
                else: self.consume_changes()
            self.events.put(apply)
        future.add_done_callback(finished)

    def auto_tick(self):
        if self.settings['auto'] and not self.busy:
            src,vault=self.source,self.vault
            def work(): return self.library.move(self.library.plan(src,vault),src,vault)
            def finish(result):
                if result[0] or result[1]: self.cleaned(result)
            self.run_job(work,finish,'새 파일을 안전하게 보관하는 중…')
        self.root.after(30000,self.auto_tick)

    def quit(self):
        if self.busy: return super().quit()
        if self.watcher: self.watcher.close()
        if self.desktop_room: self.desktop_room.close()
        self.index_cancel.set(); self.index_pool.shutdown(wait=False,cancel_futures=True)
        if self.hotkey: self.hotkey.close()
        self.ai.close(); super().quit()

def living_tick(self):
    self.frame+=1; app=self.app; s=app.settings
    focus=bool(getattr(app,'focus_reason','') or s.get('focus_manual')) and not app.interaction_active()
    route=getattr(app,'walk_route',None)
    if route:
        if self.dragging or focus or not s.get('walk'): app.walk_route=None
        else:
            started,x,y,tx,ty=route; t=min(1,(time.monotonic()-started)/5); t=t*t*(3-2*t)
            self.space.move(x+(tx-x)*t,y+(ty-y)*t)
            if t>=1: app.walk_route=None; self.save_position()
    active=time.time()<self.until and not focus
    fx=getattr(app,'search_fx',None); searching=bool(fx and fx['active'])
    moving=searching and s['animate'] and not focus
    outcome=fx.get('outcome','') if fx and time.monotonic()<fx.get('until',0) else ''
    outfit=1 if focus else (self.pose if active and self.pose is not None and s['auto_outfit'] else s['outfit'])
    if searching and s['auto_outfit'] and not focus: outfit=2
    action=getattr(self,'action',None) if time.time()<getattr(self,'action_until',0) and not focus and s['animate'] else None
    activity=fx.get('activity','search') if searching else ''
    inspecting=activity=='search' and (self.frame//24)%2==1
    if searching: action=({'search':None if inspecting else 3,'clean':1,'carry':3,'dance':2,'dress':0,'room':0,'launch':0}.get(activity)) if moving else None
    if isinstance(app.sprites,LivingSprites):
        frame=(self.frame if moving else self.frame//2)%4
        im=app.sprites.render(outfit,s['size'],s['accessory'],action=action,frame=frame)
    else: im=app.sprites.render(outfit,s['size'],s['accessory'])
    if moving: im=im.rotate(round(math.sin(self.frame*.28)*5),resample=Image.Resampling.BICUBIC)
    self.photo=ImageTk.PhotoImage(im); c=self.canvas; c.delete('all')
    bounce=math.sin(self.frame*.15)*2 if s['animate'] and not focus else 0
    c.create_oval(self.width/2-48,self.height-17,self.width/2+48,self.height-6,fill='#C6C4B5',outline='')
    sway=math.sin(self.frame*.52)*(6 if inspecting else 25) if moving and activity!='think' else 0
    hop=abs(math.sin(self.frame*.85))*(2 if inspecting else 10) if moving and activity!='think' else 0
    if moving and activity!='think':
        for i in range(3):
            tx=self.width/2-62-i*15; ty=self.height-42-i*10
            c.create_line(tx,ty,tx-12,ty,fill='#D6AA71',width=2,tags='search-fx')
    c.create_image(self.width/2+sway,self.height-20+bounce-hop,image=self.photo,anchor='s')
    if searching and activity=='think':
        for i in range(3):
            r=4+i*2; x=self.width*.75+i*14; y=90-i*18
            c.create_oval(x-r,y-r,x+r,y+r,fill=WHITE,outline=GREEN,tags='search-fx')
        c.create_text(self.width/2,25,text='음… 생각하는 중!',font=(FONT,13,'bold'),fill=GREEN,tags='search-fx')
    elif searching and activity in ('search','clean','carry'):
        for i in range(3):
            px=22+i*23; py=65+(math.sin(self.frame*.2+i)*5 if moving else 0)
            c.create_rectangle(px,py,px+17,py+24,fill='#FFF9EB',outline='#A8977B',tags='search-fx')
            c.create_line(px+4,py+7,px+13,py+7,fill='#A8977B',tags='search-fx')
        if activity=='search':
            # The prop is attached to the rendered body's hand, including its hop and sway.
            hx=self.width/2+sway+im.width*.19
            hy=self.height-20+bounce-hop-im.height*.40
            mx=hx+17+(math.sin(self.frame*.24)*9 if moving else 0); my=hy-26
            c.create_line(hx-12,hy+2,hx,hy,fill='#EAB28F',width=7,tags='search-fx')
            c.create_line(hx,hy,mx-7,my+11,fill='#493E37',width=6,tags=('search-fx','held-magnifier'))
            c.create_oval(mx-18,my-18,mx+18,my+18,fill='#DFF4F5',outline='#493E37',width=3,tags=('search-fx','held-magnifier'))
            c.create_arc(mx-12,my-12,mx+12,my+12,start=50,extent=70,style='arc',outline=WHITE,width=3,tags='search-fx')
            c.create_oval(hx-5,hy-4,hx+5,hy+4,fill='#F6CBA7',outline='#A77B61',tags='search-fx')
        c.create_text(self.width/2,25,text=ACTIVITY_LABELS[activity],font=(FONT,13,'bold'),fill=GREEN,tags='search-fx')
    elif searching:
        c.create_text(self.width/2,25,text=ACTIVITY_LABELS.get(activity,'요청을 확인하는 중!'),font=(FONT,12,'bold'),fill=GREEN,tags='search-fx')
        c.create_text(self.width*.8,82,text={'dress':'☆','dance':'♪','room':'⌂','launch':'▦','quiet':'쉿'}.get(activity,'?'),font=(FONT,24,'bold'),fill=ORANGE,tags='search-fx')
    elif outcome in ('found','empty','error'):
        c.create_text(self.width/2,30,text={'found':'✦ 찾았다! ✦','empty':'음… 다른 단서가 있을까?','error':'잠깐, 다시 확인해 볼게!'}[outcome],font=(FONT,11,'bold'),fill=ORANGE if outcome=='found' else GREEN,tags='search-outcome')
    if active and not searching and not outcome and not (getattr(app,'bubble',None) and app.bubble.alive()):
        c.create_rectangle(9,4,self.width-9,48,fill='#FFF7E7',outline='#E4C89D')
        c.create_text(self.width/2,26,text=self.message[:90],font=(FONT,9),fill=INK,width=self.width-30)
    if action==2 and hasattr(app.library,'progress') and app.library.progress()['points']>=60:
        for i in range(5):
            c.create_text(35+i*45,65+(self.frame*6+i*19)%130,text='✦',fill=['#EDB449','#EBA0AB','#8FC8A0'][i%3],font=(FONT,15))
    self.win.after(120 if focus else 100,lambda:living_tick(self))

Pet.tick=living_tick
