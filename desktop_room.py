"""Interactive desktop room surfaces, visible only while the desktop is active."""
import ctypes
from ctypes import wintypes
import tkinter as tk
from PIL import Image,ImageTk
from monitors import DesktopSpace
from core import BASE

REGIONS=[(.04,.43,.31,.77,'침대 · 집중','focus'),(.32,.30,.55,.59,'책상 · 찾기','search'),
         (.59,.34,.73,.58,'장난감 · 트레이','tray'),(.72,.25,.84,.57,'서랍 · 꾸미기','closet')]

def on_desktop(owned):
    u=ctypes.windll.user32; u.GetForegroundWindow.restype=wintypes.HWND
    hwnd=u.GetForegroundWindow()
    if hwnd in owned: return True
    name=ctypes.create_unicode_buffer(256)
    u.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    u.GetClassNameW(hwnd,name,256)
    return name.value in ('Progman','WorkerW')

def above_desktop(exclude):
    u=ctypes.windll.user32; previous=[0]
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    u.IsWindowVisible.argtypes=[wintypes.HWND]
    @callback
    def visit(hwnd,_):
        if hwnd in exclude or not u.IsWindowVisible(hwnd): return True
        name=ctypes.create_unicode_buffer(256); u.GetClassNameW(hwnd,name,256)
        if name.value in ('Progman','WorkerW'): return False
        previous[0]=hwnd; return True
    u.EnumWindows(visit,0)
    return previous[0]

class DesktopRoom:
    def __init__(self,app):
        self.app=app; self.windows=[]; self.closed=False; self.screens=None; self.timer=None
        self.image=Image.open(BASE/'assets/shinchan-room-anime-v2.png').convert('RGB')
        self.refresh(); self.tick()
    def refresh(self):
        screens=self.app.pet.space.screens()
        if screens==self.screens: return
        for win,_,_ in self.windows: win.destroy()
        self.windows=[]; self.screens=screens
        for left,top,right,bottom in screens:
            width,height=right-left,bottom-top
            win=tk.Toplevel(self.app.root); win.withdraw(); win.overrideredirect(True)
            win.attributes('-toolwindow',True); win.geometry(f'{width}x{height}')
            canvas=tk.Canvas(win,bg='#FFF5E6',highlightthickness=0); canvas.pack(fill='both',expand=True)
            scale=min(width/self.image.width,height/self.image.height)
            iw,ih=int(self.image.width*scale),int(self.image.height*scale); ox,oy=(width-iw)//2,(height-ih)//2
            photo=ImageTk.PhotoImage(self.image.resize((iw,ih),Image.Resampling.LANCZOS)); canvas.image=photo
            canvas.create_image(ox,oy,image=photo,anchor='nw'); regions=[]
            for x1,y1,x2,y2,title,action in REGIONS:
                rect=(ox+x1*iw,oy+y1*ih,ox+x2*iw,oy+y2*ih)
                regions.append((*rect,action))
                canvas.create_text((rect[0]+rect[2])/2,rect[3]+15,text=title,fill='#374337',font=('맑은 고딕',11,'bold'))
            canvas.bind('<Button-1>',lambda event,rs=regions:self.click(event.x,event.y,rs))
            canvas.bind('<Motion>',lambda event,c=canvas,rs=regions:c.configure(cursor='hand2' if any(x1<=event.x<=x2 and y1<=event.y<=y2 for x1,y1,x2,y2,_ in rs) else ''))
            win.bind('<Escape>',lambda e:self.app.change('desktop_room',False))
            win.update_idletasks(); space=DesktopSpace(win); space.move(left,top)
            u=ctypes.windll.user32
            u.GetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int]; u.SetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_long]
            u.SetWindowLongW(space.handle(),-20,u.GetWindowLongW(space.handle(),-20)|0x08000000|0x80)
            self.windows.append((win,space,regions))
    def click(self,x,y,regions):
        for x1,y1,x2,y2,action in regions:
            if x1<=x<=x2 and y1<=y<=y2:
                self.activate(action); break
    def activate(self,action):
        if action=='search':
            if not self.app.bubble or not self.app.bubble.alive(): self.app.toggle_bubble()
        elif action=='focus': self.app.change('focus_manual',not self.app.settings.get('focus_manual',False))
        else: self.app.show(action)
    def tick(self):
        if self.closed: return
        self.refresh()
        owned=[space.handle() for _,space,_ in self.windows]+[self.app.pet.space.handle()]
        if self.app.bubble and self.app.bubble.alive(): owned.append(self.app.bubble.space.handle())
        visible=on_desktop(owned)
        after=above_desktop(owned) if visible else 0
        for win,space,_ in self.windows:
            if visible:
                if not space.u.IsWindowVisible(space.handle()):
                    # Show without activating; never raise above foreground applications.
                    space.u.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
                    space.u.ShowWindow(space.handle(),4)
                    space.u.SetWindowPos(space.handle(),after,0,0,0,0,0x0013)
            elif space.u.IsWindowVisible(space.handle()):
                space.u.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
                space.u.ShowWindow(space.handle(),0)
        self.timer=self.app.root.after(250,self.tick)
    def close(self):
        self.closed=True
        if self.timer:
            try: self.app.root.after_cancel(self.timer)
            except tk.TclError: pass
        for win,_,_ in self.windows: win.destroy()
        self.windows=[]
