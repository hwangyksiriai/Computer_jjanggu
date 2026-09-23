"""User-invoked desktop integrations; never executes model-provided commands."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import struct
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

def _clipboard_api():
    """Private DLL instances keep pointer-sized signatures separate from hotkeys."""
    if os.name!='nt':raise OSError('파일 복사는 Windows에서 사용할 수 있어요.')
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    u=ctypes.WinDLL('user32',use_last_error=True)
    signatures=(
        (k,'GlobalAlloc',[wintypes.UINT,ctypes.c_size_t],wintypes.HGLOBAL),
        (k,'GlobalLock',[wintypes.HGLOBAL],ctypes.c_void_p),
        (k,'GlobalUnlock',[wintypes.HGLOBAL],wintypes.BOOL),
        (k,'GlobalFree',[wintypes.HGLOBAL],wintypes.HGLOBAL),
        (k,'GetModuleHandleW',[wintypes.LPCWSTR],wintypes.HMODULE),
        (u,'RegisterClipboardFormatW',[wintypes.LPCWSTR],wintypes.UINT),
        (u,'IsWindow',[wintypes.HWND],wintypes.BOOL),
        (u,'OpenClipboard',[wintypes.HWND],wintypes.BOOL),
        (u,'EmptyClipboard',[],wintypes.BOOL),
        (u,'SetClipboardData',[wintypes.UINT,wintypes.HANDLE],wintypes.HANDLE),
        (u,'CloseClipboard',[],wintypes.BOOL),
        (u,'CreateWindowExW',[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,
            ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HMENU,
            wintypes.HINSTANCE,ctypes.c_void_p],wintypes.HWND),
        (u,'DestroyWindow',[wintypes.HWND],wintypes.BOOL),
    )
    for dll,name,args,result in signatures:
        function=getattr(dll,name);function.argtypes=args;function.restype=result
    return k,u


def _clipboard_memory(kernel,payload):
    handle=kernel.GlobalAlloc(0x0042,len(payload))  # GMEM_MOVEABLE | GMEM_ZEROINIT
    if not handle:raise OSError('클립보드 메모리를 준비하지 못했어요.')
    try:
        pointer=kernel.GlobalLock(handle)
        if not pointer:raise OSError('클립보드 메모리를 열지 못했어요.')
        try:ctypes.memmove(pointer,payload,len(payload))
        finally:
            ctypes.set_last_error(0)
            unlocked=kernel.GlobalUnlock(handle)
            # GlobalUnlock returns zero on success when its last lock is gone.
            if not unlocked and ctypes.get_last_error():
                raise OSError('클립보드 메모리를 닫지 못했어요.')
    except BaseException:
        kernel.GlobalFree(handle)
        raise
    return handle


def copy_files(paths,owner_hwnd=None):
    """Publish file references with COPY intent, without touching originals.

    All requested files must exist. Allocation and validation precede changing
    the clipboard; its previous contents are never read. A successful call
    returns the absolute, deduplicated paths. The optional owner is a live HWND.
    Legacy callers use a hidden message-only window on the calling thread.

    Win32 contracts: learn.microsoft.com/windows/win32/api/winuser/nf-winuser-setclipboarddata
    and learn.microsoft.com/windows/win32/shell/clipboard.
    """
    from file_transfer import validate_file_paths
    paths=validate_file_paths(paths)
    # DROPFILES is five 32-bit fields on both 32-bit and 64-bit Windows.
    payload=struct.pack('<IiiII',20,0,0,0,1)+('\0'.join(paths)+'\0\0').encode('utf-16-le')
    k,u=_clipboard_api()
    pending=[];hidden=None;opened=False;close_ok=True
    try:
        effect=u.RegisterClipboardFormatW('Preferred DropEffect')
        if not effect:raise OSError('파일 복사 형식을 준비하지 못했어요.')
        copy_handle=_clipboard_memory(k,struct.pack('<I',1))  # DROPEFFECT_COPY
        pending.append(copy_handle)
        files_handle=_clipboard_memory(k,payload)
        pending.append(files_handle)
        if owner_hwnd is None:
            hidden=u.CreateWindowExW(0,'STATIC','Jjanggu file clipboard',0,0,0,0,0,
                                     wintypes.HWND(-3),None,k.GetModuleHandleW(None),None)
            if not hidden:raise OSError('파일 복사를 준비하지 못했어요. 다시 시도해 주세요.')
            owner=hidden
        else:
            owner=getattr(owner_hwnd,'value',owner_hwnd)
            if not isinstance(owner,int) or isinstance(owner,bool) or owner<=0 or not u.IsWindow(owner):
                raise ValueError('복사할 창이 닫혔어요. 파일을 다시 선택해 주세요.')
        # A file may disappear while native memory/window preparation runs.
        validate_file_paths(paths)
        if not u.OpenClipboard(owner):raise OSError('다른 앱이 클립보드를 사용 중이에요. 다시 복사해 주세요.')
        opened=True
        if not u.EmptyClipboard():raise OSError('클립보드를 준비하지 못했어요. 다시 복사해 주세요.')
        # Publish intent first: a failed second format must not leave usable
        # file references with an unspecified move/copy operation.
        for format_id,handle in ((effect,copy_handle),(15,files_handle)):
            if not u.SetClipboardData(format_id,handle):
                u.EmptyClipboard()  # OS releases any already-transferred handle.
                raise OSError('파일 복사를 마치지 못했어요. 다시 복사해 주세요.')
            pending.remove(handle)  # From now on only Windows may free it.
    finally:
        if opened:close_ok=bool(u.CloseClipboard())
        for handle in pending:k.GlobalFree(handle)
        if hidden:u.DestroyWindow(hidden)
    if not close_ok:raise OSError('클립보드를 닫지 못했어요. 다시 복사해 주세요.')
    return paths

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
        if enabled:
            command=f'"{sys.executable}" --background' if getattr(sys,'frozen',False) else f'"{python}" "{Path(script).resolve()}" --background'
            winreg.SetValueEx(key,'ShinchanPocket',0,winreg.REG_SZ,command)
        else:
            try: winreg.DeleteValue(key,'ShinchanPocket')
            except FileNotFoundError: pass
