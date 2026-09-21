from __future__ import annotations
import bootstrap
import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import queue
import random
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime
from PIL import Image, ImageTk, ImageDraw
from core import BASE, Library, desktop_path, set_desktop_icons, IMAGE_EXTS
from monitors import DesktopSpace, enable_dpi_awareness, fit_position, virtual_bounds

BG = '#FAF8F3'; WHITE = '#FFFFFF'; INK = '#30332E'; MUTED = '#83867E'
ORANGE = '#EF7754'; PALE = '#FFF0DF'; GREEN = '#526F59'; LINE = '#E8E7DF'
FONT = '맑은 고딕'
OUTFITS = ['평소의 짱구', '졸린 짱구', '탐정 짱구', '훌라 짱구']
DEFAULTS = dict(outfit=0, accessory='없음', backdrop='크림', size=180,
                animate=True, topmost=True, auto_outfit=True, sound=True,
                voice='Microsoft Heami Desktop', rate=1, volume=70, auto=False,
                source='', vault='', name='짱구', pet_x=None, pet_y=None)

def label(parent, text, size=10, color=INK, bg=None, bold=False, **kw):
    return tk.Label(parent, text=text, font=(FONT, size, 'bold' if bold else 'normal'),
                    fg=color, bg=bg or parent.cget('bg'), **kw)

def button(parent, text, command, primary=False, bg=None, **kw):
    return tk.Button(parent, text=text, command=command, font=(FONT, 10, 'bold'),
                     bg=ORANGE if primary else (bg or WHITE), fg=WHITE if primary else INK,
                     activebackground='#DB6747' if primary else PALE,
                     activeforeground=WHITE if primary else INK,
                     relief='flat', bd=0, padx=16, pady=10, cursor='hand2', **kw)

class Voice:
    def __init__(self, error_callback):
        self.process = None
        self.lock = threading.Lock()
        self.error_callback = error_callback
        self.speech_token=object(); self.qwen_client=None

    def say(self, text, settings, force=False):
        if not settings['sound'] and not force:
            return
        if settings.get('voice_mode')=='qwen_local':
            from qwen_voice import QwenVoiceClient,speech_chunks
            self.stop(); token=self.speech_token
            if self.qwen_client is None:self.qwen_client=QwenVoiceClient()
            reference=settings.get('voice_reference','')
            captured=dict(settings)
            def generate_and_play():
                try:
                    for part in speech_chunks(text):
                        if self.speech_token is not token:return
                        self.error_callback('짱구 답변 음성 준비 중 · 처음 생성하는 문장은 시간이 걸릴 수 있어요.')
                        path=self.qwen_client.generate(part,reference)
                        if self.speech_token is not token:return
                        self.error_callback('짱구 답변 음성을 재생합니다.')
                        self.play_clip(path,captured,force,preserve_request=True)
                        process=self.process
                        if process:process.wait()
                except Exception as e:
                    if self.speech_token is token:self.error_callback('짱구 음성 생성 실패: '+str(e))
            threading.Thread(target=generate_and_play,daemon=True).start()
            return
        from voice_clips import event_for
        clip=settings.get('voice_clips',{}).get(event_for(text))
        if settings.get('voice_mode')=='original_only':
            if clip and Path(clip).is_file(): return self.play_clip(clip,settings,force)
            self.stop()
            self.error_callback('이 답변에 맞는 짱구 음성이 아직 없어요. 답변은 말풍선에서 확인해 주세요.')
            return
        if clip and Path(clip).is_file():
            return self.play_clip(clip,settings,force)
        self.stop()
        payload = json.dumps(dict(text=text, voice=settings['voice'], rate=settings['rate'],
                                  volume=settings['volume']), ensure_ascii=False).encode('utf-8')
        try:
            with self.lock:
                p = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive',
                                      '-ExecutionPolicy', 'Bypass', '-File', str(BASE/'speech.ps1')],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=0x08000000)
                self.process = p
            def wait():
                _, err = p.communicate(payload)
                if p.returncode and self.process is p:
                    self.error_callback('음성을 재생하지 못했어요. 설치된 음성을 선택해 주세요.')
            threading.Thread(target=wait, daemon=True).start()
        except OSError:
            self.error_callback('Windows 음성 엔진을 실행하지 못했어요.')

    def play_clip(self,path,settings,force=False,preserve_request=False):
        if not settings['sound'] and not force: return
        import sys
        self.stop(cancel_speech=not preserve_request)
        payload=json.dumps({'path':str(path),'volume':settings.get('volume',70)},ensure_ascii=False).encode('utf-8')
        try:
            with self.lock:
                command=[sys.executable,'--play-voice'] if getattr(sys,'frozen',False) else [sys.executable,str(BASE/'voice_clips.py')]
                p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=0x08000000)
                self.process=p
            def wait():
                _,err=p.communicate(payload)
                if p.returncode and self.process is p: self.error_callback('등록한 음성을 재생하지 못했어요.')
            threading.Thread(target=wait,daemon=True).start()
        except OSError: self.error_callback('음성 재생기를 열지 못했어요.')

    def stop(self,cancel_speech=True):
        if cancel_speech:self.speech_token=object()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
            self.process = None

    def close(self):
        self.stop()
        if self.qwen_client:
            process=self.qwen_client.process
            if process and process.poll() is None:process.terminate()

