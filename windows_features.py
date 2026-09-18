"""User-invoked desktop integrations; never executes model-provided commands."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading

class Hotkey:
    def __init__(self,callback):
        self.callback=callback; self.thread_id=None; self.error=''; self.ready=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True); self.thread.start()
    def run(self):
        u=ctypes.windll.user32
        self.thread_id=ctypes.windll.kernel32.GetCurrentThreadId()
        if not u.RegisterHotKey(None,0x5A17,0x4000|0x0002|0x0004,0x20):
            self.error='Ctrl+Shift+Space가 다른 앱에서 사용 중이에요.'; self.ready.set(); return
        self.ready.set(); msg=wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg),None,0,0)>0:
                if msg.message==0x0312: self.callback()
        finally: u.UnregisterHotKey(None,0x5A17)
    def close(self):
        if self.thread_id: ctypes.windll.user32.PostThreadMessageW(self.thread_id,0x0012,0,0)

def foreground_focus_reason(own_hwnds):
    u=ctypes.windll.user32
    u.GetForegroundWindow.restype=wintypes.HWND
    u.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    u.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    u.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    hwnd=u.GetForegroundWindow()
    if not hwnd or hwnd in own_hwnds: return ''
    title=ctypes.create_unicode_buffer(1024); u.GetWindowTextW(hwnd,title,1024)
    cls=ctypes.create_unicode_buffer(128); u.GetClassNameW(hwnd,cls,128)
    if cls.value in ('Progman','WorkerW','Shell_TrayWnd'): return ''
    if any(s in title.value.lower() for s in ('zoom meeting','zoom 회의','microsoft teams','google meet','meet -')):
        return '회의 창 감지'
    u.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD]; u.MonitorFromWindow.restype=wintypes.HANDLE
    class Info(ctypes.Structure):
        _fields_=[('cbSize',wintypes.DWORD),('monitor',wintypes.RECT),('work',wintypes.RECT),('flags',wintypes.DWORD)]
    info=Info(); info.cbSize=ctypes.sizeof(info); rect=wintypes.RECT()
    u.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Info)]
    if u.GetWindowRect(hwnd,ctypes.byref(rect)) and u.GetMonitorInfoW(u.MonitorFromWindow(hwnd,2),ctypes.byref(info)):
        m=info.monitor
        if rect.left<=m.left and rect.top<=m.top and rect.right>=m.right and rect.bottom>=m.bottom:
            return '전체화면 감지'
    return ''

def copy_files(paths):
    paths=[str(Path(p).resolve()) for p in paths if Path(p).exists()]
    if not paths: raise ValueError('복사할 파일이 없어요.')
    class DROPFILES(ctypes.Structure):
        _fields_=[('pFiles',wintypes.DWORD),('pt',wintypes.POINT),('fNC',wintypes.BOOL),('fWide',wintypes.BOOL)]
    header=DROPFILES(); header.pFiles=ctypes.sizeof(header); header.fWide=True
    payload=bytes(header)+('\0'.join(paths)+'\0\0').encode('utf-16-le')
    k=ctypes.windll.kernel32; u=ctypes.windll.user32
    k.GlobalAlloc.argtypes=[wintypes.UINT,ctypes.c_size_t]; k.GlobalAlloc.restype=wintypes.HGLOBAL
    k.GlobalLock.argtypes=[wintypes.HGLOBAL]; k.GlobalLock.restype=ctypes.c_void_p
    k.GlobalUnlock.argtypes=[wintypes.HGLOBAL]; k.GlobalFree.argtypes=[wintypes.HGLOBAL]
    u.SetClipboardData.argtypes=[wintypes.UINT,wintypes.HANDLE]; u.SetClipboardData.restype=wintypes.HANDLE
    handle=k.GlobalAlloc(0x0042,len(payload)); pointer=k.GlobalLock(handle)
    if not pointer: raise OSError('클립보드 메모리를 준비하지 못했어요.')
    ctypes.memmove(pointer,payload,len(payload)); k.GlobalUnlock(handle)
    if not u.OpenClipboard(None): k.GlobalFree(handle); raise OSError('클립보드를 다른 앱이 사용 중이에요.')
    try:
        u.EmptyClipboard()
        if not u.SetClipboardData(15,handle): k.GlobalFree(handle); raise OSError('파일 복사에 실패했어요.')
    finally: u.CloseClipboard()

def get_wallpaper():
    buffer=ctypes.create_unicode_buffer(32768)
    ctypes.windll.user32.SystemParametersInfoW(0x0073,len(buffer),buffer,0)
    return buffer.value

def set_wallpaper(path):
    path=str(Path(path).resolve())
    if not Path(path).is_file(): raise ValueError('배경 이미지가 없어요.')
    if not ctypes.windll.user32.SystemParametersInfoW(20,0,path,3): raise ctypes.WinError()

def launchers(desktop):
    roots=[Path(desktop),Path(os.environ.get('PUBLIC',r'C:\Users\Public'))/'Desktop',
           Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs',
           Path(os.environ['PROGRAMDATA'])/'Microsoft/Windows/Start Menu/Programs']
    results=[]; seen=set()
    for index,root in enumerate(roots):
        if not root.exists(): continue
        for p in (root.glob('*.lnk') if index<2 else root.rglob('*.lnk')):
            key=p.stem.lower()
            if key not in seen: results.append((p.stem,str(p))); seen.add(key)
    return sorted(results,key=lambda r:r[0].lower())

def startup(enabled,script):
    import sys,winreg
    python=Path(sys.executable).with_name('pythonw.exe')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled: winreg.SetValueEx(key,'ShinchanPocket',0,winreg.REG_SZ,f'"{python}" "{Path(script).resolve()}" --background')
        else:
            try: winreg.DeleteValue(key,'ShinchanPocket')
            except FileNotFoundError: pass
