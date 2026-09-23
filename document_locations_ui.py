"""One small chooser for document search; never changes organization settings."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

from app import BG, WHITE, INK, GREEN, MUTED, FONT, button, label
from document_locations import suggested_document_locations, normalize_document_roots


def show_locations(app):
    previous=getattr(app,'locations_window',None)
    if previous is not None and previous.winfo_exists():
        previous.lift();previous.focus_force();return previous
    app.document_roots()
    win=tk.Toplevel(app.root);app.locations_window=win
    win.title('어디에서 찾아볼까요?');win.configure(bg=BG)
    from accessibility import fit_window,scroll_page,apply_fonts
    fit_window(win,620,540,440,340)
    win.attributes('-topmost',True)
    head=tk.Frame(win,bg=BG,padx=22,pady=18);head.pack(fill='x')
    label(head,'어디에서 찾아볼까요?',20,bold=True).pack(anchor='w')
    label(head,'여러 폴더를 함께 찾을 수 있어요. 파일 위치는 바뀌지 않아요.',12,GREEN,
          wraplength=540,justify='left').pack(anchor='w',pady=(8,0))
    footer=tk.Frame(win,bg=BG,padx=22,pady=14);footer.pack(side='bottom',fill='x')
    body=scroll_page(win,BG)
    choices={}
    demo=app.demo.resolve()
    configured=normalize_document_roots(app.settings.get('document_roots',[]),existing_only=False)
    # Practice files are replaced when the user connects their own folders.
    selected=[p for p in configured if p!=demo]
    def add(path,title=None,checked=False):
        path=Path(path).resolve()
        if path in choices:
            if checked:choices[path].set(True)
            return
        var=tk.BooleanVar(value=checked);choices[path]=var
        row=tk.Frame(body,bg=WHITE,padx=14,pady=10);row.pack(fill='x',padx=20,pady=4)
        tk.Checkbutton(row,text=title or path.name or str(path),variable=var,bg=WHITE,fg=INK,
                       font=(FONT,13,'bold'),anchor='w',cursor='hand2').pack(fill='x')
        text=str(path)+(' · 지금은 연결되어 있지 않아요' if not path.is_dir() else '')
        info=label(row,text,10,MUTED,wraplength=500,justify='left');info.pack(anchor='w',padx=(30,0))
        row.bind('<Configure>',lambda e,w=info:w.configure(wraplength=max(150,e.width-62)))
    suggestions=suggested_document_locations()
    for item in suggestions:add(item['path'],item['label'],Path(item['path']).resolve() in selected)
    for path in selected:
        # The implicit empty vault is not useful as a first-run choice.
        if path==app.vault.resolve() and not path.exists() and not app.settings.get('source'):continue
        add(path,checked=True)
    def choose_more():
        path=filedialog.askdirectory(parent=win,title='함께 찾을 폴더를 선택하세요')
        if path:add(path,checked=True);apply_fonts(win,app.settings.get('text_scale',1.0))
    button(footer,'폴더 추가',choose_more).pack(side='left')
    def save():
        app.set_document_roots([str(path) for path,var in choices.items() if var.get()])
        app.document_locations_changed()
        win.destroy()
    button(footer,'여기서 찾기',save,primary=True).pack(side='right')
    button(footer,'취소',win.destroy).pack(side='right',padx=8)
    win.location_choices=choices
    win.save_locations=save
    def release_choices(event):
        if event.widget is win:
            # Tcl variables must be released on Tk's thread, not a later worker GC.
            choices.clear()
            win.save_locations=None
            if app.locations_window is win:app.locations_window=None
    win.bind('<Destroy>',release_choices,add='+')
    win.bind('<Escape>',lambda e:win.destroy())
    apply_fonts(win,app.settings.get('text_scale',1.0))
    return win
