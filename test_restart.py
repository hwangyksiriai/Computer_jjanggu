"""Restart must never interrupt an active file operation."""
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from app import App

class RestartTests(unittest.TestCase):
    def test_waits_for_work_and_only_requests_one_exit(self):
        root=tk.Tk(); root.withdraw(); exits=[]
        app=SimpleNamespace(root=root,restarting=False,restart_requested=False,
                            busy=True,indexing=True,index_cancel=threading.Event(),
                            status=tk.StringVar(root),quit=lambda:exits.append(True))
        try:
            App.restart(app); App.restart(app)
            self.assertTrue(app.index_cancel.is_set()); self.assertFalse(exits)
            app.busy=False; root.update(); self.assertFalse(exits)
            app.indexing=False
            deadline=time.monotonic()+1
            while not exits and time.monotonic()<deadline:
                root.update(); time.sleep(.02)
            self.assertEqual(exits,[True]); self.assertTrue(app.restart_requested)
        finally:root.destroy()

if __name__=='__main__':unittest.main()
