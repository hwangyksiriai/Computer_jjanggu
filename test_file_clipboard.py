"""No desktop access: Win32 clipboard and window operations are fully mocked."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import windows_features as windows


class Function:
    def __init__(self,implementation):self.implementation=implementation
    def __call__(self,*args):return self.implementation(*args)


class ClipboardAPI:
    OWNER=0x123456789
    HIDDEN=0x234567890
    EFFECT=0xC123

    def __init__(self,fail=None):
        self.fail=fail
        self.calls=[];self.buffers={};self.allocated=[];self.freed=[]
        self.transferred=set();self.system_freed=[];self.locked=set()
        self.formats={'previous':b'synthetic previous value'}
        self.counts={};self.prepared=None
        self.kernel=SimpleNamespace(**{name:Function(getattr(self,name)) for name in
            ('GlobalAlloc','GlobalLock','GlobalUnlock','GlobalFree','GetModuleHandleW')})
        self.user=SimpleNamespace(**{name:Function(getattr(self,name)) for name in
            ('RegisterClipboardFormatW','IsWindow','OpenClipboard','EmptyClipboard',
             'SetClipboardData','CloseClipboard','CreateWindowExW','DestroyWindow')})

    def call(self,name,*args):
        self.calls.append((name,args))
        self.counts[name]=self.counts.get(name,0)+1
        return self.fail in (name,(name,self.counts[name]))

    def GlobalAlloc(self,flags,size):
        if self.call('GlobalAlloc',flags,size):return 0
        assert flags & 0x2,'clipboard allocation must be GMEM_MOVEABLE'
        handle=0x300000000+len(self.allocated)
        self.allocated.append(handle);self.buffers[handle]=ctypes.create_string_buffer(size)
        return handle

    def GlobalLock(self,handle):
        if self.call('GlobalLock',handle):return 0
        self.locked.add(handle)
        return ctypes.addressof(self.buffers[handle])

    def GlobalUnlock(self,handle):
        if self.call('GlobalUnlock',handle):
            ctypes.set_last_error(6)
            return 0
        self.locked.discard(handle)
        return 0  # The usual successful final unlock, not an error.

    def GlobalFree(self,handle):
        self.call('GlobalFree',handle)
        assert handle not in self.transferred,'application must not free Windows-owned memory'
        assert handle not in self.freed,'must not double-free memory'
        self.freed.append(handle);self.buffers.pop(handle);self.locked.discard(handle)
        return 0

    def GetModuleHandleW(self,name):self.call('GetModuleHandleW',name);return 0x345678901

    def RegisterClipboardFormatW(self,name):
        return 0 if self.call('RegisterClipboardFormatW',name) else self.EFFECT

    def IsWindow(self,handle):self.call('IsWindow',handle);return handle==self.OWNER

    def OpenClipboard(self,handle):
        assert handle in (self.OWNER,self.HIDDEN),'a non-NULL owner is required'
        return not self.call('OpenClipboard',handle)

    def EmptyClipboard(self):
        if self.call('EmptyClipboard'):return 0
        for handle in self.transferred:
            self.system_freed.append(handle);self.buffers.pop(handle)
        self.transferred.clear();self.formats.clear()
        return 1

    def SetClipboardData(self,format_id,handle):
        if self.call('SetClipboardData',format_id,handle):return 0
        assert handle not in self.locked,'clipboard handles must be unlocked'
        self.formats[format_id]=bytes(self.buffers[handle])
        self.transferred.add(handle)
        return handle

    def CloseClipboard(self):return not self.call('CloseClipboard')

    def CreateWindowExW(self,*args):
        if self.call('CreateWindowExW',*args):return 0
        if self.prepared:self.prepared()
        return self.HIDDEN

    def DestroyWindow(self,handle):self.call('DestroyWindow',handle);return 1


class FileClipboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.first=self.root/'급여 명세서 {6월} 😀.txt'
        self.second=self.root/'file with spaces.txt'
        self.first.write_bytes(b'first original');self.second.write_bytes(b'second original')
        self.before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in (self.first,self.second)}

    def copy(self,api,paths=None,owner=None):
        with patch.object(windows,'_clipboard_api',return_value=(api.kernel,api.user)):
            return windows.copy_files(paths if paths is not None else [self.first,self.second],owner)

    def assert_originals_unchanged(self):
        for path,(content,mtime) in self.before.items():
            self.assertEqual(path.read_bytes(),content)
            self.assertEqual(path.stat().st_mtime_ns,mtime)

    def assert_all_released(self,api):
        self.assertEqual(set(api.allocated),set(api.freed)|set(api.system_freed))
        self.assertFalse(set(api.freed)&set(api.system_freed))

    def test_unicode_file_payload_and_copy_intent_are_complete_and_read_only(self):
        api=ClipboardAPI()
        result=self.copy(api,[self.first,str(self.first),self.second])
        self.assertIsInstance(result,tuple)
        self.assertEqual(result,(str(self.first.resolve()),str(self.second.resolve())))
        data=api.formats[15]
        self.assertEqual(struct.unpack('<IiiII',data[:20]),(20,0,0,0,1))
        names=data[20:].decode('utf-16-le')
        self.assertEqual(names,'\0'.join(result)+'\0\0')
        self.assertEqual(api.formats[api.EFFECT],struct.pack('<I',1))
        self.assertEqual([args[0] for name,args in api.calls if name=='SetClipboardData'],[api.EFFECT,15])
        self.assertEqual(api.freed,[])
        self.assertEqual(len(api.transferred),2)
        self.assertEqual(api.counts['CloseClipboard'],1)
        self.assertEqual(api.calls[-1],('DestroyWindow',(api.HIDDEN,)))
        created=next(args for name,args in api.calls if name=='CreateWindowExW')
        self.assertEqual(created[1],'STATIC')
        self.assertEqual(created[3]&0x10000000,0)  # No WS_VISIBLE.
        self.assertEqual(created[8].value,wintypes.HWND(-3).value)  # HWND_MESSAGE.
        self.assert_originals_unchanged()

    def test_explicit_64_bit_owner_is_used_without_creating_or_destroying_a_window(self):
        api=ClipboardAPI()
        self.copy(api,owner=wintypes.HWND(api.OWNER))
        self.assertIn(('OpenClipboard',(api.OWNER,)),api.calls)
        self.assertNotIn('CreateWindowExW',api.counts)
        self.assertNotIn('DestroyWindow',api.counts)

    def test_single_path_and_relative_path_are_supported(self):
        api=ClipboardAPI()
        relative=os.path.relpath(self.first,Path.cwd())
        result=self.copy(api,relative)
        self.assertEqual(result,(str(self.first.resolve()),))

    def test_any_invalid_selection_fails_before_native_clipboard_access(self):
        missing=self.root/'missing.txt'
        for paths in ([],[self.first,missing],[self.first,self.root],
                      [self.first,None],[self.first,''],[self.first,'bad\0path'],
                      [self.first,'https://example.invalid/file.pdf']):
            with self.subTest(paths=paths),patch.object(windows,'_clipboard_api') as native:
                with self.assertRaises(ValueError):windows.copy_files(paths)
                native.assert_not_called()
        self.assert_originals_unchanged()

    def test_generator_selection_cannot_silently_skip_a_missing_last_file(self):
        paths=(p for p in (self.first,self.second,self.root/'gone.txt'))
        with patch.object(windows,'_clipboard_api') as native:
            with self.assertRaises(ValueError):windows.copy_files(paths)
            native.assert_not_called()

    def test_file_removed_during_preparation_aborts_before_emptying_clipboard(self):
        api=ClipboardAPI();api.prepared=lambda:self.second.unlink()
        with self.assertRaises(ValueError):self.copy(api)
        self.assertNotIn('OpenClipboard',api.counts)
        self.assertIn('previous',api.formats)
        self.assert_all_released(api)
        self.assertEqual(self.first.read_bytes(),self.before[self.first][0])

    def test_invalid_owner_never_changes_clipboard_and_releases_allocations(self):
        for owner in (0,-1,'123',True,123):
            with self.subTest(owner=owner):
                api=ClipboardAPI()
                with self.assertRaises(ValueError):self.copy(api,owner=owner)
                self.assertNotIn('OpenClipboard',api.counts)
                self.assert_all_released(api)

    def test_format_registration_failure_changes_nothing(self):
        api=ClipboardAPI('RegisterClipboardFormatW')
        with self.assertRaises(OSError):self.copy(api)
        self.assertEqual(api.allocated,[])
        self.assertNotIn('OpenClipboard',api.counts)
        self.assertIn('previous',api.formats)

    def test_allocation_and_lock_failures_free_every_owned_handle(self):
        for name in ('GlobalAlloc','GlobalLock'):
            for ordinal in (1,2):
                with self.subTest(name=name,ordinal=ordinal):
                    api=ClipboardAPI((name,ordinal))
                    with self.assertRaises(OSError):self.copy(api)
                    self.assertNotIn('OpenClipboard',api.counts)
                    self.assert_all_released(api)
        self.assert_originals_unchanged()

    def test_unlock_zero_with_no_error_is_success_even_if_old_error_existed(self):
        api=ClipboardAPI()
        ctypes.set_last_error(999)
        self.copy(api)
        self.assertIn(15,api.formats)

    def test_unlock_failure_frees_memory_and_never_opens_clipboard(self):
        api=ClipboardAPI(('GlobalUnlock',2))
        with self.assertRaises(OSError):self.copy(api)
        self.assertNotIn('OpenClipboard',api.counts)
        self.assert_all_released(api)

    def test_memory_copy_exception_unlocks_and_frees_all_owned_memory(self):
        api=ClipboardAPI()
        with patch.object(windows.ctypes,'memmove',side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):self.copy(api)
        self.assertEqual(api.counts.get('GlobalUnlock'),1)
        self.assert_all_released(api)

    def test_hidden_window_failure_frees_memory_without_clipboard_access(self):
        api=ClipboardAPI('CreateWindowExW')
        with self.assertRaises(OSError):self.copy(api)
        self.assertNotIn('OpenClipboard',api.counts)
        self.assertNotIn('DestroyWindow',api.counts)
        self.assert_all_released(api)

    def test_busy_clipboard_preserves_previous_value_and_cleans_up(self):
        api=ClipboardAPI('OpenClipboard')
        with self.assertRaises(OSError):self.copy(api)
        self.assertNotIn('EmptyClipboard',api.counts)
        self.assertNotIn('CloseClipboard',api.counts)
        self.assertEqual(api.counts['DestroyWindow'],1)
        self.assertIn('previous',api.formats)
        self.assert_all_released(api)

    def test_empty_failure_never_publishes_and_still_closes_clipboard(self):
        api=ClipboardAPI(('EmptyClipboard',1))
        with self.assertRaises(OSError):self.copy(api)
        self.assertNotIn('SetClipboardData',api.counts)
        self.assertEqual(api.counts['CloseClipboard'],1)
        self.assertIn('previous',api.formats)
        self.assert_all_released(api)

    def test_set_failure_clears_partial_formats_without_double_free(self):
        for ordinal in (1,2):
            with self.subTest(ordinal=ordinal):
                api=ClipboardAPI(('SetClipboardData',ordinal))
                with self.assertRaises(OSError):self.copy(api)
                self.assertEqual(api.formats,{})
                self.assertEqual(api.counts['CloseClipboard'],1)
                self.assert_all_released(api)
                self.assertEqual(len(api.system_freed),ordinal-1)
        self.assert_originals_unchanged()

    def test_rollback_failure_still_cannot_leave_file_paths_without_copy_intent(self):
        api=ClipboardAPI(('SetClipboardData',2))
        original=api.user.EmptyClipboard
        def empty():
            if api.counts.get('EmptyClipboard')==1:
                api.call('EmptyClipboard');return 0
            return original()
        api.user.EmptyClipboard=empty
        with self.assertRaises(OSError):self.copy(api)
        self.assertNotIn(15,api.formats)
        self.assertEqual(api.formats[api.EFFECT],struct.pack('<I',1))
        self.assertEqual(len(api.freed),1)
        self.assertEqual(len(api.transferred),1)

    def test_close_failure_is_reported_without_freeing_transferred_memory(self):
        api=ClipboardAPI('CloseClipboard')
        with self.assertRaises(OSError):self.copy(api)
        self.assertEqual(api.freed,[])
        self.assertEqual(len(api.transferred),2)
        self.assertEqual(api.counts['DestroyWindow'],1)

    def test_dll_signatures_keep_handles_and_allocations_pointer_sized(self):
        api=ClipboardAPI()
        with patch.object(windows.ctypes,'WinDLL',side_effect=[api.kernel,api.user]) as load:
            kernel,user=windows._clipboard_api()
        self.assertEqual(load.call_count,2)
        self.assertTrue(all(call.kwargs.get('use_last_error') for call in load.call_args_list))
        self.assertEqual(kernel.GlobalAlloc.argtypes,[wintypes.UINT,ctypes.c_size_t])
        self.assertIs(kernel.GlobalLock.restype,ctypes.c_void_p)
        for function in (kernel.GlobalAlloc,kernel.GlobalFree,user.SetClipboardData,user.CreateWindowExW):
            self.assertEqual(ctypes.sizeof(function.restype),ctypes.sizeof(ctypes.c_void_p))
        self.assertEqual(user.OpenClipboard.argtypes,[wintypes.HWND])
        self.assertEqual(user.DestroyWindow.argtypes,[wintypes.HWND])


if __name__=='__main__':unittest.main()
