"""Small named collections of file references, shared by desktop search surfaces."""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from accessibility import apply_fonts, fit_window


BG = '#FFFDF9'
INK = '#292A28'
MUTED = '#65675F'
ACCENT = '#B9382B'
SELECTED = '#FFF0E8'
FONT = '맑은 고딕'


def _label(parent, text, size=12, fg=INK):
    return tk.Label(parent, text=text, font=(FONT, size), fg=fg, bg=BG, anchor='w', justify='left')


def _button(parent, text, command, primary=False):
    return tk.Button(parent, text=text, command=command, font=(FONT, 12),
                     bg=ACCENT if primary else BG, fg='white' if primary else INK,
                     activebackground=ACCENT if primary else SELECTED,
                     activeforeground='white' if primary else INK,
                     relief='flat', padx=12, pady=9, cursor='hand2')


def _notice(app, text):
    status = getattr(app, 'status', None)
    if status is not None:
        status.set(text)


def _refresh_view(app):
    view = getattr(app, 'collections_view', None)
    if view is not None and view.win.winfo_exists():
        view.refresh()


def _preview_rows(rows):
    return [dict(row, group='일치하는 파일', body='', reason='내가 이 모음에 담은 파일이에요.')
            for row in rows if row.get('available')]


def show_collections(app):
    view = getattr(app, 'collections_view', None)
    if view is not None and view.win.winfo_exists():
        view.refresh()
        view.win.deiconify()
        view.win.lift()
    else:
        view = app.collections_view = CollectionsWindow(app)
    return view.win


