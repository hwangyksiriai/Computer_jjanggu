"""Move one confirmed local photo to Windows Recycle Bin, never unlink it.

``recycle_photo(row)`` is synchronous; call it in the application's worker. It
returns {ok, cancelled, error, path, recycled_path}. Only ``ok`` authorizes the
caller to remove a catalogue entry. No catalogue or original contents are edited.

Microsoft's IFileOperation uses shell items (not glob strings). RECYCLEONDELETE
requests recycling; PreDeleteItem rejects a permanent-delete operation and checks
the original again. PostDeleteItem must supply the newly recycled shell item.
https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags
https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperationprogresssink-predeleteitem
https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperationprogresssink-postdeleteitem
"""
import ctypes
import math
import ntpath
import os
from pathlib import Path
import stat
import sys
import threading
import uuid

PHOTO_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp', '.gif',
                    '.tif', '.avif', '.heic', '.heif', '.dng', '.cr2', '.nef', '.arw'}
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_NOERRORUI = 0x0400
FOF_NORECURSION = 0x1000
FOFX_RECYCLEONDELETE = 0x00080000
FOFX_EARLYFAILURE = 0x00100000
FOFX_ADDUNDORECORD = 0x20000000
RECYCLE_FLAGS = (FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_NORECURSION |
                 FOFX_RECYCLEONDELETE | FOFX_EARLYFAILURE | FOFX_ADDUNDORECORD)
TSF_DELETE_RECYCLE_IF_POSSIBLE = 0x80
E_ABORT = -2147467260
HRESULT = ctypes.c_int32
DWORD = ctypes.c_uint32
PTR = ctypes.c_void_p
_CANCEL_CODES = {0x80004004, 0x800704C7, 0x80270000}


class RecycleError(Exception):
    def __init__(self, message, *, cancelled=False):
        super().__init__(message)
        self.cancelled = cancelled


def _result(path='', *, ok=False, cancelled=False, error='', recycled_path=''):
    return dict(ok=ok, cancelled=cancelled, error=error, path=str(path),
                recycled_path=recycled_path)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _safe_path(value):
    if not isinstance(value, str) or not value or '\0' in value or '*' in value or '?' in value:
        raise RecycleError('삭제할 사진의 경로가 올바르지 않아요.')
    drive, tail = ntpath.splitdrive(value)
    if len(drive) != 2 or drive[1] != ':' or not drive[0].isalpha() or not tail.startswith(('\\', '/')):
        raise RecycleError('이 PC의 로컬 드라이브에 있는 사진만 휴지통으로 보낼 수 있어요.')
    if ':' in tail or any(part == '..' or part.endswith((' ', '.')) for part in tail.replace('/', '\\').split('\\') if part):
        raise RecycleError('이 경로의 사진은 안전하게 삭제할 수 없어요.')
    path = Path(ntpath.normpath(value))
    if path.suffix.lower() not in PHOTO_EXTENSIONS:
        raise RecycleError('사진 파일만 휴지통으로 보낼 수 있어요.')
    return path


def _validate_photo(row, expected=None):
    path = _safe_path(row.get('path'))
    # lstat each component before resolving anything: junctions/symlinks/cloud
    # placeholders must never redirect the single target to another location.
    for item in reversed((path, *path.parents)):
        info = item.lstat()
        attributes = getattr(info, 'st_file_attributes', 0)
        if stat.S_ISLNK(info.st_mode) or attributes & 0x400:
            raise RecycleError('바로가기·연결 폴더나 클라우드 연결 사진은 여기서 삭제하지 않아요. 저장 폴더에서 확인해 주세요.')
        if attributes & (0x1000 | 0x40000 | 0x400000):
            raise RecycleError('PC에 완전히 내려받지 않은 사진은 먼저 다운로드해 주세요.')
    if not stat.S_ISREG(info.st_mode):
        raise RecycleError('일반 사진 파일만 휴지통으로 보낼 수 있어요. 폴더는 삭제하지 않아요.')
    for key, actual in (('mtime', info.st_mtime), ('size', info.st_size)):
        value = row.get(key)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or not math.isfinite(value) or value != actual):
            raise RecycleError('검색한 뒤 사진이 변경됐어요. 다시 검색한 다음 삭제해 주세요.')
    if expected is not None and _identity(info) != expected:
        raise RecycleError('삭제를 준비하는 동안 사진이 변경됐어요. 다시 검색해 주세요.')
    return path, _identity(info)


