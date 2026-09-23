"""Tk tests with synthetic .eml input; never a network login or OS clipboard."""
import gc
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mail_connections_ui import show_mail_connections
from mail_store import MailStore
from mail_sync import ImportResult
from test_mail_import import message


class MailConnectionsUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jjanggu-mail-ui-')
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args:self.errors.append(args)
        self.changed = 0
        self.shown = 0
        self.app = SimpleNamespace(root=self.root, data=Path(self.temp.name)/'data',settings={'text_scale':1.3},
                                   mail_attachments_changed=self.on_changed,show_mail_attachments=self.on_show)
        self.win = show_mail_connections(self.app)
        self.ui = self.win.mail_connections
        self.win.geometry('780x560')
        self.root.update()

    def on_changed(self): self.changed += 1
    def on_show(self): self.shown += 1

    def pump(self, condition=lambda:True, seconds=5):
        until = time.monotonic()+seconds
        while time.monotonic()<until:
            self.root.update()
            if condition():return
            time.sleep(.01)
        self.fail('UI worker did not finish')

    def tearDown(self):
        if not self.ui.closed:self.ui.close()
        self.root.update()
        self.root.destroy()
        gc.collect()
        self.temp.cleanup()
        self.assertFalse(self.errors,self.errors)

    def test_window_reuse_and_primary_buttons_visible_at_large_text(self):
        self.assertIs(show_mail_connections(self.app),self.win)
        for button in (self.ui.connect_button,self.ui.stop_button):
            self.assertTrue(button.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_rooty(),self.win.winfo_rooty())
            self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),self.win.winfo_rooty()+self.win.winfo_height())
        self.ui.files_button.invoke()
        self.assertEqual(self.shown,1)

    def test_eml_import_completes_and_notifies_main_search(self):
        path = Path(self.temp.name)/'받은 메일.eml'
        path.write_bytes(message(('급여명세서.txt','합성 급여명세서'.encode())))
        with patch('mail_connections_ui.filedialog.askopenfilenames',return_value=(str(path),)):
            self.ui.import_button.invoke()
        self.pump(lambda:not self.ui.busy)
        self.assertEqual(self.changed,1)
        self.assertEqual(self.ui.store.attachment_count(),1)
        self.assertIn('1개',self.ui.status.cget('text'))
        self.assertEqual(self.ui.password.get(),'')
        self.assertFalse(self.ui.remember.get())

    def test_outlook_does_not_offer_basic_login_and_keeps_eml_import(self):
        self.ui.provider.current(self.ui.providers.index('outlook'))
        self.ui.provider_changed()
        self.assertEqual(str(self.ui.connect_button.cget('state')),'disabled')
        self.assertEqual(str(self.ui.password.cget('state')),'disabled')
        self.assertEqual(str(self.ui.import_button.cget('state')),'normal')
        self.assertIn('아직 지원하지',self.ui.note.cget('text'))

    def test_connect_secret_is_cleared_and_default_storage_off(self):
        captured = []
        def fake(data,request,replies,cancelled):
            captured.append(dict(request))
            replies.put((ImportResult(),None))
        self.ui.address.insert(0,'fixture@naver.com')
        self.ui.password.insert(0,'synthetic password')
        with patch('mail_connections_ui._worker',side_effect=fake):
            self.ui.connect_button.invoke()
            self.pump(lambda:not self.ui.busy)
        self.assertEqual(captured[0]['secret'],'syntheticpassword')
        self.assertFalse(captured[0]['remember'])
        self.assertEqual(self.ui.password.get(),'')

    def test_close_cancels_worker_and_committed_files_still_notify(self):
        release = threading.Event()
        finished = threading.Event()
        def fake(data,request,replies,cancelled):
            release.wait(3)
            replies.put((ImportResult(files=['synthetic-path'],cancelled=cancelled.is_set()),None))
            finished.set()
        with patch('mail_connections_ui._worker',side_effect=fake):
            self.ui.start(dict(kind='eml',paths=[]))
            self.ui.close()
            self.assertTrue(self.ui.cancelled.is_set())
            self.assertIsNone(self.ui.remember)
            release.set()
            self.assertTrue(finished.wait(3))
            self.pump(lambda:not self.ui.busy)
        self.assertEqual(self.changed,1)
        self.assertIsNone(self.app.mail_connections_window)

    def test_disconnect_preserves_cache_clear_requires_explicit_click(self):
        profile = self.ui.store.save_profile('naver','fixture@naver.com')
        paths = self.ui.store.add_message(profile['id'],'fixture',{},[('명세서.txt',b'fixture')])
        self.ui.refresh_accounts(profile['id'])
        self.ui.disconnect_button.invoke()
        self.assertTrue(Path(paths[0]).exists())
        self.assertFalse(self.ui.store.profile(profile['id'])['enabled'])
        with patch('mail_connections_ui.messagebox.askyesno',return_value=False):
            self.ui.clear_button.invoke()
        self.assertTrue(Path(paths[0]).exists())
        with patch('mail_connections_ui.messagebox.askyesno',return_value=True):
            self.ui.clear_button.invoke()
        self.assertFalse(Path(paths[0]).exists())
        self.assertEqual(self.changed,1)


if __name__=='__main__':unittest.main()