class CollectionsWindow:
    PAGE_SIZE = 50

    def __init__(self, app):
        self.app = app
        self.memory = app.file_memory
        self.collection_id = None
        self.selected_path = None
        self.collections = []
        self.rows = []
        self.file_buttons = {}
        self.visible_count = self.PAGE_SIZE
        self.win = tk.Toplevel(app.root)
        self.win.title('내 모음')
        self.win.configure(bg=BG)
        fit_window(self.win, 830, 640, 620, 440)
        self.win.bind('<Escape>', lambda event: self.win.destroy())
        self.win.bind('<FocusIn>', self._focus)
        header = tk.Frame(self.win, bg=BG, padx=20, pady=16)
        header.pack(fill='x')
        _label(header, '내 모음', 20).pack(anchor='w')
        _label(header, '파일은 제자리에 두고, 필요한 것만 모아 봐요.', 11, MUTED).pack(anchor='w', pady=(4, 0))
        shortcuts=tk.Frame(header,bg=BG);shortcuts.pack(fill='x',pady=(10,0))
        _button(shortcuts,'★ 고정한 파일',app.show_pinned).pack(side='left')
        _button(shortcuts,'♥ 찜한 사진',app.open_saved_photos).pack(side='left',padx=8)
        if hasattr(app,'show_mail_connections'):
            mail=_button(shortcuts,'메일 첨부파일',app.show_mail_connections)
            mail.configure(font=(FONT,10),padx=5);mail.pack(side='left')
        self.pane = tk.PanedWindow(self.win, orient='horizontal', bg='#E3E4DC', sashwidth=7, bd=0)
        self.pane.pack(fill='both', expand=True, padx=15)
        left = tk.Frame(self.pane, bg=BG)
        right = tk.Frame(self.pane, bg=BG)
        self.pane.add(left, minsize=175, width=220)
        self.pane.add(right, minsize=250)
        _button(left, '+ 새 모음', self.create).pack(fill='x', pady=(0, 8))
        _button(left,'자동 모음 →',self.show_smart).pack(fill='x',pady=(0,8))
        self.collection_list = tk.Listbox(left, font=(FONT, 12), bg=BG, fg=INK,
                                         relief='flat', highlightthickness=0, activestyle='none',
                                         selectbackground=SELECTED, selectforeground=INK, exportselection=False)
        collection_bar = ttk.Scrollbar(left, command=self.collection_list.yview)
        collection_bar.pack(side='right', fill='y')
        self.collection_list.configure(yscrollcommand=collection_bar.set)
        self.collection_list.pack(fill='both', expand=True)
        self.collection_list.bind('<<ListboxSelect>>', self._choose)
        self.collection_list.bind('<Return>', lambda event: self.preview_all())
        self.collection_heading = _label(right, '', 14)
        self.collection_heading.pack(fill='x', padx=12, pady=(6, 10))
        actions = tk.Frame(right, bg=BG)
        actions.pack(side='bottom', fill='x', padx=12, pady=10)
        self.open_button = _button(actions, '파일 열기', self.open, primary=True)
        self.open_button.pack(side='right')
        self.preview_button = _button(actions, '미리 보기', self.preview_all)
        self.preview_button.pack(side='left')
        self.info = _label(right, '', 10, MUTED)
        self.info.configure(wraplength=420)
        self.info.pack(side='bottom', fill='x', padx=12, pady=(6, 0))
        tools = tk.Frame(right, bg=BG)
        tools.pack(side='bottom', fill='x', padx=12)
        self.remove_button = _button(tools, '모음에서 빼기', self.remove)
        self.remove_button.configure(font=(FONT, 10), padx=0, pady=6)
        self.remove_button.pack(side='left')
        menu_button = tk.Menubutton(tools, text='모음 관리 ▾', font=(FONT, 10), bg=BG, fg=MUTED,
                                    relief='flat', cursor='hand2')
        menu = tk.Menu(menu_button, tearoff=False)
        menu.add_command(label='이름 바꾸기', command=self.rename)
        menu.add_command(label='모음 지우기', command=self.delete)
        menu_button.configure(menu=menu)
        menu_button.pack(side='right')
        self.canvas = tk.Canvas(right, bg=BG, highlightthickness=0)
        bar = ttk.Scrollbar(right, command=self.canvas.yview)
        bar.pack(side='right', fill='y')
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(fill='both', expand=True)
        self.files_frame = tk.Frame(self.canvas, bg=BG, padx=10)
        self.files_item = self.canvas.create_window(0, 0, anchor='nw', window=self.files_frame)
        self.files_frame.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self._fit_files)
        self.win.bind('<MouseWheel>', self._wheel, add='+')
        self.refresh()

    def _focus(self, event):
        if event.widget is self.win:
            self.refresh()

    def show_smart(self):
        from smart_collections_ui import show_smart_collections
        return show_smart_collections(self.app)

    def _fit_files(self, event):
        self.canvas.itemconfigure(self.files_item, width=max(1, event.width))
        for button in self.file_buttons.values():
            button.configure(wraplength=max(140, event.width - 35))
        self.info.configure(wraplength=max(140, event.width - 24))

    def _wheel(self, event):
        if str(event.widget).startswith(str(self.files_frame)) or event.widget is self.canvas:
            self.canvas.yview_scroll(-int(event.delta / 120), 'units')
            return 'break'

    def current_collection(self):
        return next((row for row in self.collections if row['id'] == self.collection_id), None)

    def refresh(self):
        self.collections = self.memory.list_collections()
        if not any(row['id'] == self.collection_id for row in self.collections):
            self.collection_id = self.collections[0]['id'] if self.collections else None
        self.collection_list.delete(0, 'end')
        for index, row in enumerate(self.collections):
            self.collection_list.insert('end', f"{row['name']}  ·  {row['file_count']}")
            if row['id'] == self.collection_id:
                self.collection_list.selection_set(index)
                self.collection_list.see(index)
        self._show_files()

    def _choose(self, event=None):
        selection = self.collection_list.curselection()
        if selection and selection[0] < len(self.collections):
            ident = self.collections[selection[0]]['id']
            if ident != self.collection_id:
                self.collection_id = ident
                self.selected_path = None
                self.visible_count = self.PAGE_SIZE
                self._show_files()

    def _show_files(self):
        for widget in self.files_frame.winfo_children():
            widget.destroy()
        self.file_buttons = {}
        collection = self.current_collection()
        self.rows = self.memory.collection_files(self.collection_id, limit=self.visible_count) if collection else []
        self.collection_heading.configure(text=f"{collection['name']} · {collection['file_count']}개" if collection else '첫 모음을 만들어 보세요')
        if not self.rows:
            message = ('파일을 찾은 다음 “내 모음에 담기”를 눌러 주세요.' if collection else
                       '예: 이번 프로젝트, 자주 쓰는 서류\n왼쪽의 “새 모음”으로 시작해요.')
            label = _label(self.files_frame, message, 12, MUTED)
            label.configure(wraplength=360)
            label.pack(fill='x', padx=8, pady=25)
        for row in self.rows:
            suffix = '\n파일을 찾을 수 없어요' if not row['available'] else '\n' + Path(row['path']).parent.name
            button = _button(self.files_frame, row['name'] + suffix,
                             lambda path=row['path']: self.select(path))
            button.configure(anchor='w', justify='left', wraplength=360)
            if not row['available']:
                button.configure(fg=MUTED)
            button.pack(fill='x', pady=3)
            button.bind('<Double-Button-1>', lambda event, path=row['path']: self._open_path(path))
            self.file_buttons[row['path']] = button
        if collection and collection['file_count'] > len(self.rows):
            _button(self.files_frame, '더 보기', self.show_more).pack(fill='x', pady=10)
        if self.selected_path not in self.file_buttons:
            self.selected_path = self.rows[0]['path'] if self.rows else None
        self.select(self.selected_path)
        apply_fonts(self.win, getattr(self.app, 'settings', {}).get('text_scale', 1.0))

    def show_more(self):
        self.visible_count += self.PAGE_SIZE
        self._show_files()

    def select(self, path):
        self.selected_path = path
        row = next((row for row in self.rows if row['path'] == path), None)
        for value, button in self.file_buttons.items():
            button.configure(bg=SELECTED if value == path else BG)
        self.open_button.configure(state='normal' if row and row['available'] else 'disabled')
        self.preview_button.configure(state='normal' if row and row['available'] else 'disabled')
        self.remove_button.configure(state='normal' if row else 'disabled')
        self.info.configure(text=(row['path'] if row and row['available'] else
                                 '파일이 이동되었거나 삭제됐어요. 모음에서 빼도 원본에는 영향이 없어요.' if row else ''))

    def open(self):
        row = next((row for row in self.rows if row['path'] == self.selected_path), None)
        if row and Path(row['path']).is_file():
            self.app.open_file(row['path'])
        else:
            self._show_files()

    def _open_path(self, path):
        self.select(path)
        self.open()
        return 'break'

    def preview_all(self):
        collection = self.current_collection()
        if collection:
            from result_browser import ResultBrowser
            rows = _preview_rows([row for row in self.rows if row['path'] == self.selected_path])
            if rows:
                if hasattr(self.app,'reference_rows'):rows=self.app.reference_rows(rows)
                browser = ResultBrowser(self.app, rows, context_title=collection['name'])
                if self.selected_path in {row['path'] for row in rows}:
                    browser.select(self.selected_path)

    def create(self):
        name = simpledialog.askstring('새 모음', '모음 이름을 지어 주세요. 예: 이번 프로젝트', parent=self.win)
        if name is None:
            return
        try:
            self.collection_id = self.memory.create_collection(name)
            self.visible_count = self.PAGE_SIZE
            self.refresh()
        except ValueError as error:
            messagebox.showinfo('모음 이름', str(error), parent=self.win)

    def rename(self):
        collection = self.current_collection()
        if not collection:
            return
        name = simpledialog.askstring('이름 바꾸기', '새 모음 이름', initialvalue=collection['name'], parent=self.win)
        if name is None:
            return
        try:
            self.memory.rename_collection(self.collection_id, name)
            self.refresh()
        except ValueError as error:
            messagebox.showinfo('모음 이름', str(error), parent=self.win)

    def delete(self):
        collection = self.current_collection()
        if collection and messagebox.askyesno('모음 지우기', f"‘{collection['name']}’ 모음을 지울까요?\n원본 파일은 그대로 남아요.", parent=self.win):
            self.memory.delete_collection(self.collection_id)
            self.collection_id = None
            self.selected_path = None
            self.refresh()

    def remove(self):
        if self.collection_id is not None and self.selected_path:
            self.memory.remove_files(self.collection_id, [self.selected_path])
            self.refresh()


