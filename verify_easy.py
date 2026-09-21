"""Exercise the real Tk UI against temporary, synthetic files only."""
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from unittest.mock import patch
from easy_app import EasyApp


def run():
    with tempfile.TemporaryDirectory(prefix='jjanggu-check-') as folder:
        base = Path(folder)
        data = base / 'settings'; data.mkdir()
        source = base / 'documents'; source.mkdir()
        vault = base / 'storage'
        doc = source / '연습 영수증.txt'
        doc.write_text('영수증 커피 4500원', encoding='utf-8')
        os.utime(doc, (time.time()-120, time.time()-120))
        (data / 'settings.json').write_text(json.dumps(dict(source=str(source), vault=str(vault),
            sound=False, focus_auto=False)), encoding='utf-8')
        root = tk.Tk(); root.withdraw(); root.pet_only_start=True; errors = []
        root.report_callback_exception = lambda *args: errors.append(str(args[1]))
        app = EasyApp(root, data)

        def idle():
            for _ in range(8): root.update(); time.sleep(.08)
            deadline = time.monotonic()+30
            while (app.busy or app.indexing) and time.monotonic()<deadline:
                root.update(); time.sleep(.03)
            assert not app.busy and not app.indexing, 'background job did not finish'
            assert not errors, errors

        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        try:
            idle()
            root.pet_only_start=False
            assert root.state()=='withdrawn'
            with patch.object(app.voice,'say') as speech:
                app.settings['sound']=True
                app.open_desktop_search(); root.update()
                assert app.bubble.alive() and root.state()=='withdrawn'
                assert speech.called, 'click must request a greeting'
                bubble=app.bubble
                bubble.entry.focus_force(); root.update()
                bubble.text.set('급여명세서'); bubble.entry.icursor('end'); root.update()
                assert bubble.ime.sync(), 'Windows IME font/position update failed'
                import ctypes
                from ime_entry import LOGFONT
                ime=bubble.ime; hwnd=ime.last_applied[0]
                ime.imm.ImmGetCompositionFontW.argtypes=[ctypes.c_void_p,ctypes.POINTER(LOGFONT)]
                context=ime.imm.ImmGetContext(hwnd)
                try:
                    applied=LOGFONT()
                    assert ime.imm.ImmGetCompositionFontW(context,ctypes.byref(applied))
                    assert applied.height==ime.last_applied[1] and applied.face==ime.last_applied[2]
                finally:ime.imm.ImmReleaseContext(hwnd,context)
                committed=[]; ime.commit_then(lambda:committed.append(bubble.text.get())); root.update()
                assert committed==['급여명세서']
                bubble.text.set('')
                initial_buttons=[w.cget('text') for w in widgets(bubble.win) if isinstance(w,tk.Button)]
                assert initial_buttons==['×','찾기'],initial_buttons
                app.open_desktop_search(); assert app.bubble is bubble
                bubble.submit('커피'); idle()
                assert len(bubble.rows)==1 and root.state()=='withdrawn'
                assert bubble.result_buttons
                assert [w.cget('text') for w in widgets(bubble.win) if isinstance(w,tk.Button)]==['×','찾기',doc.name]
                with patch.object(app,'open_file') as opened:
                    bubble.result_buttons[0].invoke(); opened.assert_called_once_with(str(doc))
                bubble.close(); app.open_desktop_search(); root.update()
                assert app.bubble.rows
                app.bubble.close()
            app.settings['sound']=False
            import uuid
            from single_instance import SingleInstance
            guard_name='JjangguPocket.Test.'+uuid.uuid4().hex
            first=SingleInstance(guard_name); second=SingleInstance(guard_name)
            try:
                assert first.first and not second.first and first.requested()
            finally: second.close(); first.close()
            import sys, wave
            if getattr(sys,'frozen',False):
                sample=base/'silence.wav'
                with wave.open(str(sample),'wb') as wav:
                    wav.setparams((1,2,24000,0,'NONE','not compressed')); wav.writeframes(b'\0'*4800)
                app.voice.play_clip(sample,app.settings,force=True)
                player=app.voice.process
                assert player.args[1:] == ['--play-voice']
                assert player.wait(timeout=15)==0, 'audio child process must finish without creating another app'
            assert list(app.nav) == ['home', 'organize', 'history', 'more']
            assert not app.ai.ready(), 'test must run without AI models'
            app.query.set('커피'); app.do_search(False); idle()
            assert app.last_count == 1, app.last_count
            for page in ['organize', 'history', 'more', 'closet', 'home']:
                app.show(page); root.update()
            app.show('organize'); app.plan_clean(); root.update()
            cancel = next(w for w in widgets(root) if isinstance(w, tk.Button) and w.cget('text') == '아직 안 할래요')
            cancel.invoke(); root.update(); assert doc.exists()
            app.plan_clean(); root.update()
            confirm = next(w for w in widgets(root) if isinstance(w, tk.Button) and w.cget('text') == '② 이 파일들 정리하기')
            with patch('tkinter.messagebox.showinfo'), patch('tkinter.messagebox.showwarning'):
                confirm.invoke(); idle()
            assert not doc.exists()
            assert len(list(vault.rglob('*.txt'))) == 1
            with patch('tkinter.messagebox.askyesno', return_value=True):
                app.undo(); idle()
            assert doc.read_text(encoding='utf-8') == '영수증 커피 4500원'
            assert not app.settings['auto']
            return dict(ok=True, checks=['IME font readback', 'Korean text preserved on deferred submit', 'desktop-only startup', 'pet greeting requested', 'bubble keyword search without main window',
                'result opens original file', 'reopen preserves results', 'single-instance wake-up', 'frozen audio worker' if getattr(sys,'frozen',False) else 'source runtime',
                'four primary menus', 'search without AI', 'page navigation',
                'preview cancel preserves original', 'confirmed move', 'undo restores original', 'no Tk callback errors'])
        finally:
            app.quit()


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False))