def _check_hr(value, action):
    code = int(value) & 0xFFFFFFFF
    if code & 0x80000000:
        cancelled = code in _CANCEL_CODES
        message = ('휴지통으로 보내기가 취소됐어요.' if cancelled else
                   f'{action}하지 못했어요. 파일이 사용 중이거나 휴지통을 사용할 수 없을 수 있어요. (0x{code:08X})')
        raise RecycleError(message, cancelled=cancelled)


class GUID(ctypes.Structure):
    _fields_ = [('Data1', DWORD), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16),
                ('Data4', ctypes.c_ubyte * 8)]

    @classmethod
    def from_text(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _method(pointer, index, result=HRESULT, args=()):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(PTR))).contents
    return ctypes.WINFUNCTYPE(result, PTR, *args)(table[index])


def _call(pointer, index, *values, args=(), result=HRESULT):
    return _method(pointer, index, result, args)(pointer, *values)


def _shell_path(item, ole32):
    text = PTR()
    hr = _call(item, 5, DWORD(0x80058000), ctypes.byref(text), args=(DWORD, ctypes.POINTER(PTR)))
    _check_hr(hr, '휴지통 위치를 확인')
    if not text.value:
        raise RecycleError('휴지통 위치를 확인하지 못했어요.')
    try:
        return ctypes.wstring_at(text)
    finally:
        ole32.CoTaskMemFree(text)


