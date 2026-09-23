"""Exercise the real Tk UI against temporary, synthetic files only."""
import json
import hashlib
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import traceback
from types import SimpleNamespace
from unittest.mock import patch
from app_version import VERSION
from easy_app import EasyApp


def run():
    def fake_copy_files(paths, **kwargs):
        # Exercise UI success/failure without changing the user's clipboard.
        paths=list(paths)
        if not paths or any(not Path(path).is_file() for path in paths):
            raise ValueError('복사할 파일이 이동되었거나 없어졌어요.')
        return tuple(str(Path(path).resolve()) for path in paths)

    with tempfile.TemporaryDirectory(prefix='jjanggu-check-') as folder, \
            patch('windows_features.copy_files',side_effect=fake_copy_files) as clipboard_backend, \
            patch('enhancements.copy_files',side_effect=fake_copy_files):
        base = Path(folder)
        data = base / 'settings'; data.mkdir()
        source = base / 'documents'; source.mkdir()
        vault = base / 'storage'
        doc = source / '연습 영수증.txt'
        doc.write_text('영수증 커피 4500원', encoding='utf-8')
        os.utime(doc, (time.time()-120, time.time()-120))
        original_document=(hashlib.sha256(doc.read_bytes()).hexdigest(),doc.stat().st_mtime_ns)
        (data / 'settings.json').write_text(json.dumps(dict(source=str(source), vault=str(vault),
            sound=False, focus_auto=False)), encoding='utf-8')
        root = tk.Tk(); root.withdraw(); root.pet_only_start=True; errors = []
        def callback_error(kind,value,trace):
            detail=''.join(traceback.format_exception(kind,value,trace))
            frame=trace
            while frame is not None:
                wrapper=frame.tb_frame.f_locals.get('self')
                callback=getattr(wrapper,'func',None)
                code=getattr(callback,'__code__',None)
                if code is not None:
                    detail+=f'\nCallback registered at {code.co_filename}:{code.co_firstlineno}'
                frame=frame.tb_next
            errors.append(detail)
        root.report_callback_exception = callback_error
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
                assert initial_buttons==['×','찾기','내 모음','찾을 폴더'],initial_buttons
                app.open_desktop_search(); assert app.bubble is bubble
                bubble.submit('커피'); idle()
                assert len(bubble.rows)==1 and root.state()=='withdrawn'
                assert bubble.result_buttons
                result_labels=[w.cget('text') for w in widgets(bubble.win) if isinstance(w,tk.Button)]
                assert len(result_labels)==7 and set(result_labels)=={'×','찾기','미리보기로 크게 보기','처음으로','복사','☆',doc.name},result_labels
                assert len(bubble.copy_buttons)==1
                assert hasattr(bubble.result_buttons[0],'_file_drag'), 'bubble filename must support dragging'
                bubble.copy_buttons[0].invoke();root.update()
                assert clipboard_backend.call_args.args[0]==[str(doc)], 'bubble copy must target the displayed original file'
                with patch.object(tk.Menu,'tk_popup'):
                    bubble.file_menu(SimpleNamespace(x_root=1,y_root=1),bubble.rows[0])
                bubble_menu=bubble.file_context_menu
                menu_copy=next(index for index in range(bubble_menu.index('end')+1)
                               if bubble_menu.entrycget(index,'label')=='파일 복사')
                bubble_menu.invoke(menu_copy);root.update()
                assert clipboard_backend.call_args.args[0]==[str(doc)], 'bubble context menu must copy its file'
                assert len(bubble.pin_buttons)==1 and bubble.pin_buttons[0][0]==str(doc)
                bubble.pin_buttons[0][1].invoke();root.update()
                assert app.is_pinned(str(doc)) and bubble.pin_buttons[0][1].cget('text')=='★'
                with patch.object(tk.Menu,'tk_popup'):
                    bubble.file_menu(SimpleNamespace(x_root=1,y_root=1),bubble.rows[0])
                unpin=next(index for index in range(bubble.file_context_menu.index('end')+1)
                           if bubble.file_context_menu.entrycget(index,'label')=='고정 해제')
                bubble.file_context_menu.invoke(unpin);root.update()
                assert not app.is_pinned(str(doc)) and bubble.pin_buttons[0][1].cget('text')=='☆'
                assert (hashlib.sha256(doc.read_bytes()).hexdigest(),doc.stat().st_mtime_ns)==original_document
                before=set(root.winfo_children())
                bubble.open_results(); root.update()
                opened_windows=[w for w in root.winfo_children() if w not in before and isinstance(w,tk.Toplevel)]
                assert opened_windows, 'result browser must open from bubble'
                browser=next(b for b in app.result_browsers if b.win in opened_windows)
                browser.win.geometry('780x520');root.update()
                assert str(browser.pane.cget('orient'))=='vertical', 'narrow results window stacks the preview'
                assert browser.open_button.winfo_rooty()+browser.open_button.winfo_height()<=browser.win.winfo_rooty()+browser.win.winfo_height()
                from tkinter import font as tkfont
                assert browser.file_buttons, 'search results must provide clickable file cards'
                assert hasattr(browser.file_buttons[0],'_file_drag'), 'result filename must support dragging'
                normal_size=tkfont.Font(font=browser.file_buttons[0].cget('font')).actual('size')
                app.settings['text_scale']=1.3;browser.apply_readability();root.update()
                assert tkfont.Font(font=browser.file_buttons[0].cget('font')).actual('size')>normal_size
                assert browser.copy_button.winfo_ismapped()
                assert browser.copy_button.winfo_rooty()+browser.copy_button.winfo_height()<=browser.win.winfo_rooty()+browser.win.winfo_height(), 'copy action must fit the narrow window with large text'
                browser.copy_button.invoke();root.update()
                assert clipboard_backend.call_args.args[0]==[str(doc)] and 'Ctrl+V' in app.status.get()
                assert (hashlib.sha256(doc.read_bytes()).hexdigest(),doc.stat().st_mtime_ns)==original_document
                app.settings['text_scale']=1;browser.apply_readability();browser.win.geometry('1050x720');root.update()
                assert browser.query.get()=='커피', 'results search box must show the original request'
                browser.query.set('커피'); browser.submit_search(); idle()
                assert app.query.get()=='커피' and browser.search_query=='커피', 'results search must route to the app search'
                chosen=browser.selected()['path']
                extra=source/'첨부파일.txt'; extra.write_text('영수증 커피 5500원',encoding='utf-8')
                app.reindex(); idle()
                assert len(bubble.rows)==2, 'analysis completion must refresh the hidden-window bubble'
                assert len(browser.rows)==2 and len(browser.visible)==2, 'open browser must receive new search results'
                assert browser.query.get()=='커피' and browser.selected()['path']==chosen, 'refresh must preserve the search text and selection'
                extra.unlink(); app.reindex(); idle()
                missing_copy=base/'없어진 첨부 파일.txt'
                calls_before_failure=clipboard_backend.call_count
                with patch('easy_app.messagebox.showerror') as copy_error:
                    assert not app.copy_result_files([str(doc),str(missing_copy)],parent=browser.win)
                assert copy_error.called, 'a missing file must explain why copy was refused'
                assert clipboard_backend.call_count in (calls_before_failure,calls_before_failure+1)
                assert (hashlib.sha256(doc.read_bytes()).hexdigest(),doc.stat().st_mtime_ns)==original_document
                drag_fixture=base/'한글 {검토본} 첨부 자료.txt'
                drag_fixture.write_text('Synthetic drag attachment',encoding='utf-8')
                drag_original=(hashlib.sha256(drag_fixture.read_bytes()).hexdigest(),drag_fixture.stat().st_mtime_ns)
                from file_transfer import bind_file_drag
                drag_widget=tk.Button(root,text='Synthetic drag probe')
                assert bind_file_drag(drag_widget,lambda:[str(drag_fixture)]), 'bundled drag support must register'
                drag_data=drag_widget._file_drag.begin()
                from tkinterdnd2 import COPY,DND_FILES,REFUSE_DROP
                assert drag_data[:2]==(COPY,DND_FILES), 'drag must offer copy, never move'
                assert tuple(root.tk.splitlist(drag_data[2]))==(str(drag_fixture.resolve()),), 'Unicode, braces and spaces must stay in one file path'
                assert (hashlib.sha256(drag_fixture.read_bytes()).hexdigest(),drag_fixture.stat().st_mtime_ns)==drag_original
                drag_fixture.unlink()
                assert drag_widget._file_drag.begin()==REFUSE_DROP, 'drag must recheck files when the gesture starts'
                drag_widget.destroy()
                for window in opened_windows: window.destroy()
                with patch.object(app,'open_file') as opened:
                    bubble.result_buttons[0].invoke(); opened.assert_called_once_with(str(doc))
                bubble.close(); app.open_desktop_search(); root.update()
                assert app.bubble.rows
                app.bubble.close()
            app.settings['sound']=False
            import fitz
            from pdf_runtime import PDF_LOCK
            from result_browser import ResultBrowser
            pdf_path=base/'preview-only.pdf'
            with PDF_LOCK, fitz.open() as document:
                for number,color in enumerate(((.15,.35,.8),(.9,.4,.1)),1):
                    page=document.new_page(width=300,height=400)
                    page.draw_rect(page.rect,fill=color,color=None)
                    page.insert_text((30,50),f'Preview page {number}',fontsize=18,color=(1,1,1))
                document.save(str(pdf_path))
            pdf_stat=pdf_path.stat()
            pdf_row=dict(path=str(pdf_path),name=pdf_path.name,mtime=pdf_stat.st_mtime,
                         size=pdf_stat.st_size,group='일치하는 파일',body='Extracted text is not the page preview.')
            with patch.object(app,'open_file') as opened_pdf:
                pdf_browser=ResultBrowser(app,[pdf_row])
                try:
                    def wait_pdf_page(number):
                        deadline=time.monotonic()+10
                        while time.monotonic()<deadline:
                            root.update()
                            if (pdf_browser.photo is not None and pdf_browser.preview_result is not None
                                    and pdf_browser.preview_result.page==number):
                                break
                            time.sleep(.03)
                        assert pdf_browser.photo is not None and pdf_browser.preview_result is not None, 'inline PDF preview must render a real image'
                        assert pdf_browser.preview_result.page==number and pdf_browser.page_count==2
                        assert pdf_browser.page_label.cget('text')==f'{number+1} / 2쪽'
                    wait_pdf_page(0)
                    first_pixel=pdf_browser.preview_result.image.getpixel((20,20))
                    pdf_browser.next_page.invoke()
                    wait_pdf_page(1)
                    assert pdf_browser.preview_result.image.getpixel((20,20))!=first_pixel, 'next PDF page must replace the rendered image'
                    opened_pdf.assert_not_called()
                    assert not errors,errors
                finally:
                    pdf_browser.win.destroy();root.update()
            compare_dir=base/'compare-fixtures';compare_dir.mkdir()
            first_pdf=compare_dir/'검토 문서_v1.pdf'
            second_pdf=compare_dir/'검토 문서_v2.pdf'
            first_pdf.write_bytes(pdf_path.read_bytes());second_pdf.write_bytes(first_pdf.read_bytes())
            first_text=compare_dir/'회의 메모.txt'
            second_text=compare_dir/'메모 복사본.txt'
            different_text=compare_dir/'기타 메모.txt'
            first_text.write_text('동일 문서 비교용 임시 회의 메모',encoding='utf-8')
            second_text.write_bytes(first_text.read_bytes())
            different_text.write_text('다른 내용을 가진 임시 회의 메모',encoding='utf-8')
            comparison_paths=[first_pdf,second_pdf,first_text,second_text,different_text]
            originals={path:(path.read_bytes(),path.stat().st_mtime_ns) for path in comparison_paths}
            comparison_rows=[dict(path=str(path),name=path.name,mtime=path.stat().st_mtime,
                                  size=path.stat().st_size,body='같은 검색용 요약',group='일치하는 파일')
                             for path in comparison_paths]
            group_browser=ResultBrowser(app,comparison_rows,context_title='비교 검증')
            comparison_windows=[]
            try:
                group_browser.set_grouping(True)
                deadline=time.monotonic()+10
                while group_browser.group_pending and time.monotonic()<deadline:
                    root.update();time.sleep(.03)
                assert not group_browser.group_pending and len(group_browser.identical_groups)==2
                groups={frozenset(row['path'] for row in group) for group in group_browser.identical_groups.values()}
                assert groups=={frozenset((str(first_pdf),str(second_pdf))),frozenset((str(first_text),str(second_text)))}
                assert len(group_browser.grouping_rows())==3 and len(group_browser.file_buttons)==3
                pdf_group=next(key for key,group in group_browser.identical_groups.items() if group[0]['path']==str(first_pdf))
                group_browser.toggle_group(pdf_group);root.update()
                assert {str(first_pdf),str(second_pdf)}.issubset({row['path'] for row in group_browser.displayed_rows})
                assert len(group_browser.grouping_rows())==4
                group_browser.toggle_group(pdf_group);root.update();assert len(group_browser.grouping_rows())==3
                group_browser.set_grouping(False);root.update()
                assert {row['path'] for row in group_browser.displayed_rows}=={str(path) for path in comparison_paths}
                group_browser.select(str(first_pdf));group_browser.toggle_details();root.update()
                assert group_browser.compare_button.winfo_ismapped()
                before_windows=set(root.winfo_children())
                with patch.object(app,'open_file') as opened_comparison:
                    group_browser.compare_button.invoke();root.update()
                    comparison_window=next(window for window in root.winfo_children()
                                           if window not in before_windows and hasattr(window,'comparison'))
                    comparison_windows.append(comparison_window)
                    comparison=comparison_window.comparison
                    deadline=time.monotonic()+10
                    while (not all(pane['photo'] is not None for pane in comparison.panes)
                           or '같아요' not in comparison.status.cget('text')) and time.monotonic()<deadline:
                        root.update();time.sleep(.03)
                    assert all(pane['photo'] is not None and pane['result'].page_count==2 for pane in comparison.panes), 'comparison must render both original PDF documents'
                    assert comparison.selected['path']==str(first_pdf) and comparison.other['path']==str(second_pdf)
                    assert '같아요' in comparison.status.cget('text')
                    opened_comparison.assert_not_called()
                    comparison_window.destroy();root.update();comparison.worker.join(timeout=3)
                    assert comparison.closed and not comparison.worker.is_alive()
                    from file_compare_ui import show_comparison
                    chosen_window=show_comparison(app,comparison_rows[2],[])
                    comparison_windows.append(chosen_window);root.update()
                    chosen_comparison=chosen_window.comparison
                    assert chosen_comparison.other is None and str(chosen_comparison.panes[1]['open'].cget('state'))=='disabled'
                    with patch('file_compare_ui.filedialog.askopenfilename',return_value=str(different_text)) as pick_file:
                        chosen_comparison.choose_button.invoke();root.update();pick_file.assert_called_once()
                    deadline=time.monotonic()+10
                    while (not all(pane['result'] is not None for pane in chosen_comparison.panes)
                           or '완전히 같지는' not in chosen_comparison.status.cget('text')) and time.monotonic()<deadline:
                        root.update();time.sleep(.03)
                    assert chosen_comparison.other['path']==str(different_text)
                    assert '동일 문서 비교용' in chosen_comparison.panes[0]['text'].get('1.0','end')
                    assert '다른 내용을 가진' in chosen_comparison.panes[1]['text'].get('1.0','end')
                    assert '완전히 같지는' in chosen_comparison.status.cget('text')
                    opened_comparison.assert_not_called()
                    chosen_window.destroy();root.update();chosen_comparison.worker.join(timeout=3)
                    assert chosen_comparison.closed and not chosen_comparison.worker.is_alive()
                assert {path:(path.read_bytes(),path.stat().st_mtime_ns) for path in comparison_paths}==originals, 'grouping and comparison must preserve every original file'
                assert not errors,errors
            finally:
                for window in comparison_windows:
                    if window.winfo_exists():window.destroy()
                    window.comparison.worker.join(timeout=3)
                group_browser.win.destroy();root.update()
            recent_dir=base/'recent-only';recent_dir.mkdir()
            recent_files=[doc]
            for number in range(3):
                path=recent_dir/f'최근파일-{number}.txt'
                path.write_text(f'Synthetic recent file {number}',encoding='utf-8')
                recent_files.append(path)
            with patch('app.os.startfile') as started:
                for path in recent_files:assert app.open_file(str(path)) is True
                assert started.call_count==4
            assert [row['path'] for row in app.file_memory.recent()]==[str(path) for path in reversed(recent_files)]
            failed_file=recent_dir/'failed-open.txt';failed_file.write_text('Cannot open fixture',encoding='utf-8')
            with patch('app.os.startfile',side_effect=OSError('synthetic open failure')),patch('app.messagebox.showerror'):
                assert app.open_file(str(failed_file)) is False
            assert str(failed_file) not in [row['path'] for row in app.file_memory.recent()]
            app.open_desktop_search();root.update();app.bubble.show_home();root.update()
            recent_names=[row['name'] for row in app.file_memory.recent(3)]
            home_buttons=[w.cget('text') for w in widgets(app.bubble.win) if isinstance(w,tk.Button)]
            assert home_buttons==['×','찾기','내 모음','찾을 폴더','모두 보기',*recent_names],home_buttons
            assert len(recent_names)==3 and doc.name not in recent_names
            recent_buttons=[w for w in widgets(app.bubble.win) if isinstance(w,tk.Button) and w.cget('text') in recent_names]
            assert len(recent_buttons)==3 and all(w.winfo_ismapped() for w in recent_buttons)
            body_bottom=app.bubble.body.winfo_rooty()+app.bubble.body.winfo_height()
            assert max(w.winfo_rooty()+w.winfo_height() for w in recent_buttons)<=body_bottom, 'all three recent shortcuts must fit inside the bubble without scrolling'
            app.bubble.close()
            from file_memory import FileMemory
            pin_row=app.reference_rows([dict(path=str(doc),name=doc.name,available=True)])[0]
            pin_first=ResultBrowser(app,[pin_row],context_title='고정 검증 하나')
            pin_second=ResultBrowser(app,[pin_row],context_title='고정 검증 둘')
            pin_first.pin_button.invoke();root.update()
            assert app.is_pinned(str(doc)) and FileMemory(data).is_pinned(str(doc)), 'pin must persist beyond its FileMemory instance'
            assert all('고정 해제' in view.pin_button.cget('text') for view in (pin_first,pin_second)), 'pin state must synchronize across open result windows'
            pinned_browser=app.show_pinned();root.update()
            assert pinned_browser.pinned_view and [row['path'] for row in pinned_browser.rows]==[str(doc)]
            assert app.show_pinned() is pinned_browser, 'opening pinned files twice must reuse the same window'
            pin_second.pin_button.invoke();root.update()
            assert not app.is_pinned(str(doc)) and not pinned_browser.rows
            assert all('고정하기' in view.pin_button.cget('text') for view in (pin_first,pin_second))
            pin_first.pin_button.invoke();root.update()
            for path in recent_files[1:]:assert app.toggle_pinned(str(path)) is True
            app.open_desktop_search();root.update();app.bubble.show_home();root.update()
            pinned_names=[row['name'] for row in app.file_memory.pinned_files(limit=3,include_missing=False)]
            pin_home_buttons=[w for w in widgets(app.bubble.win) if isinstance(w,tk.Button)]
            pin_shortcuts=[w for w in pin_home_buttons if w.cget('text') in pinned_names]
            assert len(pin_shortcuts)==3 and doc.name not in pinned_names
            assert any(w.cget('text')=='모두 보기' for w in pin_home_buttons)
            home_labels=[w.cget('text') for w in widgets(app.bubble.win) if isinstance(w,tk.Label)]
            assert any('고정한 파일' in value for value in home_labels) and '최근 연 파일' not in home_labels
            body_bottom=app.bubble.body.winfo_rooty()+app.bubble.body.winfo_height()
            assert all(w.winfo_rooty()+w.winfo_height()<=body_bottom for w in pin_shortcuts), 'pinned shortcuts must fit inside the home bubble'
            next(w for w in pin_home_buttons if w.cget('text')=='모두 보기').invoke();root.update()
            assert app.pinned_view is pinned_browser and len(pinned_browser.rows)==4
            missing_pin=recent_dir/'사라진 고정 파일.txt';missing_pin.write_text('Temporary missing pin',encoding='utf-8')
            assert app.toggle_pinned(str(missing_pin)) is True
            missing_pin.unlink();app.pinned_files_changed();root.update()
            pinned_browser.select(str(missing_pin));root.update()
            assert any(row['path']==str(missing_pin) for row in pinned_browser.rows), 'missing pinned paths must remain removable'
            assert str(pinned_browser.open_button.cget('state'))=='disabled' and str(pinned_browser.copy_button.cget('state'))=='disabled'
            assert str(pinned_browser.pin_button.cget('state'))=='normal'
            pinned_browser.pin_button.invoke();root.update()
            assert not app.is_pinned(str(missing_pin)) and all(row['path']!=str(missing_pin) for row in pinned_browser.rows)
            for path in recent_files[1:]:assert app.toggle_pinned(str(path)) is False
            assert (hashlib.sha256(doc.read_bytes()).hexdigest(),doc.stat().st_mtime_ns)==original_document, 'pinning and unpinning must preserve the original'
            app.bubble.close()
            for view in (pin_first,pin_second,pinned_browser):view.win.destroy()
            root.update()
            collection_id=app.file_memory.create_collection('검증 모음')
            before_document=doc.read_bytes()
            chooser=app.add_to_collection([str(doc)]);root.update()
            choose_button=next(w for w in widgets(chooser) if isinstance(w,tk.Button) and w.cget('text')=='검증 모음  ·  0개')
            choose_button.invoke();root.update()
            assert not chooser.winfo_exists()
            assert [row['path'] for row in app.file_memory.collection_files(collection_id)]==[str(doc)]
            assert doc.read_bytes()==before_document, 'adding to a collection must preserve the original file'
            collection_window=app.show_collections();root.update()
            collection_view=app.collections_view
            collection_actions=[w for w in widgets(collection_window) if isinstance(w,tk.Button)]
            pinned_entry=next(w for w in collection_actions if w.cget('text')=='★ 고정한 파일')
            saved_entry=next(w for w in collection_actions if w.cget('text')=='♥ 찜한 사진')
            assert pinned_entry.winfo_ismapped() and saved_entry.winfo_ismapped()
            pinned_entry.invoke();root.update()
            assert app.pinned_view.context_title=='고정한 파일' and [row['path'] for row in app.pinned_view.rows]==[str(doc)]
            app.pinned_view.win.destroy();root.update()
            saved_entry.invoke();root.update()
            assert app.saved_photo_gallery.saved_only.get(), 'saved-photos collection entry must open the persistent saved list'
            app.saved_photo_gallery.close();root.update()
            collection_view.select(str(doc));root.update()
            before_browsers=set(app.result_browsers)
            collection_view.preview_all();root.update()
            collection_browser=next(browser for browser in app.result_browsers if browser not in before_browsers)
            assert collection_browser.context_title=='검증 모음' and collection_browser.query.get()==''
            recent_browser=app.show_recent();root.update()
            assert recent_browser.context_title=='최근 연 파일' and len(recent_browser.rows)==4
            for snapshot in (collection_browser,recent_browser):
                snapshot.select(str(doc))
                deadline=time.monotonic()+5
                while '커피' not in snapshot.body.get('1.0','end') and time.monotonic()<deadline:
                    root.update();time.sleep(.03)
                assert '커피' in snapshot.body.get('1.0','end'), 'recent and collection text previews must reuse unchanged indexed document contents'
                snapshot.copy_button.invoke();root.update()
                assert clipboard_backend.call_args.args[0]==[str(doc)], 'recent and collection copies must target the selected original'
                assert (hashlib.sha256(doc.read_bytes()).hexdigest(),doc.stat().st_mtime_ns)==original_document
            missing=recent_dir/'missing-from-collection.txt';missing.write_text('Missing fixture',encoding='utf-8')
            app.file_memory.add_files(collection_id,[missing]);missing.unlink()
            collection_view.refresh();collection_view.select(str(missing));root.update()
            assert str(collection_view.open_button.cget('state'))=='disabled'
            assert str(collection_view.preview_button.cget('state'))=='disabled'
            assert '파일을 찾을 수 없어요' in collection_view.file_buttons[str(missing)].cget('text')
            collection_view.remove_button.invoke();root.update()
            assert [row['path'] for row in app.file_memory.collection_files(collection_id)]==[str(doc)]
            assert doc.read_bytes()==before_document
            extra_dir=base/'extra-documents';extra_dir.mkdir()
            extra_doc=extra_dir/'이름모를첨부.txt';extra_doc.write_text('해오라기 전용 검증 자료',encoding='utf-8')
            suggestions=[dict(label='연습 문서',path=str(source)),dict(label='추가 문서',path=str(extra_dir))]
            organization_before=(app.source,app.vault,app.settings['auto'])
            roots_before=list(app.settings['document_roots'])
            with patch('document_locations_ui.suggested_document_locations',return_value=suggestions):
                locations=app.choose_document_locations();root.update()
                for path,checked in locations.location_choices.items():checked.set(path==extra_dir)
                cancel=next(w for w in widgets(locations) if isinstance(w,tk.Button) and w.cget('text')=='취소')
                cancel.invoke();root.update()
                assert app.settings['document_roots']==roots_before, 'cancelling the folder chooser must not change search roots'
                assert (app.source,app.vault,app.settings['auto'])==organization_before
                locations=app.choose_document_locations();root.update()
                for path,checked in locations.location_choices.items():checked.set(path in {source,extra_dir})
                locations.save_locations();idle()
            assert set(app.document_roots())=={source,extra_dir}
            assert (app.source,app.vault,app.settings['auto'])==organization_before, 'search roots must not change organization settings'
            app.query.set('해오라기');app.do_search(False);idle()
            assert [row['path'] for row in app.result_cache]==[str(extra_doc)], 'an explicitly added folder must be searched by file contents'
            assert [row['path'] for row in collection_browser.rows]==[str(doc)], 'an unrelated search must not overwrite a collection snapshot'
            assert len(recent_browser.rows)==4, 'an unrelated search must not overwrite a recent-file snapshot'
            app.set_document_roots([]);idle()
            app.query.set('커피');app.do_search(False);idle()
            assert app.document_roots()==[] and app.document_rows()==[] and app.result_cache==[], 'empty search roots must not leak other indexed files'
            assert (app.source,app.vault,app.settings['auto'])==organization_before
            app.set_document_roots([str(source),str(vault)]);idle()
            collection_browser.win.destroy();recent_browser.win.destroy();collection_window.destroy();root.update()
            with patch('app.os.startfile'):assert app.open_file(str(doc)) is True
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
            app.final_versions.mark(str(doc))
            app.show('organize'); app.plan_clean(); root.update()
            cancel = next(w for w in widgets(root) if isinstance(w, tk.Button) and w.cget('text') == '아직 안 할래요')
            cancel.invoke(); root.update(); assert doc.exists()
            app.plan_clean(); root.update()
            confirm = next(w for w in widgets(root) if isinstance(w, tk.Button) and w.cget('text') == '② 이 파일들 정리하기')
            with patch('tkinter.messagebox.showinfo'), patch('tkinter.messagebox.showwarning'):
                confirm.invoke(); idle()
            assert not doc.exists()
            assert len(list(vault.rglob('*.txt'))) == 1
            moved_doc=next(vault.rglob('*.txt'))
            assert app.final_versions.describe(moved_doc)['state']=='final', 'organization must preserve explicit final version references'
            assert app.file_memory.recent(1)[0]['path']==str(moved_doc), 'organization must update recent-file references'
            assert app.file_memory.collection_files(collection_id)[0]['path']==str(moved_doc), 'organization must update collection references'
            assert app.file_memory.is_pinned(str(moved_doc)) and not app.file_memory.is_pinned(str(doc)), 'organization must update pinned-file references'
            with patch('tkinter.messagebox.askyesno', return_value=True):
                app.undo(); idle()
            assert doc.read_text(encoding='utf-8') == '영수증 커피 4500원'
            assert not app.settings['auto']
            assert app.file_memory.recent(1)[0]['path']==str(doc) and app.file_memory.recent(1)[0]['available']
            assert app.file_memory.collection_files(collection_id)[0]['path']==str(doc), 'undo must restore recent-file and collection references'
            assert FileMemory(data).is_pinned(str(doc)), 'undo must persist the restored pinned path'
            assert app.final_versions.describe(doc)['state']=='final', 'undo must restore explicit final version references'
            from PIL import Image
            photo_dir=base/'remembered-photos';photo_dir.mkdir()
            blue=photo_dir/'IMG_1492.jpg';red=photo_dir/'attachment_003.jpg'
            Image.new('RGB',(160,120),(15,30,155)).save(blue)
            Image.new('RGB',(160,120),(200,15,30)).save(red)
            with patch('photo_controller.photo_roots',return_value=[photo_dir]):
                app.query.set('사진 좀 보여줘');app.do_search(False);idle()
                assert len(app.result_cache)==2 and app.photo_gallery.win.winfo_exists()
                gallery=app.photo_gallery
                assert app.toggle_pinned(str(doc)) is False and app.toggle_pinned(str(doc)) is True, 'pin broadcasts must coexist with an open photo gallery'
                gallery.toggle_saved(str(blue));gallery.show_saved(True)
                assert len(gallery.visible)==1 and gallery.selected_path==str(blue)
                photo_originals={path:(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns) for path in (blue,red)}
                assert [row['path'] for row in FileMemory(data).saved_photos()]==[str(blue)], 'saved photos must persist to the local database'
                snapshot=(app.query.get(),app.is_photo_search,[row['path'] for row in app.result_cache],app.photo_gallery)
                app.open_saved_photos();root.update();saved_gallery=app.saved_photo_gallery
                assert [row['path'] for row in saved_gallery.visible]==[str(blue)]
                assert snapshot==(app.query.get(),app.is_photo_search,[row['path'] for row in app.result_cache],app.photo_gallery), 'opening saved photos must preserve the active search'
                saved_gallery.close();root.update()
                app.open_saved_photos();root.update();saved_gallery=app.saved_photo_gallery
                assert [row['path'] for row in saved_gallery.visible]==[str(blue)], 'closing and reopening saved photos must keep the saved list'
                saved_gallery.toggle_saved(str(blue));root.update()
                assert not FileMemory(data).saved_photos(), 'unsaving must persist immediately'
                assert {path:(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns) for path in (blue,red)}==photo_originals
                saved_gallery.close();root.update()
                gallery.show_saved(True);root.update()
                assert not gallery.visible, 'another photo window must observe saved-list changes'
                gallery.show_saved(False);gallery.toggle_saved(str(blue));root.update()
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
            from verify_next_workflows import run as verify_workflows
            new_checks=verify_workflows(app,base,source,idle)
            return dict(ok=True, version=VERSION, checks=['IME font readback', 'Korean text preserved on deferred submit', 'desktop-only startup', 'pet greeting requested', 'bubble keyword search without main window',
                'result opens original file', 'reopen preserves results', 'results search box submits the original request', 'live analysis refreshes bubble and open browser', 'refresh preserves browser search text and selection', 'single-instance wake-up', 'frozen audio worker' if getattr(sys,'frozen',False) else 'source runtime',
                'four primary menus', 'search without AI', 'page navigation', 'help and diagnostics page', 'text size toggle', 'narrow results layout', 'scaled file cards', 'inline two-page PDF preview without opening original', 'guided voice setup opens',
                'preview cancel preserves original', 'confirmed move', 'undo restores original', 'photo gallery opens from natural request',
                'anonymous photo found by actual color', 'photo refinement and back', 'photo search preserves organization folder', 'saved photo shortlist', 'bundled large photo rendering', 'large photo navigation and close', 'confirmed photo deletion updates gallery and catalogue (temporary file backend)',
                'successful opens remembered and failed opens excluded', 'three recent files on bubble home', 'collection chooser preserves originals', 'missing collection files disable open and preview', 'collection and recent snapshots survive unrelated searches', 'recent and collection text previews show indexed contents',
                'folder chooser cancellation preserves search and organization settings', 'explicit extra folder searched by contents', 'empty search scope excludes previously indexed files', 'organization and undo update recent and collection paths',
                'identical files group expand and restore without changing originals', 'comparison opens from result details and renders both PDFs', 'comparison without candidates accepts a chosen text file', 'three recent file shortcuts fit inside the bubble',
                'bubble button and context menu copy the displayed file (mock clipboard)', 'search results copy selected originals with Ctrl+V guidance (mock clipboard)', 'copy button fits narrow large-text results',
                'missing copy input refuses the complete operation', 'Unicode braces and spaces stay one copy-only drag path', 'drag rechecks missing files at gesture start', 'recent and collection snapshots copy selected originals (mock clipboard)', 'copy and drag preserve source bytes and modification time',
                'bubble star and context menu synchronize pin state', 'document pins persist and synchronize across result windows', 'pinned list window is reused and updates after unpin', 'three available pins replace recent home shortcuts with an all-files action', 'missing pinned paths remain visible and removable', 'pinning and unpinning preserve originals', 'collections exposes pinned files and saved photos', 'organization and undo preserve pinned references',
                'saved photos persist to local storage', 'saved photo entry preserves active search', 'closing and reopening saved photos keeps favorites', 'unsaving persists across photo windows without changing originals',
                'organization and undo preserve explicit final version references',
                'no Tk callback errors']+new_checks)
        finally:
            app.quit()


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False))
