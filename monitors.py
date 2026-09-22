"""Virtual-desktop coordinates for the Windows pet, including negative origins."""
import ctypes
from ctypes import wintypes
import os


def enable_dpi_awareness():
    if os.name == 'nt':
        # Set before Tk starts. System-aware coordinates stay consistent across
        # GetCursorPos, monitor rectangles and SetWindowPos (including mixed DPI).
        ctypes.windll.user32.SetProcessDPIAware()


def fit_position(x, y, width, height, screens):
    """Fit into the nearest real monitor, never into a virtual-desktop gap."""
    if not screens:
        return int(x), int(y)
    candidates = []
    for left, top, right, bottom in screens:
        nx = max(left, min(x, max(left, right-width)))
        ny = max(top, min(y, max(top, bottom-height)))
        candidates.append(((nx-x)**2+(ny-y)**2, int(nx), int(ny)))
    _, x, y = min(candidates, key=lambda p:p[0])
    return x, y


def virtual_bounds(screens):
    return (min(s[0] for s in screens), min(s[1] for s in screens),
            max(s[2] for s in screens), max(s[3] for s in screens))


class DesktopSpace:
    def __init__(self, window):
        self.window = window
        self.native = os.name == 'nt'
        if self.native:
            u = self.u = ctypes.WinDLL('user32', use_last_error=True)
            u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            u.GetAncestor.restype = wintypes.HWND
            u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            u.GetWindowRect.restype = wintypes.BOOL
            u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
            u.GetCursorPos.restype = wintypes.BOOL
            u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            u.SetWindowPos.restype = wintypes.BOOL
            self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE,
                                                    wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
            u.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), self.callback_type, wintypes.LPARAM]
            u.EnumDisplayMonitors.restype = wintypes.BOOL

    def handle(self):
        return self.u.GetAncestor(self.window.winfo_id(), 2) or self.window.winfo_id()

    def screens(self):
        result = []
        if self.native:
            @self.callback_type
            def collect(monitor, dc, rect, data):
                r = rect.contents
                result.append((r.left,r.top,r.right,r.bottom))
                return True
            self.u.EnumDisplayMonitors(None,None,collect,0)
        return sorted(result) or [(0,0,self.window.winfo_screenwidth(),self.window.winfo_screenheight())]

    def cursor(self):
        if self.native:
            point=wintypes.POINT()
            if self.u.GetCursorPos(ctypes.byref(point)):
                return point.x, point.y
        return self.window.winfo_pointerxy()

    def workareas(self):
        if not self.native:return self.screens()
        class MONITORINFO(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD)]
        areas=[]
        self.u.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MONITORINFO)]
        @self.callback_type
        def collect(monitor,dc,rect,data):
            info=MONITORINFO();info.cbSize=ctypes.sizeof(info)
            if self.u.GetMonitorInfoW(monitor,ctypes.byref(info)):
                r=info.rcWork;areas.append((r.left,r.top,r.right,r.bottom))
            return True
        self.u.EnumDisplayMonitors(None,None,collect,0)
        return sorted(areas) or self.screens()

    def position(self):
        if self.native:
            rect=wintypes.RECT()
            if self.u.GetWindowRect(self.handle(),ctypes.byref(rect)):
                return rect.left, rect.top
        return self.window.winfo_x(),self.window.winfo_y()

    def move(self,x,y):
        if self.native:
            # Do not use Tk's negative geometry offsets: they mean distance from
            # the right/bottom edge rather than a negative desktop coordinate.
            if not self.u.SetWindowPos(self.handle(),None,int(x),int(y),0,0,0x0015):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            self.window.geometry(f'+{int(x)}+{int(y)}')