class _RecycleSink:
    """Keep all COM callbacks alive until IFileOperation has released them."""
    def __init__(self, row, expected, ole32):
        self.references = 1
        self.row, self.expected, self.ole32 = row, expected, ole32
        self.blocked = ''
        self.pre_called = False
        self.post_called = False
        self.recycled_path = ''
        self.delete_hr = None
        self.callbacks = []
        qi_ids = {uuid.UUID(value).bytes_le for value in (
            '00000000-0000-0000-C000-000000000046', '04b0f1a7-9490-44bc-96e1-4296a31252e2')}

        def query(this, iid, output):
            if iid and output and ctypes.string_at(iid, 16) in qi_ids:
                output[0] = this
                self.references += 1
                return 0
            if output:
                output[0] = None
            return -2147467262  # E_NOINTERFACE

        def add_ref(this):
            self.references += 1
            return self.references

        def release(this):
            self.references = max(0, self.references - 1)
            return self.references

        def pre_delete(this, flags, item):
            self.pre_called = True
            try:
                if not flags & TSF_DELETE_RECYCLE_IF_POSSIBLE:
                    raise RecycleError('이 사진은 휴지통을 사용할 수 없어 삭제하지 않았어요.')
                target = _shell_path(item, self.ole32)
                if ntpath.normcase(ntpath.normpath(target)) != ntpath.normcase(ntpath.normpath(self.row['path'])):
                    raise RecycleError('선택한 사진과 삭제 대상이 달라 작업을 중단했어요.')
                _validate_photo(self.row, self.expected)
                return 0
            except Exception as exc:
                self.blocked = str(exc)
                return E_ABORT

        def post_delete(this, flags, item, hr, recycled):
            self.post_called = True
            self.delete_hr = hr
            try:
                _check_hr(hr, '휴지통으로 이동')
                if not recycled:
                    raise RecycleError('휴지통으로 이동했는지 확인할 수 없어요. 원본 위치와 휴지통을 확인해 주세요.')
                self.recycled_path = _shell_path(recycled, self.ole32)
                return 0
            except Exception as exc:
                self.blocked = str(exc)
                return E_ABORT

        def callback(result, args, function):
            value = ctypes.WINFUNCTYPE(result, PTR, *args)(function)
            self.callbacks.append(value)
            return ctypes.cast(value, PTR).value

        noop = lambda *args: 0
        methods = [
            callback(HRESULT, (PTR, ctypes.POINTER(PTR)), query),
            callback(DWORD, (), add_ref), callback(DWORD, (), release),
            callback(HRESULT, (), noop), callback(HRESULT, (HRESULT,), noop),
            callback(HRESULT, (DWORD, PTR, ctypes.c_wchar_p), noop),
            callback(HRESULT, (DWORD, PTR, ctypes.c_wchar_p, HRESULT, PTR), noop),
            callback(HRESULT, (DWORD, PTR, PTR, ctypes.c_wchar_p), noop),
            callback(HRESULT, (DWORD, PTR, PTR, ctypes.c_wchar_p, HRESULT, PTR), noop),
            callback(HRESULT, (DWORD, PTR, PTR, ctypes.c_wchar_p), noop),
            callback(HRESULT, (DWORD, PTR, PTR, ctypes.c_wchar_p, HRESULT, PTR), noop),
            callback(HRESULT, (DWORD, PTR), pre_delete),
            callback(HRESULT, (DWORD, PTR, HRESULT, PTR), post_delete),
            callback(HRESULT, (DWORD, PTR, ctypes.c_wchar_p), noop),
            callback(HRESULT, (DWORD, PTR, ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD, HRESULT, PTR), noop),
            callback(HRESULT, (DWORD, DWORD), noop),
            callback(HRESULT, (), noop), callback(HRESULT, (), noop), callback(HRESULT, (), noop),
        ]
        self.vtable = (PTR * len(methods))(*methods)
        self.instance = (PTR * 1)(ctypes.cast(self.vtable, PTR).value)
        self.pointer = ctypes.cast(self.instance, PTR)


def _check_volume(path):
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = DWORD
    if kernel32.GetDriveTypeW(path.anchor) != 3:  # DRIVE_FIXED; reject removable/network/RAM/etc.
        raise RecycleError('외장·네트워크 드라이브는 여기서 삭제하지 않아요. 저장 폴더에서 확인해 주세요.')
    class RecycleInfo(ctypes.Structure):
        _fields_ = [('cbSize', DWORD), ('i64Size', ctypes.c_int64), ('i64NumItems', ctypes.c_int64)]
    shell32 = ctypes.WinDLL('shell32')
    shell32.SHQueryRecycleBinW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(RecycleInfo)]
    shell32.SHQueryRecycleBinW.restype = HRESULT
    info = RecycleInfo()
    info.cbSize = ctypes.sizeof(info)
    _check_hr(shell32.SHQueryRecycleBinW(path.anchor, ctypes.byref(info)), '이 드라이브의 휴지통을 확인')


