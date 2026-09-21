"""Match Windows IME composition text to the focused Tk entry's actual font/caret."""
import ctypes
from ctypes import wintypes as W
import os
import tkinter as tk
from tkinter import font as tkfont


class LOGFONT(ctypes.Structure):
    _fields_=[(name,W.LONG) for name in ('height','width','escapement','orientation','weight')]+[
        (name,W.BYTE) for name in ('italic','underline','strikeout','charset','outprecision','clipprecision','quality','pitch')]+[('face',W.WCHAR*32)]


class COMPOSITIONFORM(ctypes.Structure):
    _fields_=[('style',W.DWORD),('point',W.POINT),('area',W.RECT)]


class ImeEntry:
    def __init__(self,entry):
        self.entry=entry; self.timer=None; self.last_applied=None
        self.imm=ctypes.WinDLL('imm32'); self.user=ctypes.WinDLL('user32')
        self.imm.ImmGetContext.argtypes=[W.HWND]; self.imm.ImmGetContext.restype=W.HANDLE
        self.imm.ImmReleaseContext.argtypes=[W.HWND,W.HANDLE]
        self.imm.ImmSetCompositionFontW.argtypes=[W.HANDLE,ctypes.POINTER(LOGFONT)]
        self.imm.ImmSetCompositionWindow.argtypes=[W.HANDLE,ctypes.POINTER(COMPOSITIONFORM)]
        self.imm.ImmNotifyIME.argtypes=[W.HANDLE,W.DWORD,W.DWORD,W.DWORD]
        self.user.GetFocus.restype=W.HWND
        self.user.MapWindowPoints.argtypes=[W.HWND,W.HWND,ctypes.POINTER(W.POINT),W.UINT]
        entry.bind('<FocusIn>',self.start,add='+')
        entry.bind('<FocusOut>',self.stop,add='+')
        entry.bind('<Destroy>',self.stop,add='+')
        for event in ('<KeyRelease>','<ButtonRelease-1>','<Configure>'):
            entry.bind(event,self.schedule,add='+')

    def geometry(self):
        entry=self.entry; font=tkfont.Font(root=entry,font=entry.cget('font'))
        actual=font.actual(); size=actual['size']
        height=round(entry.winfo_fpixels(f'{size}p')) if size>0 else abs(size)
        index=entry.index('insert'); length=len(entry.get())
        box=entry.bbox(index if index<length else max(0,index-1))
        if box:
            x,y,width,_=box
            if index==length and length:x+=width
        else:x,y=3,max(0,(entry.winfo_height()-font.metrics('linespace'))//2)
        return actual,max(1,height),max(2,min(x,entry.winfo_width()-3)),max(0,y)

    def sync(self):
        entry=self.entry
        if not entry.winfo_exists() or entry.focus_get()!=entry:return False
        hwnd=self.user.GetFocus() or entry.winfo_id()
        context=self.imm.ImmGetContext(hwnd)
        if not context:return False
        try:
            actual,height,x,y=self.geometry()
            point=W.POINT(x,y); self.user.MapWindowPoints(entry.winfo_id(),hwnd,ctypes.byref(point),1)
            font=LOGFONT(); font.height=-height; font.weight=700 if actual['weight']=='bold' else 400
            font.italic=actual['slant']=='italic'; font.charset=1; font.face=actual['family'][:31]
            position=COMPOSITIONFORM(); position.style=2; position.point=point
            ok=self.imm.ImmSetCompositionFontW(context,ctypes.byref(font))
            ok=self.imm.ImmSetCompositionWindow(context,ctypes.byref(position)) and ok
            self.last_applied=(hwnd,font.height,font.face,point.x,point.y)
            return bool(ok)
        finally:self.imm.ImmReleaseContext(hwnd,context)

    def schedule(self,event=None):
        if self.entry.winfo_exists():self.entry.after_idle(self.safe_sync)

    def safe_sync(self):
        try:self.sync()
        except tk.TclError:pass

    def start(self,event=None):
        self.stop(); self.tick()

    def tick(self):
        self.timer=None
        try:
            if self.entry.winfo_exists() and self.entry.focus_get()==self.entry:
                self.sync(); self.timer=self.entry.after(100,self.tick)
        except tk.TclError:pass

    def stop(self,event=None):
        if self.timer is not None:
            try:self.entry.after_cancel(self.timer)
            except tk.TclError:pass
            self.timer=None

    def commit_then(self,callback):
        # Finish composition before reading StringVar; do not change keyboard/language settings.
        hwnd=self.user.GetFocus() or self.entry.winfo_id()
        context=self.imm.ImmGetContext(hwnd)
        if context:
            try:self.imm.ImmNotifyIME(context,0x15,1,0)  # NI_COMPOSITIONSTR, CPS_COMPLETE
            finally:self.imm.ImmReleaseContext(hwnd,context)
        self.entry.after_idle(callback)
        return 'break'


def attach(entry):
    return ImeEntry(entry) if os.name=='nt' else None
