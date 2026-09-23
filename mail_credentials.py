"""Optional current-Windows-user DPAPI storage; no plaintext fallback."""
import ctypes
from ctypes import wintypes
import os


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('비밀번호 저장은 Windows에서만 사용할 수 있어요.')
    if not isinstance(data, bytes) or len(data) > 65536:
        raise ValueError('저장된 로그인 정보를 확인할 수 없어요.')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    source_buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN, current-user scope; never LOCAL_MACHINE.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError('Windows에서 로그인 정보를 보호하지 못했어요. 저장하지 않고 연결해 주세요.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(output.data)
        ctypes.memset(source_buffer, 0, len(data))


def protect(secret):
    return _crypt(secret.encode('utf-8'))


def unprotect(value):
    try:
        return _crypt(value, decrypt=True).decode('utf-8')
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError('저장한 비밀번호를 읽을 수 없어요. 다시 입력해 주세요.') from error