def _recycle_windows(row):
    path, expected = _validate_photo(row)
    _check_volume(path)
    ole32 = ctypes.WinDLL('ole32')
    ole32.CoInitializeEx.argtypes = [PTR, DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), PTR, DWORD, ctypes.POINTER(GUID), ctypes.POINTER(PTR)]
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CoTaskMemFree.argtypes = [PTR]
    ole32.CoTaskMemFree.restype = None
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    _check_hr(ole32.CoInitializeEx(None, 2), 'Windows 휴지통을 준비')  # COINIT_APARTMENTTHREADED
    operation, item = PTR(), PTR()
    sink = None
    try:
        clsid = GUID.from_text('3ad05575-8857-4850-9277-11b85bdb8e09')
        iid = GUID.from_text('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')
        _check_hr(ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(operation)), '휴지통 작업을 준비')
        _check_hr(_call(operation, 5, DWORD(RECYCLE_FLAGS), args=(DWORD,)), '휴지통 전용 옵션을 설정')
        shell32 = ctypes.WinDLL('shell32')
        shell32.SHCreateItemFromParsingName.argtypes = [ctypes.c_wchar_p, PTR, ctypes.POINTER(GUID), ctypes.POINTER(PTR)]
        shell32.SHCreateItemFromParsingName.restype = HRESULT
        shell_iid = GUID.from_text('43826d1e-e718-42ee-bc55-a1e261c37bfe')
        _check_hr(shell32.SHCreateItemFromParsingName(str(path), None, ctypes.byref(shell_iid), ctypes.byref(item)), '선택한 사진을 확인')
        sink = _RecycleSink(row, expected, ole32)
        _check_hr(_call(operation, 18, item, sink.pointer, args=(PTR, PTR)), '사진을 휴지통 작업에 등록')
        _validate_photo(row, expected)
        performed = _call(operation, 21)
        aborted = ctypes.c_int32()
        abort_status = _call(operation, 22, ctypes.byref(aborted), args=(ctypes.POINTER(ctypes.c_int32),))
        if sink.blocked:
            raise RecycleError(sink.blocked, cancelled=(sink.delete_hr is not None and (sink.delete_hr & 0xFFFFFFFF) in _CANCEL_CODES))
        _check_hr(performed, '휴지통으로 이동')
        _check_hr(abort_status, '휴지통 이동 결과를 확인')
        if aborted.value:
            raise RecycleError('휴지통으로 보내기가 취소됐어요.', cancelled=True)
        if not sink.pre_called or not sink.post_called or not sink.recycled_path:
            raise RecycleError('휴지통으로 이동했는지 확인하지 못했어요. 사진 목록을 새로 확인해 주세요.')
        # Require both the Shell's recycled item and filesystem evidence. A
        # missing source alone is never considered a successful recycle.
        if os.path.lexists(path) or not os.path.isfile(sink.recycled_path):
            raise RecycleError('휴지통 이동 결과를 확인하지 못했어요. 원본 위치와 휴지통을 확인해 주세요.')
        return _result(path, ok=True, recycled_path=sink.recycled_path)
    finally:
        if item:
            _call(item, 2, result=DWORD)
        if operation:
            _call(operation, 2, result=DWORD)
        # sink must remain live through operation.Release().
        ole32.CoUninitialize()


def recycle_photo(row):
    """Recycle one previously confirmed photo; no fallback to permanent delete.

    The caller owns user confirmation and catalogue updates. Optional mtime/size
    must match the current file. A dedicated STA thread also permits calls from
    Python worker threads that were initialized as MTA by another component.
    """
    try:
        row = dict(row)
    except (TypeError, ValueError):
        return _result(error='삭제할 사진 정보가 올바르지 않아요.')
    path = row.get('path', '')
    if os.name != 'nt' or sys.getwindowsversion().major < 6 or (sys.getwindowsversion().major == 6 and sys.getwindowsversion().minor < 2):
        return _result(path, error='사진 휴지통 기능은 Windows 8 이상에서 사용할 수 있어요.')
    results = []
    def perform():
        try:
            results.append(_recycle_windows(row))
        except RecycleError as exc:
            results.append(_result(path, cancelled=exc.cancelled, error=str(exc)))
        except FileNotFoundError:
            results.append(_result(path, error='사진이 이동되었거나 이미 삭제됐어요. 목록을 새로 확인해 주세요.'))
        except PermissionError:
            results.append(_result(path, error='사진에 접근할 수 없어요. 사용 중인 프로그램이나 폴더 권한을 확인해 주세요.'))
        except Exception as exc:
            results.append(_result(path, error=f'휴지통으로 보내지 못했어요. {exc}'))
    thread = threading.Thread(target=perform, name='photo-recycle-sta')
    thread.start()
    thread.join()
    return results[0]
