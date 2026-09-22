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
        self.entry=entry; self.timer=None; self.idle=None; self.submission=None; self.last_applied=None
        self.imm=ctypes.WinDLL('imm32'); self.user=ctypes.WinDLL('user32')
        self.imm.ImmGetContext.argtypes=[W.HWND]; self.imm.ImmGetContext.restype=W.HANDLE
        self.imm.ImmReleaseContext.argtypes=[W.HWND,W.HANDLE]
        self.imm.ImmSetCompositionFontW.argtypes=[W.HANDLE,ctypes.POINTER(LOGFONT)]
        self.imm.ImmGetCompositionFontW.argtypes=[W.HANDLE,ctypes.POINTER(LOGFONT)]
        self.imm.ImmSetCompositionWindow.argtypes=[W.HANDLE,ctypes.POINTER(COMPOSITIONFORM)]
        self.imm.ImmGetCompositionWindow.argtypes=[W.HANDLE,ctypes.POINTER(COMPOSITIONFORM)]
        self.imm.ImmNotifyIME.argtypes=[W.HANDLE,W.DWORD,W.DWORD,W.DWORD]
        self.imm.ImmGetCompositionStringW.argtypes=[W.HANDLE,W.DWORD,ctypes.c_void_p,W.DWORD]
        self.imm.ImmGetCompositionStringW.restype=W.LONG
        self.user.GetFocus.restype=W.HWND
        self.user.MapWindowPoints.argtypes=[W.HWND,W.HWND,ctypes.POINTER(W.POINT),W.UINT]
        entry.bind('<FocusIn>',self.start,add='+')
        entry.bind('<FocusOut>',self.stop,add='+')
        entry.bind('<Destroy>',self.destroy,add='+')
        for event in ('<KeyPress>','<KeyRelease>','<ButtonRelease-1>','<Configure>'):
            entry.bind(event,self.schedule,add='+')

    def geometry(self):
        entry=self.entry; font=tkfont.Font(root=entry,font=entry.cget('font'))
        actual=font.actual(); size=actual['size']
        height=round(entry.winfo_fpixels(f'{size}p')) if size>0 else abs(size)
        index=entry.index('insert'); length=entry.index('end')
        # Entry.bbox is inherited Misc.grid_bbox, NOT the character rectangle.
        # It returned (0, 0, 0, 0) in the packed bubble. Ask Tk's entry directly.
        box=entry.tk.call(entry._w,'bbox',index if index<length else max(0,index-1))
        box=tuple(map(int,entry.tk.splitlist(box))) if box else ()
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
            current_font=LOGFONT(); current_position=COMPOSITIONFORM()
            font_same=self.imm.ImmGetCompositionFontW(context,ctypes.byref(current_font)) and (
                current_font.height,current_font.face,current_font.weight,current_font.italic)==(
                font.height,font.face,font.weight,font.italic)
            position_same=self.imm.ImmGetCompositionWindow(context,ctypes.byref(current_position)) and (
                current_position.style,current_position.point.x,current_position.point.y)==(position.style,point.x,point.y)
            # Repeatedly resetting an unchanged composition window makes it flicker.
            ok=font_same or self.imm.ImmSetCompositionFontW(context,ctypes.byref(font))
            ok=(position_same or self.imm.ImmSetCompositionWindow(context,ctypes.byref(position))) and ok
            self.last_applied=(hwnd,font.height,font.face,point.x,point.y)
            return bool(ok)
        finally:self.imm.ImmReleaseContext(hwnd,context)

    def schedule(self,event=None):
        if self.entry.winfo_exists() and self.idle is None:self.idle=self.entry.after_idle(self.safe_sync)

    def safe_sync(self):
        self.idle=None
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
        for name in ('timer','idle'):
            ident=getattr(self,name)
            if ident is not None:
                try:self.entry.after_cancel(ident)
                except tk.TclError:pass
                setattr(self,name,None)

    def destroy(self,event=None):
        self.stop()
        if self.submission is not None:
            try:self.entry.after_cancel(self.submission)
            except tk.TclError:pass
            self.submission=None

    def commit_then(self,callback):
        if self.submission is not None:return 'break'
        # Finish composition before reading StringVar; do not change keyboard/language settings.
        hwnd=self.user.GetFocus() or self.entry.winfo_id()
        context=self.imm.ImmGetContext(hwnd)
        if context:
            try:self.imm.ImmNotifyIME(context,0x15,1,0)  # NI_COMPOSITIONSTR, CPS_COMPLETE
            finally:self.imm.ImmReleaseContext(hwnd,context)
        def submit():
            self.submission=None
            if self.entry.winfo_exists():callback()
        # Windows posts the final composed character after the IME notification.
        # Let its message round-trip complete before reading the entry variable.
        self.submission=self.entry.after(25,submit)
        return 'break'


def attach(entry):
    return ImeEntry(entry) if os.name=='nt' else None