class Sprites:
    def __init__(self):
        sheet = Image.open(BASE/'assets'/'shinchan-sheet.png').convert('RGBA')
        w,h = sheet.size
        self.items = []
        for i in range(4):
            tile = sheet.crop(((i%2)*w//2, (i//2)*h//2, (i%2+1)*w//2, (i//2+1)*h//2))
            box = tile.getchannel('A').getbbox()
            self.items.append(tile.crop(box) if box else tile)

    @lru_cache(maxsize=96)
    def render(self, outfit, size, accessory='없음', angle=0):
        source = self.items[outfit].copy()
        source.thumbnail((size, size), Image.Resampling.LANCZOS)
        # Accessories are an independent UI layer; generated art stays intact.
        target = Image.new('RGBA', (size+32,size+42))
        x,y = (target.width-source.width)//2, target.height-source.height-10
        target.alpha_composite(source, (x,y))
        draw = ImageDraw.Draw(target)
        if accessory == '별 핀':
            cx,cy = x+source.width*.75, y+source.height*.16
            points = [(cx+math.sin(i*math.pi/5)*(13 if i%2==0 else 6),
                       cy-math.cos(i*math.pi/5)*(13 if i%2==0 else 6)) for i in range(10)]
            draw.polygon(points, fill='#FFD958', outline='#9B732C')
        elif accessory == '하트':
            cx,cy = x+source.width*.85, y+source.height*.12
            draw.ellipse((cx-12,cy-7,cx+1,cy+7), fill='#F48F99')
            draw.ellipse((cx-1,cy-7,cx+12,cy+7), fill='#F48F99')
            draw.polygon([(cx-12,cy+1),(cx+12,cy+1),(cx,cy+18)], fill='#F48F99')
        elif accessory == '동그란 안경':
            cy = y+source.height*(.275 if outfit == 0 else .30)
            cx = x+source.width*.52; radius = source.width*.1
            for offset in (-radius*1.2, radius*1.2):
                draw.ellipse((cx+offset-radius,cy-radius,cx+offset+radius,cy+radius), outline='#3B3835', width=3)
            draw.line((cx-radius*.2,cy,cx+radius*.2,cy), fill='#3B3835', width=3)
        return target.rotate(angle, resample=Image.Resampling.BICUBIC)

class Pet:
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.key = '#010203'
        self.win.configure(bg=self.key)
        self.win.wm_attributes('-transparentcolor', self.key)
        self.canvas = tk.Canvas(self.win, bg=self.key, highlightthickness=0, cursor='hand2')
        self.canvas.pack(fill='both', expand=True)
        self.frame = 0; self.pose = None; self.until = 0; self.message = '나를 누르면 바로 파일을 찾아줄게!'
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<Button-3>', self.menu)
        self.space = DesktopSpace(self.win)
        self.dragging = False
        self.screens = self.space.screens()
        self.refresh()
        self.win.update_idletasks()
        x = app.settings.get('pet_x'); y = app.settings.get('pet_y')
        x = x if x is not None else self.win.winfo_screenwidth()-300
        y = y if y is not None else self.win.winfo_screenheight()-360
        self.space.move(*fit_position(x,y,self.width,self.height,self.screens))
        self.win.after(2000,self.check_displays)
        self.tick()

    def refresh(self):
        s = self.app.settings
        self.win.attributes('-topmost', s['topmost'])
        self.width = max(270, s['size']+50); self.height = s['size']+114
        self.win.geometry(f'{self.width}x{self.height}')
        self.win.after_idle(self.keep_visible)

    def save_position(self):
        x,y=self.space.position()
        self.app.settings.update(pet_x=x,pet_y=y)
        self.app.save()

    def keep_visible(self):
        if self.dragging: return
        x,y=self.space.position()
        fitted=fit_position(x,y,self.width,self.height,self.screens)
        if fitted!=(x,y): self.space.move(*fitted)

    def check_displays(self):
        screens=self.space.screens()
        if screens!=self.screens and not self.dragging:
            self.screens=screens
            self.keep_visible()
            self.save_position()
        self.win.after(2000,self.check_displays)

    def next_monitor(self):
        self.screens=self.space.screens()
        x,y=self.space.position()
        nearest=fit_position(x,y,self.width,self.height,self.screens)
        current=next((i for i,s in enumerate(self.screens)
                      if s[0]<=nearest[0]<s[2] and s[1]<=nearest[1]<s[3]),0)
        target=self.screens[(current+1)%len(self.screens)]
        x=target[2]-self.width-30; y=target[3]-self.height-60
        self.space.move(*fit_position(x,y,self.width,self.height,[target]))
        self.save_position()
        self.event('여기로 왔어!',speak=False)

    def event(self, text, pose=None, speak=True):
        self.message = text; self.pose = pose; self.until = time.time()+4
        if speak: self.app.voice.say(text, self.app.settings)

    def tick(self):
        self.frame += 1
        s = self.app.settings
        active = time.time() < self.until
        outfit = self.pose if active and self.pose is not None and s['auto_outfit'] else s['outfit']
        angle = round(math.sin(self.frame*.65)*9) if active and outfit==3 and s['animate'] else 0
        bounce = math.sin(self.frame*.17)*3 if s['animate'] else 0
        self.photo = ImageTk.PhotoImage(self.app.sprites.render(outfit, s['size'], s['accessory'], angle))
        c = self.canvas; c.delete('all')
        c.create_oval(self.width/2-49,self.height-18,self.width/2+49,self.height-6, fill='#C6C4B5',outline='')
        c.create_image(self.width/2,self.height-20+bounce, image=self.photo, anchor='s')
        if active or self.frame < 55:
            c.create_rectangle(9,4,self.width-9,47,fill='#FFF7E7',outline='#E4C89D',width=1)
            c.create_text(self.width/2,25,text=self.message,font=(FONT,9),fill=INK,width=self.width-30)
        self.win.after(100,self.tick)

    def press(self,e):
        self.screens=self.space.screens()
        self.start=(*self.space.cursor(),*self.space.position())
        self.moved=False; self.dragging=True
    def drag(self,e):
        cx,cy=self.space.cursor()
        dx,dy = cx-self.start[0], cy-self.start[1]
        if abs(dx)+abs(dy)>5: self.moved=True
        left,top,right,bottom=virtual_bounds(self.screens)
        x=max(left,min(right-self.width,self.start[2]+dx))
        y=max(top,min(bottom-self.height,self.start[3]+dy))
        self.space.move(x,y)
    def release(self,e):
        self.dragging=False
        if self.moved:
            self.keep_visible(); self.save_position()
        else:
            if hasattr(self.app,'toggle_bubble'): self.app.toggle_bubble()
            else: self.app.show('home')
    def menu(self,e):
        m = tk.Menu(self.win,tearoff=0,font=(FONT,10))
        for name,cmd in [('파일 찾기',lambda:self.app.focus_search()),('정리·설정',lambda:self.app.show('organize')),('내 짱구 꾸미기',lambda:self.app.show('closet')),
                         ('다음 모니터로 이동',self.next_monitor),
                         ('훌라훌라!',lambda:self.event('훌라훌라! 오늘도 신나게!',3)),
                         ('말소리 켜기 / 끄기',lambda:self.app.toggle_sound()),
                         ('바탕화면 아이콘 복원',lambda:self.app.icons(True)),('완전히 종료',self.app.quit)]:
            m.add_command(label=name,command=cmd)
        if hasattr(self.app,'voice_settings'): m.add_command(label='목소리 설정',command=self.app.voice_settings)
        if hasattr(self.app,'connect_from_bubble'): m.add_command(label='찾을 폴더 연결',command=self.app.connect_from_bubble)
        m.tk_popup(e.x_root,e.y_root)

class App:
    def __init__(self, root, data=None):
        self.root=root; self.data=Path(data or BASE/'.local'); self.data.mkdir(parents=True,exist_ok=True)
        self.config_path=self.data/'settings.json'; self.settings=DEFAULTS.copy()
        try: self.settings.update(json.loads(self.config_path.read_text('utf-8')))
        except (OSError,ValueError): pass
        self.library=self.make_library(); self.library.recover()
        self.events=queue.Queue(); self.pool=ThreadPoolExecutor(max_workers=1); self.busy=False
        self.sprites=Sprites(); self.voice=Voice(lambda s:self.events.put(lambda:self.status.set(s)))
        self.previous=''; self.last_query=None; self.resolved_query=''; self.page='home'; self.filter='전체'; self.query=tk.StringVar(); self.status=tk.StringVar(value='내 파일을 기억하는 작은 친구')
        self.icon_changed=False; self.preview_photos=[]; self.search_offset=0
        self.demo=BASE/'demo_files'; self.demo.mkdir(exist_ok=True)
        self.make_demo()
        self.root.title('짱구의 주머니 · 바탕화면 친구')
        self.root.geometry('1100x780'); self.root.minsize(980,700); self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW',self.hide)
        self.root.bind('<Control-f>',lambda e:self.focus_search())
        self.root.bind('<Escape>',lambda e:self.hide())
        self.shell()
        self.pet=Pet(self)
        self.show('home')
        self.root.after(100,self.poll)
        self.root.after(300,self.reindex)
        self.root.after(30000,self.auto_tick)
        self.root.after(1000,self.load_voices)

    @property
    def source(self): return Path(self.settings['source']) if self.settings['source'] else self.demo
    def make_library(self): return Library(self.data)
    @property
    def vault(self): return Path(self.settings['vault']) if self.settings['vault'] else self.data/'보관함'

    def make_demo(self):
        marker=self.data/'demo-created'
        if marker.exists(): return
        samples={
          '거래처A_9월.txt':'INVOICE\nInvoice No: 2026-0918\nBill to: Studio Pocket\nSubtotal: USD 1,200\nAmount due: USD 1,320\nPayment due: 2026-10-01\nDesign service for September.',
          '새 문서.txt':'청구서\n공급자: 초록 스튜디오\n공급가액: 500,000원\n청구금액: 550,000원\n지급기한: 2026년 10월 10일',
          '브랜드 리뉴얼 제안서.txt':'제안서\n브랜드 리뉴얼 프로젝트\n제안 배경: 사용자에게 친근한 브랜드 경험을 만듭니다.\n일정: 9월 리서치, 10월 디자인',
          '협업_최종.txt':'업무 위탁 계약서\n계약 기간: 2026년 9월부터 12월까지\n디자인 업무에 관한 계약입니다.',
          '커피_결제.txt':'영수증\nPOCKET COFFEE\n결제 완료\n아메리카노 4,500원',
          '아이디어 메모.txt':'바탕화면에는 짱구만!\n내 짱구 꾸미기: 잠옷, 탐정, 훌라\n정리가 끝나면 함께 춤추기.'}
        for name,body in samples.items():
            p=self.demo/name
            if not p.exists():
                p.write_text(body,encoding='utf-8'); os.utime(p,(time.time()-120,time.time()-120))
        marker.touch()

    def save(self):
        tmp=self.config_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.settings,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(self.config_path)

    def shell(self):
        side=tk.Frame(self.root,bg='#F0EEE6',width=205); side.pack(side='left',fill='y'); side.pack_propagate(False)
        label(side,'SHINCHAN’S',10,GREEN,bold=True).pack(anchor='w',padx=25,pady=(32,2))
        label(side,'작은 주머니',23,bold=True).pack(anchor='w',padx=23)
        label(side,'비워두면, 더 즐거워져.',9,MUTED).pack(anchor='w',padx=25,pady=(8,38))
        self.nav={}
        for key,text in [('home','⌕   내 파일 찾기'),('organize','▤   바탕화면 정리'),('closet','♡   내 짱구 꾸미기'),('history','↶   정리 기록')]:
            b=button(side,text,lambda k=key:self.show(k),bg='#F0EEE6',anchor='w'); b.pack(fill='x',padx=12,pady=4); self.nav[key]=b
        foot=tk.Frame(side,bg=side.cget('bg')); foot.pack(side='bottom',fill='x',padx=20,pady=22)
        label(foot,'●  기기 안에서만 처리해요',9,GREEN).pack(anchor='w',pady=8)
        button(foot,'짱구만 남기기  ↗',self.hide,bg='#E3E7DA').pack(fill='x',pady=5)
        button(foot,'앱 종료',self.quit,bg='#F0EEE6').pack(fill='x')
        self.main=tk.Frame(self.root,bg=BG); self.main.pack(side='left',fill='both',expand=True,padx=32,pady=25)
        self.content=tk.Frame(self.main,bg=BG); self.content.pack(fill='both',expand=True)
        label(self.main,'',9,MUTED,textvariable=self.status,anchor='w',wraplength=780).pack(fill='x',pady=(12,0))

    def show(self,page):
        self.page=page
        if not getattr(self.root,'pet_only_start',False): self.root.deiconify(); self.root.lift()
        for k,b in self.nav.items(): b.configure(bg='#FFFFFF' if k==page else '#F0EEE6',fg=ORANGE if k==page else INK)
        for child in self.content.winfo_children(): child.destroy()
        self.preview_photos=[]
        getattr(self,'page_'+page)()

    def heading(self,eyebrow,title,sub):
        label(self.content,eyebrow,9,GREEN,bold=True).pack(anchor='w')
        label(self.content,title,25,bold=True).pack(anchor='w',pady=(6,5))
        label(self.content,sub,10,MUTED).pack(anchor='w',pady=(0,20))

    def page_home(self):
        self.heading('YOUR LITTLE FILE COMPANION','어떤 파일을 찾고 있어?','이름이 기억 안 나도 괜찮아. 파일 속 글자까지 찾아볼게.')
        hero=tk.Frame(self.content,bg=PALE,height=150); hero.pack(fill='x'); hero.pack_propagate(False)
        self.hero_photo=ImageTk.PhotoImage(self.sprites.render(self.settings['outfit'],115,self.settings['accessory']))
        tk.Label(hero,image=self.hero_photo,bg=PALE).pack(side='right',padx=20)
        label(hero,'파일은 내가 챙길게.\n너는 하고 싶은 걸 해!',17,bold=True,justify='left').pack(anchor='w',padx=23,pady=(22,5))
        label(hero,'내 주머니에 넣어두면, 언제든 다시 꺼낼 수 있어.',9,GREEN).pack(anchor='w',padx=23)
        search=tk.Frame(self.content,bg=WHITE,highlightbackground=LINE,highlightthickness=1); search.pack(fill='x',pady=(20,10))
        label(search,'⌕',23,ORANGE).pack(side='left',padx=12)
        self.entry=tk.Entry(search,textvariable=self.query,font=(FONT,12),bg=WHITE,fg=INK,relief='flat',insertbackground=ORANGE)
        self.entry.pack(side='left',fill='x',expand=True,ipady=14); self.entry.bind('<Return>',lambda e:self.do_search(True))
        button(search,'찾아줘 →',lambda:self.do_search(True),primary=True).pack(side='right',padx=6,pady=6)
        quick=tk.Frame(self.content,bg=BG); quick.pack(fill='x',pady=(0,12))
        for text in ['인보이스 찾아줘','그중 달러로 된 것만','계약서','최근 파일']:
            button(quick,text,lambda t=text:self.quick(t),bg='#F0EEE6').pack(side='left',padx=(0,6))
        top=tk.Frame(self.content,bg=BG); top.pack(fill='x',pady=3)
        self.result_count=label(top,'내 주머니',11,bold=True); self.result_count.pack(side='left')
        button(top,'새로 읽기',self.reindex,bg=BG).pack(side='right')
        self.results=self.scroll(self.content)
        self.render_results()

    def scroll(self,parent):
        wrap=tk.Frame(parent,bg=BG); wrap.pack(fill='both',expand=True)
        canvas=tk.Canvas(wrap,bg=BG,highlightthickness=0); bar=tk.Scrollbar(wrap,orient='vertical',command=canvas.yview)
        bar.pack(side='right',fill='y'); canvas.pack(side='left',fill='both',expand=True); canvas.configure(yscrollcommand=bar.set)
        inner=tk.Frame(canvas,bg=BG); win=canvas.create_window((0,0),window=inner,anchor='nw')
        inner.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(win,width=e.width))
        def wheel(e):
            if canvas.winfo_exists() and canvas.winfo_containing(e.x_root,e.y_root) is not None:
                w=canvas.winfo_containing(e.x_root,e.y_root)
                if str(w).startswith(str(wrap)): canvas.yview_scroll(-int(e.delta/120),'units')
        # Per-page binding replaces the previous handler rather than accumulating it.
        self.root.bind('<MouseWheel>',wheel)
        return inner

    def quick(self,text): self.query.set(text); self.do_search(True)
    def focus_search(self): self.show('home'); self.entry.focus_set()
    def do_search(self,speak=False):
        command=self.query.get().strip().replace(' ','')
        if command in ('정리해줘','바탕화면정리해줘','정리시작'):
            self.show('organize'); self.plan_clean(); return
        if command in ('춤춰줘','훌라훌라','엉덩이춤'):
            self.pet.event('훌라훌라! 신나게 춤춰볼까?',3); return
        if command in ('꾸미기','꾸며줘','내짱구꾸미기'):
            self.show('closet'); return
        if command in ('조용히해','조용히해줘','말소리꺼줘'):
            self.change('sound',False); self.pet.event('조용히 있을게.',speak=False); return
        self.search_offset=0
        self.render_results()
        if speak:
            self.pet.event(f'{self.last_count}개 찾았어!' if self.last_count else '아직 못 찾았어. 다른 단서도 알려줘.',2)

    def render_results(self):
        if self.page!='home': return
        for c in self.results.winfo_children(): c.destroy()
        query=self.query.get()
        # Refreshing a page must not append the same follow-up to itself.
        if query != self.last_query:
            _,self.resolved_query=self.library.search(query,self.previous,roots=[self.source,self.vault])
            if query.strip(): self.previous=self.resolved_query
            self.last_query=query
        rows,_=self.library.search(self.resolved_query,roots=[self.source,self.vault])
        self.last_count=len(rows)
        indexed,_=self.library.search(roots=[self.source,self.vault])
        incomplete=sum('완료' not in r['status'] for r in indexed)
        mode='샘플 파일 체험 중' if not self.settings['source'] else self.source.name
        self.result_count.configure(text=f'{mode}  ·  {len(rows)}개'+(f'  ·  전체 보관함 중 본문 미분석 {incomplete}개' if incomplete else ''))
        if not rows:
            label(self.results,'아직 찾은 파일이 없어요.\n다른 단어로 찾거나, 정리 탭에서 폴더를 연결해 주세요.',12,MUTED,justify='center').pack(pady=35)
        for r in rows[self.search_offset:self.search_offset+40]:
            card=tk.Frame(self.results,bg=WHITE,highlightbackground=LINE,highlightthickness=1); card.pack(fill='x',pady=4)
            badge=tk.Frame(card,bg='#EDF0E5',width=60,height=62); badge.pack(side='left',padx=12,pady=12); badge.pack_propagate(False)
            label(badge,Path(r['name']).suffix[1:].upper()[:5] or 'FILE',10,GREEN,bold=True).pack(expand=True)
            info=tk.Frame(card,bg=WHITE); info.pack(side='left',fill='x',expand=True,pady=10)
            name=r['name'] if len(r['name'])<38 else r['name'][:35]+'…'
            label(info,name,11,bold=True,anchor='w').pack(fill='x')
            label(info,r['category']+'  ·  '+r['reason'],9,MUTED,anchor='w').pack(fill='x',pady=(4,0))
            button(card,'열기',lambda p=r['path']:self.open_file(p),bg='#F5F4EF').pack(side='right',padx=(4,12))
            button(card,'미리보기',lambda row=r:self.preview(row),bg=WHITE).pack(side='right')
        if len(rows)>40:
            nav=tk.Frame(self.results,bg=BG); nav.pack(fill='x',pady=10)
            if self.search_offset: button(nav,'← 이전',lambda:self.paginate(-40)).pack(side='left')
            label(nav,f'{self.search_offset+1}–{min(self.search_offset+40,len(rows))} / {len(rows)}').pack(side='left',padx=15)
            if self.search_offset+40<len(rows): button(nav,'다음 →',lambda:self.paginate(40)).pack(side='left')
        date_note=' 날짜 조건은 파일 수정일 기준이에요.' if any(x in self.query.get() for x in ['지난달','지난주','최근']) else ''
        self.status.set('로컬 본문·OCR·문서 유형 검색 · 색상/생김새를 이해하는 AI 검색은 아직 지원하지 않아요.'+date_note)

    def paginate(self,delta): self.search_offset+=delta; self.render_results()

    def preview(self,row):
        w=tk.Toplevel(self.root); w.title(row['name']); w.geometry('640x600'); w.configure(bg=BG)
        label(w,row['name'],16,bold=True,wraplength=590).pack(anchor='w',padx=24,pady=(22,8))
        label(w,row['status']+'  ·  '+row['category'],10,GREEN).pack(anchor='w',padx=24,pady=(0,12))
        if Path(row['path']).suffix.lower() in IMAGE_EXTS:
            try:
                with Image.open(row['path']) as im:
                    im.thumbnail((550,260)); photo=ImageTk.PhotoImage(im.copy())
                pic=tk.Label(w,image=photo,bg=BG); pic.image=photo; pic.pack()
            except Exception: pass
        text=tk.Text(w,font=(FONT,11),bg=WHITE,fg=INK,relief='flat',wrap='word',padx=15,pady=15)
        text.pack(fill='both',expand=True,padx=24,pady=10)
        text.insert('1.0',row['body'] or '본문을 읽지 못했어요. 원본 파일을 열어 확인해 주세요.'); text.configure(state='disabled')
        label(w,row['path'],9,MUTED,wraplength=590).pack(padx=24)
        actions=tk.Frame(w,bg=BG); actions.pack(pady=14)
        button(actions,'원본 열기',lambda:self.open_file(row['path']),primary=True).pack(side='left',padx=6)
        button(actions,'폴더에서 보기',lambda:self.reveal(row['path'])).pack(side='left')

    def open_file(self,path):
        try: os.startfile(path)
        except OSError as e: messagebox.showerror('열 수 없어요',str(e))
    def reveal(self,path):
        subprocess.Popen(['explorer.exe','/select,',str(Path(path))])

    def page_organize(self):
        self.heading('A LITTLE CLEANER, A LITTLE HAPPIER','바탕화면, 가볍게 비워볼까?','파일을 보관하고, 언제든 다시 꺼내 줘. 정리는 되돌릴 수 있어.')
        panel=tk.Frame(self.content,bg=WHITE); panel.pack(fill='x',pady=5)
        label(panel,'01   어디를 정리할까?',13,bold=True).pack(anchor='w',padx=22,pady=(20,8))
        label(panel,str(self.source),10,MUTED,wraplength=670,justify='left').pack(anchor='w',padx=22)
        bar=tk.Frame(panel,bg=WHITE); bar.pack(anchor='w',padx=22,pady=12)
        button(bar,'내 바탕화면 연결',lambda:self.choose_source(True),primary=True).pack(side='left',padx=(0,8))
        button(bar,'다른 폴더 선택',lambda:self.choose_source(False),bg='#F3F3ED').pack(side='left')
        label(panel,'02   주머니가 있는 곳',13,bold=True).pack(anchor='w',padx=22,pady=(8,6))
        label(panel,str(self.vault),10,MUTED,wraplength=670,justify='left').pack(anchor='w',padx=22)
        button(panel,'보관 위치 바꾸기',self.choose_vault,bg='#F3F3ED').pack(anchor='w',padx=22,pady=(10,20))
        actions=tk.Frame(self.content,bg=BG); actions.pack(fill='x',pady=16)
        button(actions,'정리할 파일 미리보기 →',self.plan_clean,primary=True).pack(side='left',padx=(0,10))
        button(actions,'마지막 정리 되돌리기',self.undo).pack(side='left')
        self.auto_var=tk.BooleanVar(value=self.settings['auto'])
        tk.Checkbutton(self.content,text='앞으로 들어오는 파일도 자동으로 정리',variable=self.auto_var,
                       command=self.toggle_auto,bg=BG,activebackground=BG,font=(FONT,11),selectcolor=WHITE).pack(anchor='w',pady=8)
        label(self.content,'선택 폴더의 최상위 파일만 이동해요. 폴더·바로가기·저장 중인 파일은 그대로 둬요.\n자동 정리는 앱이 켜져 있을 때 30초마다 확인해요.',10,MUTED,justify='left').pack(anchor='w',pady=8)
        zero=tk.Frame(self.content,bg='#EAF0E4'); zero.pack(fill='x',pady=(20,0))
        label(zero,'배경화면과 짱구만 남기기',12,GREEN,bold=True).pack(anchor='w',padx=18,pady=(15,3))
        label(zero,'아이콘 표시는 숨겨도 파일은 삭제되지 않아요. 앱 종료 시 다시 보여줘요.',9,GREEN).pack(anchor='w',padx=18)
        b=tk.Frame(zero,bg=zero.cget('bg')); b.pack(anchor='w',padx=18,pady=12)
        button(b,'아이콘 숨기기',lambda:self.icons(False),bg=WHITE).pack(side='left',padx=(0,8))
        button(b,'아이콘 다시 보기',lambda:self.icons(True),bg=WHITE).pack(side='left')

    def choose_source(self,desktop=False):
        if self.busy: return self.status.set('현재 작업이 끝나면 폴더를 변경해 주세요.')
        path=str(desktop_path()) if desktop else filedialog.askdirectory(title='정리할 폴더 선택')
        if not path: return
        p=Path(path).resolve()
        if not p.is_dir(): return messagebox.showerror('폴더 확인','폴더가 존재하지 않아요.')
        if p==BASE or p in BASE.parents or BASE in p.parents:
            return messagebox.showerror('폴더 확인','앱 폴더는 정리 대상으로 선택할 수 없어요.')
        if desktop and not messagebox.askyesno('바탕화면 연결',f'{p}\n\n이 폴더를 읽고 파일 내용을 검색에 사용해요.\n실제 이동은 정리 실행 또는 자동 정리를 켠 뒤 진행해요.'): return
        self.settings.update(source=str(p),auto=False)
        if not self.settings['vault']: self.settings['vault']=str(Path.home()/'Documents'/'짱구 보관함')
        self.save(); self.show('organize'); self.reindex()

    def choose_vault(self):
        if self.busy: return self.status.set('현재 작업이 끝나면 변경해 주세요.')
        path=filedialog.askdirectory(title='보관 폴더 선택')
        if path:
            try: self.library.plan(self.source,Path(path))
            except ValueError as e: return messagebox.showerror('폴더 확인',str(e))
            self.settings.update(vault=path,auto=False); self.save(); self.show('organize'); self.reindex()

    def plan_clean(self):
        if self.busy: return self.status.set('파일을 읽는 중이에요. 잠시 후 다시 눌러 주세요.')
        try: plan=self.library.plan(self.source,self.vault)
        except Exception as e: return messagebox.showerror('정리 준비',str(e))
        if not plan: return messagebox.showinfo('정리 준비','지금 이동할 파일이 없어요. 저장 후 10초가 지난 일반 파일만 정리해요.')
        w=tk.Toplevel(self.root); w.title('정리 미리보기'); w.geometry('710x520'); w.configure(bg=BG)
        label(w,f'{len(plan)}개를 주머니에 넣을게!',19,bold=True).pack(anchor='w',padx=24,pady=20)
        text=tk.Text(w,font=(FONT,10),bg=WHITE,relief='flat',wrap='word',padx=15,pady=15); text.pack(fill='both',expand=True,padx=24)
        for p in plan: text.insert('end',f"{Path(p['source']).name}\n  → {p['dest']}\n\n")
        text.configure(state='disabled')
        label(w,'이름이 겹치면 새 이름으로 보관해요. 원본 내용은 바꾸지 않아요.',9,MUTED).pack(pady=10)
        src,vault=self.source,self.vault
        def confirm():
            w.destroy(); self.run_job(lambda:self.library.move(plan,src,vault),self.cleaned,'파일을 안전하게 옮기는 중…')
        actions=tk.Frame(w,bg=BG); actions.pack(pady=(0,18))
        button(actions,'아직 안 할래요',w.destroy).pack(side='left',padx=8)
        button(actions,'② 이 파일들 정리하기',confirm,primary=True).pack(side='left',padx=8)

    def cleaned(self,result):
        done,errors=result
        self.pet.event(f'{len(done)}개 정리 끝! 훌라훌라!',3)
        if errors: messagebox.showwarning('건너뛴 파일', '\n'.join(errors[:15]))
        self.status.set(f'{len(done)}개 보관 완료 · {len(errors)}개 건너뜀')
        self.reindex()

    def undo(self):
        if self.busy: return self.status.set('현재 작업이 끝난 뒤 되돌릴 수 있어요.')
        if not messagebox.askyesno('정리 되돌리기','마지막 정리의 파일들을 원래 위치로 돌려놓을까요?\n자동 정리는 일시 중지돼요.'): return
        self.settings['auto']=False; self.save()
        def finish(result):
            done,errors=result; self.pet.event(f'{len(done)}개 다시 꺼내 놨어!',0)
            if errors: messagebox.showwarning('복원하지 못한 파일','\n'.join(errors[:15]))
            self.reindex()
            if self.page in ('history','organize'): self.show(self.page)
        self.run_job(self.library.undo,finish,'원래 위치로 돌려놓는 중…')

    def toggle_auto(self):
        enabled=self.auto_var.get()
        if enabled:
            try: self.library.plan(self.source,self.vault)
            except Exception as e:
                self.auto_var.set(False); return messagebox.showerror('설정 확인',str(e))
            enabled=messagebox.askyesno('자동 정리 범위 확인',f'{self.source}\n\n이 폴더의 기존 파일과 앞으로 추가되는 파일을\n{self.vault}\n에 자동으로 옮길까요?\n폴더·바로가기 등은 이동하지 않아요.')
        self.auto_var.set(enabled); self.settings['auto']=enabled; self.save()

    def auto_tick(self):
        if self.settings['auto'] and not self.busy:
            src,vault=self.source,self.vault
            def work():
                self.library.index([src,vault]); return self.library.move(self.library.plan(src,vault),src,vault)
            def finish(result):
                if result[0] or result[1]: self.cleaned(result)
            self.run_job(work,finish,'새 파일이 있는지 살펴보는 중…')
        self.root.after(30000,self.auto_tick)

    def icons(self,visible):
        if set_desktop_icons(visible):
            self.icon_changed=not visible
            self.settings['icons_hidden_by_app']=not visible; self.save()
            self.status.set('아이콘을 다시 표시했어요.' if visible else '아이콘을 숨겼어요. 파일은 그대로 있어요.')
        else: messagebox.showinfo('아이콘 표시','Explorer의 바탕화면을 찾지 못했어요. 바탕화면 우클릭 → 보기 → 바탕화면 아이콘 표시를 사용해 주세요.')

    def page_closet(self):
        self.heading('MY SHINCHAN, MY STYLE','오늘은 어떤 짱구로 할까?','옷도, 작은 장식도, 목소리도. 너한테 꼭 맞게 꾸며줘.')
        area=tk.Frame(self.content,bg=BG); area.pack(fill='both',expand=True)
        preview=tk.Frame(area,bg=PALE,width=260); preview.pack(side='left',fill='y',padx=(0,22)); preview.pack_propagate(False)
        label(preview,'MY LITTLE FRIEND',9,GREEN,bold=True).pack(pady=(26,0))
        self.closet_canvas=tk.Canvas(preview,width=255,height=275,bg=PALE,highlightthickness=0); self.closet_canvas.pack(pady=8)
        self.closet_name=label(preview,self.settings['name'],20,bold=True); self.closet_name.pack()
        label(preview,'바꾸면 바로 적용돼요',9,MUTED).pack(pady=8)
        button(preview,'훌라훌라! 춤춰줘',lambda:self.pet.event('훌라훌라! 기분이 좋아!',3),primary=True).pack(pady=(16,8))
        button(preview,'목소리 들어보기',lambda:self.voice.say('안녕! 나는 '+self.settings['name']+'. 파일은 내가 챙길게!',self.settings,True),bg=WHITE).pack(pady=6)
        right=tk.Frame(area,bg=BG); right.pack(side='left',fill='both',expand=True)
        opts=self.scroll(right)
        self.closet_options=opts
        label(opts,'01  오늘의 옷장',12,bold=True).pack(anchor='w',pady=(0,10))
        grid=tk.Frame(opts,bg=BG); grid.pack(fill='x')
        self.outfit_buttons=[]
        for i,title in enumerate(OUTFITS):
            photo=ImageTk.PhotoImage(self.sprites.render(i,75)); self.preview_photos.append(photo)
            b=tk.Button(grid,text=title,image=photo,compound='top',font=(FONT,9),relief='flat',bd=0,
                        bg=WHITE,activebackground=PALE,padx=10,pady=5,cursor='hand2',command=lambda n=i:self.change('outfit',n))
            b.grid(row=i//2,column=i%2,padx=(0,8),pady=(0,8),sticky='ew'); self.outfit_buttons.append(b)
        grid.columnconfigure(0,weight=1); grid.columnconfigure(1,weight=1)
        row=tk.Frame(opts,bg=BG); row.pack(fill='x',pady=(8,4))
        label(row,'작은 장식',10,bold=True).pack(side='left')
        self.option(row,'accessory',['없음','별 핀','하트','동그란 안경']).pack(side='right')
        row=tk.Frame(opts,bg=BG); row.pack(fill='x',pady=4)
        label(row,'미리보기 배경',10,bold=True).pack(side='left')
        self.option(row,'backdrop',['크림','풀밭','라벤더']).pack(side='right')
        row=tk.Frame(opts,bg=BG); row.pack(fill='x',pady=4)
        label(row,'별명',10,bold=True).pack(side='left')
        name=tk.StringVar(value=self.settings['name']); entry=tk.Entry(row,textvariable=name,font=(FONT,10),width=14,relief='flat'); entry.pack(side='right',ipady=5)
        entry.bind('<FocusOut>',lambda e:self.change('name',name.get().strip()[:12] or '짱구'))
        label(opts,'크기',10,bold=True).pack(anchor='w',pady=(8,0))
        self.scale(opts,'size',120,260)
        row=tk.Frame(opts,bg=BG); row.pack(fill='x')
        self.check(row,'animate','움직임').pack(side='left')
        self.check(row,'auto_outfit','상황별 변신').pack(side='left')
        self.check(row,'topmost','항상 위에').pack(side='left')
        row=tk.Frame(opts,bg=BG); row.pack(fill='x',pady=(12,0))
        label(row,'02  말하는 짱구',12,bold=True).pack(side='left')
        self.check(row,'sound','말소리').pack(side='right')
        voices=getattr(self,'voices',['Microsoft Heami Desktop','Microsoft Heami','Microsoft Zira Desktop'])
        self.option(opts,'voice',voices).pack(fill='x',pady=(8,0))
        controls=tk.Frame(opts,bg=BG); controls.pack(fill='x')
        for key,title,lo,hi in [('rate','속도',-3,5),('volume','볼륨',0,100)]:
            frame=tk.Frame(controls,bg=BG); frame.pack(side='left',fill='x',expand=True,padx=(0,8))
            label(frame,title,9,MUTED).pack(anchor='w'); self.scale(frame,key,lo,hi)
        self.update_closet()
        self.status.set('설정은 자동 저장돼요. 음성은 Windows 설치 음성이며 원작 캐릭터 음성이 아니에요.')

    def option(self,parent,key,options):
        var=tk.StringVar(value=self.settings[key]); menu=tk.OptionMenu(parent,var,*options,command=lambda v:self.change(key,v))
        menu.configure(bg=WHITE,fg=INK,relief='flat',highlightthickness=0,font=(FONT,9),activebackground=PALE)
        menu['menu'].configure(font=(FONT,10),bg=WHITE); return menu
    def check(self,parent,key,title):
        var=tk.BooleanVar(value=self.settings[key]); return tk.Checkbutton(parent,text=title,variable=var,command=lambda:self.change(key,var.get()),
                     bg=BG,activebackground=BG,selectcolor=WHITE,font=(FONT,9))
    def scale(self,parent,key,lo,hi):
        slider=tk.Scale(parent,from_=lo,to=hi,orient='horizontal',bg=BG,fg=MUTED,highlightthickness=0,bd=0,
                        troughcolor='#E8E4D8',activebackground=ORANGE,font=(FONT,8),sliderrelief='flat',showvalue=True)
        slider.set(self.settings[key]); slider.configure(command=lambda v:self.change(key,int(v)))
        slider.pack(fill='x'); return slider
    def change(self,key,value):
        self.settings[key]=value; self.save()
        if key=='sound' and not value: self.voice.stop()
        self.pet.refresh()
        if self.page=='closet': self.update_closet()
    def update_closet(self):
        colors={'크림':PALE,'풀밭':'#E1EDD8','라벤더':'#E9E2F4'}
        c=self.closet_canvas; c.configure(bg=colors[self.settings['backdrop']]); c.delete('all')
        c.create_oval(39,230,216,258,fill='#D2D1B9',outline='')
        c.create_text(35,60,text='✦',fill=ORANGE,font=(FONT,24)); c.create_text(225,135,text='✧',fill=GREEN,font=(FONT,20))
        self.closet_photo=ImageTk.PhotoImage(self.sprites.render(self.settings['outfit'],205,self.settings['accessory']))
        c.create_image(128,250,image=self.closet_photo,anchor='s')
        self.closet_name.configure(text=self.settings['name'])
        for i,b in enumerate(self.outfit_buttons): b.configure(bg='#FCE1C6' if i==self.settings['outfit'] else WHITE)
    def toggle_sound(self):
        self.change('sound',not self.settings['sound'])
        self.pet.event('말소리를 켰어!' if self.settings['sound'] else '조용히 있을게.',speak=self.settings['sound'])

    def load_voices(self):
        def task():
            try:
                r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                                  '-File',str(BASE/'speech.ps1'),'-List'],capture_output=True,timeout=15,creationflags=0x08000000)
                values=json.loads(r.stdout.decode('utf-8-sig'))
                if isinstance(values,dict): values=[values]
                voices=[v['name'] for v in sorted(values,key=lambda v:v['culture']!='ko-KR')]
                def apply():
                    self.voices=voices or ['Windows 기본 음성']
                    if voices and self.settings['voice'] not in voices:
                        self.settings['voice']=voices[0]; self.save()
                self.events.put(apply)
            except Exception: pass
        threading.Thread(target=task,daemon=True).start()

    def page_history(self):
        self.heading('EVERYTHING HAS ITS PLACE','어디에 넣었는지, 다 기억해.','옮긴 기록을 확인하고 마지막 정리를 되돌릴 수 있어.')
        button(self.content,'마지막 정리 되돌리기',self.undo,primary=True).pack(anchor='w',pady=(0,16))
        area=self.scroll(self.content); rows=self.library.history()
        if not rows: label(area,'아직 정리한 기록이 없어요.\n첫 정리를 하면 여기에 남겨둘게!',13,MUTED).pack(pady=60)
        names={'done':'보관 완료','undone':'복원 완료','error':'확인 필요','pending':'작업 확인 중'}
        for r in rows[:200]:
            card=tk.Frame(area,bg=WHITE); card.pack(fill='x',pady=5)
            label(card,Path(r['source']).name+'  ·  '+names.get(r['state'],r['state']),11,bold=True).pack(anchor='w',padx=16,pady=(12,5))
            stamp=datetime.fromtimestamp(r['created']).strftime('%m.%d %H:%M')
            label(card,stamp+'  →  '+r['dest'],9,MUTED,wraplength=690,justify='left').pack(anchor='w',padx=16,pady=(0,12))
            if r['error']: label(card,r['error'],9,ORANGE,wraplength=690).pack(anchor='w',padx=16,pady=(0,10))

    def run_job(self,fn,finish,text):
        if self.busy: self.status.set('진행 중인 작업이 끝나면 다시 해주세요.'); return
        self.busy=True; self.status.set(text)
        future=self.pool.submit(fn)
        def ready(f):
            def dispatch():
                self.busy=False
                try: result=f.result()
                except Exception as e:
                    self.status.set('작업 실패: '+str(e)); messagebox.showerror('작업 확인',str(e)); return
                finish(result)
            self.events.put(dispatch)
        future.add_done_callback(ready)

    def reindex(self):
        source,vault=self.source,self.vault
        def finish(count):
            self.status.set(f'{count}개 파일을 읽었어. 이름 대신 내용으로도 찾아봐!')
            if self.page=='home': self.render_results()
        self.run_job(lambda:self.library.index([source,vault]),finish,'파일 속 글자를 읽는 중… 이미지가 많으면 조금 걸려요.')
    def poll(self):
        try:
            while True: self.events.get_nowait()()
        except queue.Empty: pass
        self.root.after(100,self.poll)
    def hide(self): self.root.withdraw(); self.pet.event('여기 있을게. 필요하면 날 눌러줘!',speak=False)
    def quit(self):
        if self.busy:
            messagebox.showinfo('잠깐만!','파일 작업이 진행 중이에요. 끝난 뒤 종료해 주세요.'); return
        if self.icon_changed or self.settings.get('icons_hidden_by_app'): set_desktop_icons(True)
        self.settings['icons_hidden_by_app']=False; self.save()
        self.voice.close(); self.pool.shutdown(wait=False,cancel_futures=True); self.root.destroy()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--data-dir'); parser.add_argument('--background',action='store_true'); parser.add_argument('--show-window',action='store_true'); args=parser.parse_args()
    from single_instance import SingleInstance
    instance=SingleInstance()
    if not instance.first:
        instance.close(); return
    enable_dpi_awareness()
    from easy_app import EasyApp
    try:
        root=tk.Tk(); root.withdraw(); root.pet_only_start=not args.show_window
        app=EasyApp(root,args.data_dir)
        root.pet_only_start=False
        if not args.show_window: root.withdraw()
        def wake():
            if instance.requested(): app.open_desktop_search()
            root.after(400,wake)
        root.after(400,wake)
        if not args.background and not args.show_window: root.after(400,app.open_desktop_search)
        root.mainloop()
    finally: instance.close()

if __name__=='__main__': main()
