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
            while (app.busy or app.indexing or app.photo_indexing) and time.monotonic()<deadline:
                root.update(); time.sleep(.03)
            assert not app.busy and not app.indexing and not app.photo_indexing, 'background job did not finish'
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
                committed=[]; ime.commit_then(lambda:committed.append(bubble.text.get())); root.update();time.sleep(.04);root.update()
                assert committed==['급여명세서']
                bubble.text.set('')
                initial_buttons=[w.cget('text') for w in widgets(bubble.win) if isinstance(w,tk.Button)]
                assert initial_buttons==['×','찾기'],initial_buttons
                app.open_desktop_search(); assert app.bubble is bubble
                bubble.submit('커피'); idle()
                assert len(bubble.rows)==1 and root.state()=='withdrawn'
                assert bubble.result_buttons
                assert [w.cget('text') for w in widgets(bubble.win) if isinstance(w,tk.Button)]==['×','찾기','미리보기로 크게 보기',doc.name]
                before=set(root.winfo_children())
                bubble.open_results(); root.update()
                opened_windows=[w for w in root.winfo_children() if w not in before and isinstance(w,tk.Toplevel)]
                assert opened_windows, 'result browser must open from bubble'
                browser=next(b for b in app.result_browsers if b.win in opened_windows)
                browser.win.geometry('780x520');root.update()
                assert str(browser.pane.cget('orient'))=='vertical', 'narrow results window stacks the preview'
                assert browser.open_button.winfo_rooty()+browser.open_button.winfo_height()<=browser.win.winfo_rooty()+browser.win.winfo_height()
                app.settings['text_scale']=1.3;browser.apply_readability();root.update()
                from tkinter import font as tkfont
                assert tkfont.Font(font=browser.tree.tag_configure('match')['font']).actual('size')==13
                app.settings['text_scale']=1;browser.apply_readability();browser.win.geometry('1050x720');root.update()
                browser.query.set('커피'); browser.refresh()
                chosen=browser.selected()['path']
                extra=source/'첨부파일.txt'; extra.write_text('영수증 커피 5500원',encoding='utf-8')
                app.reindex(); idle()
                assert len(bubble.rows)==2, 'analysis completion must refresh the hidden-window bubble'
                assert len(browser.rows)==2 and len(browser.visible)==2, 'open browser must receive new search results'
                assert browser.query.get()=='커피' and browser.selected()['path']==chosen, 'refresh must preserve filters and selection'
                extra.unlink(); app.reindex(); idle()
                for window in opened_windows: window.destroy()
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
            for page in ['organize', 'history', 'more', 'closet', 'help', 'home']:
                app.show(page); root.update()
            app.toggle_text_size();root.update()
            assert app.settings['text_scale']==1.3
            app.toggle_text_size();root.update()
            assert app.settings['text_scale']==1.0
            with patch('voice_runtime.ready',return_value=False):
                voice_window=app.voice_settings();root.update()
                assert voice_window.winfo_exists();voice_window.destroy()
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
            from PIL import Image
            photo_dir=base/'remembered-photos';photo_dir.mkdir()
            blue=photo_dir/'IMG_1492.jpg';red=photo_dir/'attachment_003.jpg'
            Image.new('RGB',(160,120),(15,30,155)).save(blue)
            Image.new('RGB',(160,120),(200,15,30)).save(red)
            with patch('photo_controller.photo_roots',return_value=[photo_dir]):
                app.query.set('사진 좀 보여줘');app.do_search(False);idle()
                assert len(app.result_cache)==2 and app.photo_gallery.win.winfo_exists()
                gallery=app.photo_gallery
                gallery.toggle_saved(str(blue));gallery.show_saved(True)
                assert len(gallery.visible)==1 and gallery.selected_path==str(blue)
                gallery.show_saved(False);gallery.select(str(blue));gallery.open_viewer()
                deadline=time.monotonic()+5
                while gallery.viewer.photo is None and time.monotonic()<deadline:
                    root.update();time.sleep(.02)
                assert gallery.viewer.photo is not None, 'bundled large photo viewer must render'
                first=gallery.selected_path;gallery.move_selection(1 if gallery.visible[0]['path']==first else -1)
                root.update()
                assert gallery.viewer.row['path']==gallery.selected_path and gallery.selected_path!=first
                gallery.close_viewer();assert gallery.win.winfo_exists() and gallery.viewer is None
                app.query.set('파란 사진 찾아줘');app.do_search(False);idle()
                assert [r['path'] for r in app.result_cache]==[str(blue)]
                app.refine_photo('더 어두워');idle()
                assert [r['path'] for r in app.result_cache]==[str(blue)]
                assert '어두' in app.photo_gallery.request.cget('text')
                app.undo_photo_query();idle()
                assert app.source==source and blue.exists() and red.exists()
                recycle_fixture=base/'recycle-fixture.png'
                def fixture_recycle(row):
                    assert Path(row['path'])==blue and blue.is_relative_to(base)
                    blue.rename(recycle_fixture)
                    return dict(ok=True,cancelled=False,error='')
                with patch('photo_recycle.recycle_photo',side_effect=fixture_recycle), patch('tkinter.messagebox.askyesno',return_value=True):
                    app.photo_gallery.delete_photo(str(blue))
                    deadline=time.monotonic()+8
                    while app.photo_deleting and time.monotonic()<deadline:
                        root.update();time.sleep(.02)
                assert not app.photo_deleting and not blue.exists() and recycle_fixture.exists() and red.exists()
                assert str(blue) in app.photo_removed and not app.photo_gallery.visible
                with app.photo_library.connect() as connection:
                    assert connection.execute('SELECT 1 FROM files WHERE path=?',(str(blue),)).fetchone() is None
                app.photo_gallery.close()
            return dict(ok=True, checks=['IME font readback', 'Korean text preserved on deferred submit', 'desktop-only startup', 'pet greeting requested', 'bubble keyword search without main window',
                'result opens original file', 'reopen preserves results', 'live analysis refreshes bubble and open browser', 'refresh preserves browser filter and selection', 'single-instance wake-up', 'frozen audio worker' if getattr(sys,'frozen',False) else 'source runtime',
                'four primary menus', 'search without AI', 'page navigation', 'help and diagnostics page', 'text size toggle', 'narrow results layout', 'scaled match rows', 'guided voice setup opens',
                'preview cancel preserves original', 'confirmed move', 'undo restores original', 'photo gallery opens from natural request',
                'anonymous photo found by actual color', 'photo refinement and back', 'photo search preserves organization folder', 'saved photo shortlist', 'bundled large photo rendering', 'large photo navigation and close', 'confirmed photo deletion updates gallery and catalogue (temporary file backend)', 'no Tk callback errors'])
        finally:
            app.quit()


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False))
