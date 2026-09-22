"""Real Tk/Windows IME geometry checks; not a substitute for typing with the IME."""
import os
import ctypes
import tkinter as tk
from tkinter import ttk
import unittest
import time
from ime_entry import attach, LOGFONT, COMPOSITIONFORM

@unittest.skipUnless(os.name=='nt','Windows IME')
class ImeEntryTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk(); self.root.geometry('420x160')
    def tearDown(self): self.root.destroy()

    def entry(self,widget=tk.Entry,size=15):
        entry=widget(self.root,font=('맑은 고딕',size))
        entry.pack(fill='x',padx=16,ipady=11)
        entry.insert(0,'급여명세서 찾아줘'); entry.icursor('end')
        entry.focus_force(); self.root.update()
        return entry,attach(entry)

    def test_character_caret_not_grid_bbox(self):
        entry,ime=self.entry()
        _,_,x,y=ime.geometry()
        bx,by,bw,bh=map(int,entry.tk.call(entry._w,'bbox',entry.index('end')-1))
        self.assertEqual((x,y),(bx+bw,by))
        self.assertGreater(x,40); self.assertGreater(y,0)
        entry.icursor(2)
        _,_,x,y=ime.geometry()
        bx,by,*_=map(int,entry.tk.call(entry._w,'bbox',2))
        self.assertEqual((x,y),(bx,by))

    def test_font_and_position_roundtrip(self):
        for widget,size in ((tk.Entry,15),(ttk.Entry,16),(tk.Entry,-24)):
            entry,ime=self.entry(widget,size)
            self.assertTrue(ime.sync())
            hwnd,height,family,x,y=ime.last_applied
            context=ime.imm.ImmGetContext(hwnd)
            try:
                font=LOGFONT(); position=COMPOSITIONFORM()
                self.assertTrue(ime.imm.ImmGetCompositionFontW(context,ctypes.byref(font)))
                self.assertTrue(ime.imm.ImmGetCompositionWindow(context,ctypes.byref(position)))
                self.assertEqual((font.height,font.face),(height,family))
                self.assertEqual((position.point.x,position.point.y),(x,y))
                if size<0:self.assertEqual(height,size)
            finally:ime.imm.ImmReleaseContext(hwnd,context)
            entry.destroy(); self.root.update()

    def test_long_text_caret_stays_inside_entry(self):
        entry,ime=self.entry()
        entry.insert('end',' 긴 이름의 파일을 찾아줘'*20)
        entry.icursor('end'); entry.xview_moveto(1); self.root.update()
        _,_,x,y=ime.geometry()
        self.assertGreater(x,2); self.assertLess(x,entry.winfo_width())

    def test_submit_once_and_no_callback_after_close(self):
        entry,ime=self.entry(); submitted=[]
        ime.commit_then(lambda:submitted.append(entry.get()))
        ime.commit_then(lambda:submitted.append('duplicate'))
        self.root.update();time.sleep(.04);self.root.update()
        self.assertEqual(submitted,['급여명세서 찾아줘'])
        ime.commit_then(lambda:submitted.append('closed'))
        entry.destroy(); self.root.update()
        self.assertEqual(submitted,['급여명세서 찾아줘'])

    def test_final_composition_message_is_included(self):
        entry,ime=self.entry();entry.delete(0,'end');entry.insert(0,'급여명세')
        values=[];ime.commit_then(lambda:values.append(entry.get()))
        entry.after(0,lambda:entry.insert('end','서'))
        self.root.update();time.sleep(.04);self.root.update()
        self.assertEqual(values,['급여명세서'])

    def test_font_geometry_at_display_scales(self):
        from accessibility import apply_fonts
        entry,ime=self.entry()
        for factor in (1,1.25,1.5,2):
            apply_fonts(entry,factor);self.root.update()
            _,height,x,y=ime.geometry()
            self.assertGreater(height,12*factor)
            self.assertGreaterEqual(x,2);self.assertLess(x,entry.winfo_width())
            self.assertTrue(ime.sync())

if __name__=='__main__': unittest.main()
