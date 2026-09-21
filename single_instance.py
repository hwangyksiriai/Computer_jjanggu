"""One desktop companion per Windows session, with a wake-up event."""
import ctypes
from ctypes import wintypes


class SingleInstance:
    def __init__(self, name='JjangguPocket.Desktop.v2'):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateEventW.restype = wintypes.HANDLE
        self.api.SetEvent.argtypes = [wintypes.HANDLE]
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.mutex = self.api.CreateMutexW(None, False, 'Local\\' + name)
        if not self.mutex: raise ctypes.WinError(ctypes.get_last_error())
        self.first = ctypes.get_last_error() != 183
        self.event = self.api.CreateEventW(None, False, False, 'Local\\' + name + '.Wake')
        if not self.event:
            self.close(); raise ctypes.WinError(ctypes.get_last_error())
        if not self.first: self.api.SetEvent(self.event)

    def requested(self):
        return self.api.WaitForSingleObject(self.event, 0) == 0

    def close(self):
        for attr in ('event', 'mutex'):
            handle = getattr(self, attr, None)
            if handle: self.api.CloseHandle(handle); setattr(self, attr, None)
