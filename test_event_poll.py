"""A failed UI callback must not strand search completions or background scans."""
from pathlib import Path
import queue
import shutil
import tkinter as tk
import unittest
import uuid

from app import App


class Root:
    def __init__(self):
        self.scheduled=[]
        self.closed=False

    def after(self,delay,callback):
        if self.closed:raise tk.TclError('application has been destroyed')
        self.scheduled.append((delay,callback))


class EventPollTests(unittest.TestCase):
    def setUp(self):
        self.base=(Path(__file__).parent/'.local/tests').resolve()
        self.base.mkdir(parents=True,exist_ok=True)
        self.temporary=self.base/('jjanggu-event-poll-'+uuid.uuid4().hex)
        self.temporary.mkdir()
        self.app=App.__new__(App)
        self.app.data=self.temporary
        self.app.root=Root()
        self.app.events=queue.Queue()

    def tearDown(self):
        self.assertTrue(self.temporary.resolve().is_relative_to(self.base))
        shutil.rmtree(self.temporary)

    def test_bad_callback_does_not_strand_photo_search_completion(self):
        self.app.busy=True
        self.app.photo_searching=True
        completed=[]
        def stale_widget():raise tk.TclError('invalid command name: closed result window')
        def finish():
            self.app.busy=False
            self.app.photo_searching=False
            completed.append('results delivered')
        self.app.events.put(stale_widget)
        self.app.events.put(finish)
        self.app.poll()
        self.assertEqual(completed,['results delivered'])
        self.assertFalse(self.app.busy)
        self.assertFalse(self.app.photo_searching)
        self.assertEqual(len(self.app.root.scheduled),1)
        log=(self.app.data/'app-errors.log').read_text('utf-8')
        self.assertIn('TclError',log)
        self.assertIn('closed result window',log)
        self.app.events.put(lambda:completed.append('next poll'))
        self.app.root.scheduled[0][1]()
        self.assertEqual(completed[-1],'next poll')

    def test_continuous_progress_yields_to_tk_and_finishes_next_batch(self):
        seen=[]
        for number in range(100):self.app.events.put(lambda n=number:seen.append(n))
        self.app.poll()
        self.assertEqual(seen,list(range(64)))
        self.assertEqual(self.app.root.scheduled[0][0],10)
        self.app.root.scheduled[0][1]()
        self.assertEqual(seen,list(range(100)))
        self.assertEqual(self.app.root.scheduled[-1][0],100)

    def test_unwritable_error_log_does_not_stop_results(self):
        self.app.data=self.app.data/'missing-parent'/'missing-directory'
        completed=[]
        def broken():raise ValueError('simulated callback failure')
        self.app.events.put(broken)
        self.app.events.put(lambda:completed.append(True))
        self.app.poll()
        self.assertEqual(completed,[True])
        self.assertEqual(len(self.app.root.scheduled),1)

    def test_last_callback_can_close_app_without_reschedule_failure(self):
        self.app.events.put(lambda:setattr(self.app.root,'closed',True))
        self.app.poll()
        self.assertFalse(self.app.root.scheduled)


if __name__=='__main__':unittest.main()