def choose_collection(app, paths):
    """Choose an existing collection with one click, or name a new collection."""
    paths = tuple(dict.fromkeys(str(Path(path)) for path in paths))
    if not paths:
        return None
    win = tk.Toplevel(app.root)
    win.title('모음에 담기')
    win.configure(bg=BG)
    fit_window(win, 460, 500, 350, 320)
    win.bind('<Escape>', lambda event: win.destroy())
    header = tk.Frame(win, bg=BG, padx=20, pady=18)
    header.pack(fill='x')
    _label(header, '어느 모음에 담을까요?', 17).pack(anchor='w')
    _label(header, f'{len(paths)}개 파일 · 원래 위치는 그대로예요.', 10, MUTED).pack(anchor='w', pady=(5, 0))
    bottom = tk.Frame(win, bg=BG, padx=20, pady=12)
    bottom.pack(side='bottom', fill='x')
    feedback = _label(bottom, '', 10, MUTED)
    feedback.configure(wraplength=390)
    feedback.pack(fill='x', pady=(0, 7))
    create_area = tk.Frame(bottom, bg=BG)
    name = tk.StringVar()
    entry = tk.Entry(create_area, textvariable=name, font=(FONT, 12), relief='solid', bd=1)
    entry.pack(fill='x', ipady=6, pady=(0, 8))
    from ime_entry import attach
    ime = attach(entry)
    win.collection_ime = ime

    def finish(ident, title):
        try:
            available = [path for path in paths if Path(path).is_file()]
            if not available:
                feedback.configure(text='담을 파일을 찾을 수 없어요. 파일 위치를 확인해 주세요.')
                return
            added = app.file_memory.add_files(ident, available)
            _notice(app, f'“{title}”에 {added}개 담았어요.' if added else f'이미 “{title}”에 담겨 있어요.')
            _refresh_view(app)
            win.destroy()
        except (ValueError, OSError) as error:
            feedback.configure(text=str(error))

    def create():
        if not any(Path(path).is_file() for path in paths):
            feedback.configure(text='담을 파일을 찾을 수 없어요. 파일 위치를 확인해 주세요.')
            return
        try:
            ident = app.file_memory.create_collection(name.get())
        except ValueError as error:
            feedback.configure(text=str(error))
            return
        finish(ident, name.get().strip())

    def submit_create():
        return ime.commit_then(create) if ime else create()

    _button(create_area, '만들고 담기', submit_create, primary=True).pack(fill='x')
    entry.bind('<Return>', lambda event: submit_create())

    def show_create():
        create_button.pack_forget()
        create_area.pack(fill='x')
        entry.focus_set()

    create_button = _button(bottom, '+ 새 모음 만들기', show_create)
    create_button.pack(fill='x')
    canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
    bar = ttk.Scrollbar(win, command=canvas.yview)
    bar.pack(side='right', fill='y')
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(fill='both', expand=True, padx=20)
    options = tk.Frame(canvas, bg=BG)
    item = canvas.create_window(0, 0, anchor='nw', window=options)
    options.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>', lambda event: canvas.itemconfigure(item, width=event.width))
    def wheel(event):
        if str(event.widget).startswith(str(options)) or event.widget is canvas:
            canvas.yview_scroll(-int(event.delta / 120), 'units')
            return 'break'
    win.bind('<MouseWheel>', wheel, add='+')
    collections = app.file_memory.list_collections()
    for collection in collections:
        button = _button(options, f"{collection['name']}  ·  {collection['file_count']}개",
                         lambda row=collection: finish(row['id'], row['name']))
        button.configure(anchor='w', justify='left', wraplength=360)
        button.pack(fill='x', pady=3)
    if not collections:
        _label(options, '자주 쓰는 서류, 이번 프로젝트처럼\n편한 이름을 붙여 주세요.', 12, MUTED).pack(fill='x', pady=10)
        show_create()
    apply_fonts(win, getattr(app, 'settings', {}).get('text_scale', 1.0))
    return win
