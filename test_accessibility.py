import tkinter as tk
from tkinter import font
import unittest
from unittest.mock import patch
from accessibility import apply_fonts,fit_window,scroll_page

class AccessibilityTests(unittest.TestCase):
    def setUp(self):self.root=tk.Tk()
    def tearDown(self):self.root.destroy()
    def test_window_fits_short_monitor_without_taskbar_overlap(self):
        with patch('monitors.DesktopSpace.workareas',return_value=[(0,0,800,560)]):
            width,height=fit_window(self.root,1000,720,900,620)
        self.assertLessEqual(width,776);self.assertLessEqual(height,536)
        self.root.update();self.assertLessEqual(self.root.winfo_height(),536)
    def test_enlargement_does_not_accumulate_on_refresh(self):
        label=tk.Label(self.root,text='급여명세서',font=('맑은 고딕',12));label.pack()
        for _ in range(5):apply_fonts(self.root,1.3)
        self.assertEqual(font.Font(font=label.cget('font')).actual('size'),16)
        apply_fonts(self.root,1);self.assertEqual(font.Font(font=label.cget('font')).actual('size'),12)
    def test_long_page_has_reachable_scroll_region(self):
        self.root.geometry('640x420');body=scroll_page(self.root,'white')
        for i in range(25):tk.Button(body,text=f'찾기 {i}',font=('맑은 고딕',18)).pack(fill='x')
        self.root.update();canvas=body.master
        self.assertGreater(int(canvas.bbox('all')[3]),self.root.winfo_height())
        canvas.yview_moveto(1);self.root.update();self.assertAlmostEqual(canvas.yview()[1],1,places=2)
    def test_tab_focus_scrolls_to_offscreen_button(self):
        self.root.geometry('640x420');body=scroll_page(self.root,'white')
        for i in range(25):last=tk.Button(body,text=f'찾기 {i}');last.pack(fill='x',pady=10)
        self.root.update();last.event_generate('<FocusIn>');self.root.update()
        self.assertGreater(body.master.yview()[0],0)
    def test_wheel_over_child_scrolls_page(self):
        self.root.geometry('640x420');body=scroll_page(self.root,'white')
        for i in range(25):last=tk.Button(body,text=f'찾기 {i}');last.pack(fill='x',pady=10)
        self.root.update();last.event_generate('<MouseWheel>',delta=-120);self.root.update()
        self.assertGreater(body.master.yview()[0],0)
