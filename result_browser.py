"""Readable file choices and an inline document preview, with details on demand."""
from datetime import datetime
from pathlib import Path
import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import weakref

from PIL import ImageTk
from document_preview import render_document_preview
from search_status import coverage_text
from result_grouping import ResultGrouping
from file_transfer import bind_file_drag, COPY_MESSAGE

BG = '#FFFDF9'
INK = '#292A28'
MUTED = '#65675F'
LINE = '#E3E4DC'
WELL = '#F1F2ED'
SELECTED = '#FFF0E8'
ACCENT = '#B9382B'
FONT = '맑은 고딕'


def is_match(row):
    return row.get('group', '일치하는 파일') == '일치하는 파일'


def folder_name(value):
    path = Path(value)
    return '바탕화면' if path.name.casefold() == 'desktop' else path.name or str(path)


def file_description(row):
    """Use document evidence, never infer a pay period from the file modification date."""
    fields = row.get('fields') or {}
    kind = fields.get('document_type') or row.get('document_type') or row.get('manual_category') or ''
    if not kind and row.get('category') in {'급여명세서', '인보이스', '영수증', '계약서', '제안서'}:
        kind = row['category']
    if not isinstance(kind, str):
        kind = ''
    if not kind and any(term in (row.get('body') or '')[:4000] for term in ('급여명세서', '급여 명세서', '임금명세서')):
        kind = '급여명세서'
    parent = folder_name(Path(row['path']).parent)
    suffix = Path(row['path']).suffix.lstrip('.').upper()
    prefix = f'내용: {kind}' if kind and kind not in row.get('name', '') else suffix
    return ' · '.join(part for part in (prefix, parent) if part)


class ResultBrowser(ResultGrouping):
    PAGE_SIZE = 30

    def __init__(self, app, rows, context_title=None, pinned_view=False):
        self.app = app
        self.context_title = context_title
        self.pinned_view = pinned_view
        self.rows = list(rows)
        self.visible = []
        self.displayed_rows = []
        self.file_buttons = []
        self.cards = {}
        self.selected_path = None
        self.photo = None
        self.preview_result = None
        self.preview_key = None
        self.page_index = 0
        self.page_count = 0
        self.candidates_open = bool(rows) and not any(is_match(row) for row in rows)
        self.visible_count = self.PAGE_SIZE
        self.details_open = False
        self.search_query = None if context_title else getattr(app, 'last_submitted', '')
        self.pending = False
        self.closed = False
        self.generation = 0
        self._resize_timer = None
        self._poll_timer = None
        self._search_timer = None
        self._sash_timer = None
        self._requests = queue.Queue(maxsize=1)
        self._responses = queue.Queue()
        self._coverage = {}
        if not hasattr(app, 'result_browsers'):
            app.result_browsers = weakref.WeakSet()
        app.result_browsers.add(self)

        self.win = tk.Toplevel(app.root)
        self.win.title(context_title or '짱구가 찾은 파일')
        self.win.configure(bg=BG)
        from accessibility import fit_window
        fit_window(self.win, 1080, 820, 620, 480)
        self.win.bind('<Destroy>', self._destroy, add='+')
        self.win.bind('<Escape>', lambda event: self.win.destroy())
        top = tk.Frame(self.win, bg=BG, padx=22, pady=14)
        top.pack(fill='x')
        self.heading=self._label(top, context_title or '뭘 찾아줄까?', 18, weight='bold')
        self.heading.pack(anchor='w', pady=(0, 10))
        search = tk.Frame(top, bg=BG)
        search.pack(fill='x')
        self.query = tk.StringVar(master=self.win, value=self.search_query or '')
        self.search_button = self._button(search, '찾기', self.submit_search)
        self.search_button.pack(side='right', padx=(10, 0))
        self.entry = tk.Entry(search, textvariable=self.query, font=(FONT, 14),
                              bg='white', fg=INK, relief='solid', bd=1, insertwidth=2)
        self.entry.pack(side='left', fill='x', expand=True, ipady=9)
        from ime_entry import attach
        self.ime = attach(self.entry)
        self.entry.bind('<Return>', lambda event: self.submit_search())
        count_line=tk.Frame(top,bg=BG);count_line.pack(fill='x',pady=(12,0))
        self.setup_grouping(count_line,top)
        self.count = self._label(count_line, '', 13, weight='bold')
        self.count.pack(side='left')
        self.coverage = self._label(top, '', 10, fg=MUTED)
        self.coverage.pack(anchor='w', pady=(3, 0))

        self.pane = tk.PanedWindow(self.win, orient='horizontal', sashwidth=7,
                                  bg=LINE, bd=0, opaqueresize=True)
        self.pane.pack(fill='both', expand=True)
        self.left = tk.Frame(self.pane, bg=BG)
        self.right = tk.Frame(self.pane, bg=WELL, padx=18, pady=16)
        self.pane.add(self.left, minsize=220, width=390, stretch='always')
        self.pane.add(self.right, minsize=270, stretch='always')
        self.list_canvas = tk.Canvas(self.left, bg=BG, highlightthickness=0, width=320)
        list_bar = ttk.Scrollbar(self.left, command=self.list_canvas.yview)
        list_bar.pack(side='right', fill='y')
        self.list_canvas.configure(yscrollcommand=list_bar.set)
        self.list_canvas.pack(fill='both', expand=True)
        self.list_frame = tk.Frame(self.list_canvas, bg=BG, padx=12, pady=8)
        self.list_item = self.list_canvas.create_window(0, 0, anchor='nw', window=self.list_frame)
        self.list_frame.bind('<Configure>', lambda e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox('all')))
        self.list_canvas.bind('<Configure>', self._fit_list)

        preview_head = tk.Frame(self.right, bg=WELL)
        preview_head.pack(fill='x', pady=(0, 12))
        self.pin_button=self._button(preview_head,'☆ 고정하기',self.toggle_pinned,bg=WELL)
        self.pin_button.configure(font=(FONT,10),padx=10,pady=7)
        self.pin_button.pack(side='right',padx=(8,0))
        self.title = self._label(preview_head, '파일을 골라 주세요', 13, bg=WELL, weight='bold')
        self.title.pack(side='left', fill='x', expand=True)
        self.badge = self._label(self.right, '', 10, bg=WELL, fg='#806027')
        self.final_badge = self._label(self.right, '', 10, bg=WELL, fg='#287154')
        # Reserve space for the open action before the expanding document area.
        self.details_panel = tk.Frame(self.right, bg=WELL)
        self.details_panel.pack(side='bottom', fill='x')
        primary_actions=tk.Frame(self.details_panel,bg=WELL);primary_actions.pack(fill='x',pady=(8,0))
        self.open_button=self._button(primary_actions,'파일 열기',self.open,primary=True)
        self.open_button.pack(side='left')
        self.copy_button=self._button(primary_actions,'파일 복사',self.copy_selected,bg=BG)
        self.copy_button.pack(side='left',padx=(8,0))
        self.transfer_note=self._label(self.details_panel,'파일 이름을 첨부할 곳으로 끌어 놓으세요.',10,bg=WELL,fg=MUTED)
        self.transfer_note.pack(fill='x',pady=(5,0))
        detail_actions=tk.Frame(self.details_panel,bg=WELL);detail_actions.pack(fill='x')
        self.collection_button=self._button(detail_actions,'내 모음에 담기',self.add_to_collection,bg=WELL)
        self.collection_button.configure(font=(FONT,10),padx=5,pady=5)
        self.collection_button.pack(side='right')
        self.details_button = self._button(detail_actions, '▸ 파일 정보', self.toggle_details, bg=WELL)
        self.details_button.configure(font=(FONT, 10), padx=0, pady=5)
        self.details_button.pack(side='left')
        self.details_content = tk.Frame(self.details_panel, bg=WELL)
        self.details_text = tk.Text(self.details_content, font=(FONT, 10), height=6, wrap='word',
                                    bg=BG, fg=MUTED, relief='flat', padx=10, pady=8)
        details_bar = ttk.Scrollbar(self.details_content, command=self.details_text.yview)
        details_bar.pack(side='right', fill='y')
        self.details_text.configure(yscrollcommand=details_bar.set)
        self.details_text.pack(fill='both', expand=True)
        self.folder_button = self._button(self.details_content, '폴더에서 보기', self.reveal, bg=WELL)
        self.folder_button.configure(font=(FONT, 10), pady=5)
        self.folder_button.pack(anchor='w', pady=(3, 0))
        self.compare_button=self._button(self.details_content,'다른 파일과 비교',self.compare,bg=WELL)
        self.compare_button.configure(font=(FONT,10),pady=5)
        self.compare_button.pack(anchor='w')
        self.final_button=self._button(self.details_content,'내 최종본으로 표시',self.toggle_final,bg=WELL)
        self.final_button.configure(font=(FONT,10),pady=5)
        self.final_button.pack(anchor='w')
        self.mail_button=self._button(self.details_content,'원본 메일 보기',self.open_mail_source,bg=WELL)
        self.mail_button.configure(font=(FONT,10),pady=5)
        self.page_nav = tk.Frame(self.right, bg=WELL)
        self.previous_page = self._button(self.page_nav, '이전 장', lambda: self.change_page(-1), bg=WELL)
        self.previous_page.pack(side='left')
        self.page_label = self._label(self.page_nav, '', 10, bg=WELL)
        self.page_label.pack(side='left', expand=True)
        self.next_page = self._button(self.page_nav, '다음 장', lambda: self.change_page(1), bg=WELL)
        self.next_page.pack(side='right')
        self.preview_area = tk.Frame(self.right, bg=WELL)
        self.preview_area.pack(fill='both', expand=True)
        self.preview_canvas = tk.Canvas(self.preview_area, bg=WELL, highlightthickness=0, width=360, height=260)
        self.preview_bar = ttk.Scrollbar(self.preview_area, command=self.preview_canvas.yview)
        self.preview_canvas.configure(yscrollcommand=self.preview_bar.set)
        self.preview_canvas.bind('<Configure>', self._fit_preview)
        self.body = tk.Text(self.preview_area, wrap='word', font=(FONT, 12), bg='white', fg=INK,
                            relief='flat', padx=18, pady=18, height=5, width=20, state='disabled')
        self.body_bar = ttk.Scrollbar(self.preview_area, command=self.body.yview)
        self.body.configure(yscrollcommand=self.body_bar.set)
        self.empty_label = self._label(self.preview_area, '', 12, bg=WELL, fg=MUTED)
        self.win.bind('<MouseWheel>', self._wheel, add='+')
        self.win.bind('<Configure>', self._fit_window, add='+')
        self.win.bind('<FocusIn>',self._pin_focus,add='+')
        threading.Thread(target=self._render_worker, args=(self._requests, self._responses), daemon=True).start()
        self._poll_timer = self.win.after(60, self._poll_preview)
        self.refresh()
        self.update_coverage(getattr(getattr(app, 'library', None), 'search_coverage', {}))
        self.apply_readability()
        self._sash_timer = self.win.after_idle(lambda: self._set_sash(self.win.winfo_width() < 850) if not self.closed else None)

    @staticmethod
    def _label(parent, text, size, bg=BG, fg=INK, weight='normal'):
        return tk.Label(parent, text=text, font=(FONT, size, weight), bg=bg, fg=fg, anchor='w', justify='left')

    @staticmethod
    def _button(parent, text, command, primary=False, bg=BG):
        fill = ACCENT if primary else bg
        color = 'white' if primary else INK
        return tk.Button(parent, text=text, command=command, font=(FONT, 12),
                         bg=fill, fg=color, activebackground=fill, activeforeground=color,
                         relief='flat', bd=0, padx=17, pady=10, cursor='hand2')

    def apply_readability(self):
        from accessibility import apply_fonts
        apply_fonts(self.win, getattr(self.app, 'settings', {}).get('text_scale', 1.0))
        self._fit_list()

    def _fit_window(self, event):
        if event.widget is not self.win:
            return
        narrow = event.width < 850
        orientation = 'vertical' if narrow else 'horizontal'
        if str(self.pane.cget('orient')) != orientation:
            self.pane.configure(orient=orientation)
            self.pane.paneconfigure(self.left, minsize=90 if narrow else 220)
            self.pane.paneconfigure(self.right, minsize=170 if narrow else 270)
            if self._sash_timer:
                self.win.after_cancel(self._sash_timer)
            self._sash_timer = self.win.after_idle(lambda: self._set_sash(narrow))
        self.coverage.configure(wraplength=max(230, event.width - 48))
        self.group_note.configure(wraplength=max(230,event.width-48))
        self.title.configure(wraplength=max(80,self.right.winfo_width()-self.pin_button.winfo_reqwidth()-55))
        self.transfer_note.configure(wraplength=max(180,self.right.winfo_width()-40))

    def _set_sash(self, narrow):
        self._sash_timer = None
        if not self.closed:
            if narrow:
                self.pane.sash_place(0, 0, max(90, int(self.pane.winfo_height() * .36)))
            else:
                self.pane.sash_place(0, int(self.pane.winfo_width() * .38), 0)

    def _fit_list(self, event=None):
        width = event.width if event else self.list_canvas.winfo_width()
        self.list_canvas.itemconfigure(self.list_item, width=max(1, width))
        for card, button, meta, marker in self.cards.values():
            button.configure(wraplength=max(140, width - 62))
            meta.configure(wraplength=max(140, width - 62))
        self.title.configure(wraplength=max(80,self.right.winfo_width()-self.pin_button.winfo_reqwidth()-55))
        self.transfer_note.configure(wraplength=max(180,self.right.winfo_width()-40))

    def _wheel(self, event):
        if str(event.widget).startswith(str(self.left)):
            if self.list_canvas.yview() != (0.0, 1.0):
                self.list_canvas.yview_scroll(-int(event.delta / 120), 'units')
            return 'break'
        if event.widget is self.preview_canvas:
            self.preview_canvas.yview_scroll(-int(event.delta / 120), 'units')
            return 'break'

    def update_coverage(self, coverage):
        self._coverage = dict(coverage or {})
        if self.context_title:
            self._coverage={}
            self.coverage.configure(text='앱을 다시 켜도 여기에 남아요. 원본 파일은 제자리에 있어요.' if self.pinned_view else '짱구로 연 파일이에요. 파일을 누르면 내용을 미리 볼 수 있어요.' if self.context_title=='최근 연 파일' else '원래 위치에 있는 파일을 모아 보여요. 파일을 누르면 미리 볼 수 있어요.')
            return
        names = list(dict.fromkeys(folder_name(root) for root in self._coverage.get('roots', [])))
        scope = ' · '.join(names[:2]) + (' 외' if len(names) > 2 else '')
        prefix = scope + '에서 ' if scope else ''
        if getattr(self.app, 'indexing', False):
            text = prefix + '더 찾는 중이에요. 찾으면 여기에 추가돼요.'
        elif self._coverage.get('pending', 0) or self._coverage.get('unread_documents', 0):
            text = prefix + '찾았어요. 아직 내용을 확인하지 못한 파일이 있어요.'
        else:
            text = prefix + '찾은 결과예요.' if scope else '파일을 누르면 내용을 미리 볼 수 있어요.'
        self.coverage.configure(text=text)
        if self.details_open:
            self._fill_details()

    def submit_search(self):
        if self.closed:
            return
        return self.ime.commit_then(self._submit_search) if self.ime else self._submit_search()

    def _submit_search(self):
        if self.closed:
            return
        query = self.query.get().strip()
        if not query:
            self.coverage.configure(text='찾을 내용을 입력해 주세요. 예: 급여명세서')
            self.entry.focus_set()
            return
        if getattr(self.app, 'busy', False):
            self.coverage.configure(text='지금 하던 작업이 끝나면 다시 찾아 주세요.')
            return
        self._previous_search = self.search_query
        self.search_query = query
        self.pending = True
        self.search_button.configure(state='disabled')
        self.count.configure(text='찾는 중이에요…')
        self.app.query.set(query)
        try:
            self.app.do_search(speak=False)
        except Exception:
            self._search_failed()
            raise
        if getattr(self.app, 'is_photo_search', False):
            self.pending = False
            self.search_query = self._previous_search
            self.query.set(self.search_query or '')
            self.search_button.configure(state='normal')
            self.refresh(preserve_view=True)
            self.coverage.configure(text='사진 모음에서 검색 결과를 보여드릴게요.')
            return
        if not self.closed:
            self._search_timer = self.win.after(100, self._check_search)

    def _check_search(self):
        self._search_timer = None
        if not self.pending or self.closed:
            return
        if getattr(self.app, 'busy', False):
            self._search_timer = self.win.after(100, self._check_search)
        elif (getattr(self.app, 'search_fx', None) or {}).get('outcome') == 'error':
            self._search_failed()
        elif getattr(self.app, 'last_submitted', None) == self.search_query:
            self.update_results(getattr(self.app, 'result_cache', []), self.search_query)
        else:
            self._search_failed()

    def _search_failed(self):
        self.pending = False
        self.search_query = self._previous_search
        self.search_button.configure(state='normal')
        if self.pinned_view:self.rows=self.app.pinned_result_rows()
        self.refresh(preserve_view=True)
        self.coverage.configure(text='새 검색을 마치지 못했어요. 이전 결과를 보여드릴게요.')

    def update_results(self, rows, query):
        if self.closed or query != self.search_query:
            return
        was_pending = self.pending
        if was_pending:
            self.context_title=None
            self.pinned_view=False
            self.heading.configure(text='뭘 찾아줄까?')
            self.win.title('짱구가 찾은 파일')
        self.pending = False
        self.search_button.configure(state='normal')
        self.update_coverage(getattr(self.app.library, 'search_coverage', {}))
        if self.rows != rows or was_pending:
            self.rows = list(rows)
            if was_pending:
                self.visible_count = self.PAGE_SIZE
                self.candidates_open = bool(rows) and not any(is_match(row) for row in rows)
            self.refresh(preserve_view=not was_pending)
            if self.use_groups:self.start_grouping()

    def refresh(self, preserve_view=False, preserve_selection=True):
        if self.closed:
            return
        scroll = self.list_canvas.yview()
        self.visible = self.grouping_rows()
        matched = [row for row in self.visible if is_match(row)]
        candidates = [row for row in self.visible if not is_match(row)]
        if preserve_view and preserve_selection and self.selected_path:
            for group in (matched, candidates):
                position = next((i for i, row in enumerate(group) if row['path'] == self.selected_path), None)
                if position is not None:
                    self.visible_count = max(self.visible_count, position + 1)
                    if group is candidates:
                        self.candidates_open = True
        self.match_count = len(matched)
        self.count.configure(text=(f'{len(self.visible)}개 파일' if self.context_title else f'{len(matched)}개 찾았어요' if matched else
                                   f'비슷한 파일 {len(candidates)}개 있어요' if candidates else
                                   '현재 확인한 파일에서는 찾지 못했어요'))
        if self.use_groups and self.identical_groups:
            self.count.configure(text=f'파일 {len(self.rows)}개 · 묶어서 보기')
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        self.cards = {}
        self.file_buttons = []
        self.displayed_rows = []
        for row in matched[:self.visible_count]:
            self._add_file(row)
        if len(matched) > self.visible_count:
            self._button(self.list_frame, f'{len(matched) - self.visible_count}개 더 보기', self.show_more).pack(fill='x', pady=8)
        if candidates:
            self.candidate_button = self._button(self.list_frame,
                f'{"▾" if self.candidates_open else "▸"} 비슷한 파일 {len(candidates)}개 더 보기', self.toggle_candidates)
            self.candidate_button.configure(font=(FONT, 11), anchor='w', wraplength=260)
            self.candidate_button.pack(fill='x', pady=(16, 6))
            if self.candidates_open:
                self._label(self.list_frame, '찾던 파일인지 내용을 확인해 주세요.', 10, fg=MUTED).pack(anchor='w', padx=12, pady=(0, 8))
                for row in candidates[:self.visible_count]:
                    self._add_file(row)
                if len(candidates) > self.visible_count:
                    self._button(self.list_frame, '비슷한 파일 더 보기', self.show_more).pack(fill='x', pady=8)
        if not self.visible:
            empty='자주 쓰는 파일의 ☆를 눌러 보세요.\n앱을 다시 켜도 여기에 남아요.' if self.pinned_view else '짱구로 파일을 열면 여기에 남아요.' if self.context_title=='최근 연 파일' else '아직 담은 파일이 없어요.' if self.context_title else '다른 말로 찾아볼까요?'
            note=self._label(self.list_frame, empty, 12, fg=MUTED);note.configure(wraplength=260)
            note.pack(anchor='w', padx=12, pady=20)
        allowed = {row['path'] for row in self.displayed_rows}
        preferred = self.selected_path if self.selected_path in allowed else (self.displayed_rows[0]['path'] if self.displayed_rows else None)
        self.apply_readability()
        self.select(preferred)
        if preserve_view and scroll:
            self.list_canvas.update_idletasks()
            self.list_canvas.yview_moveto(scroll[0])
        else:
            self.list_canvas.yview_moveto(0)

    def _add_file(self, row):
        path = row['path']
        self.displayed_rows.append(row)
        card = tk.Frame(self.list_frame, bg=BG, pady=8)
        card.pack(fill='x', pady=2)
        marker = tk.Frame(card, bg=BG, width=3)
        marker.pack(side='left', fill='y')
        content = tk.Frame(card, bg=BG)
        content.pack(fill='x', expand=True, padx=9)
        button = self._button(content, row.get('name') or Path(path).name, lambda p=path: self.select(p))
        button.configure(anchor='w', justify='left', padx=3, pady=6, wraplength=290)
        button.pack(fill='x')
        meta = self._label(content, '파일을 찾을 수 없어요 · 고정 해제 가능' if self.pinned_view and row.get('available') is False else file_description(row), 10, fg=MUTED)
        meta.configure(wraplength=290, padx=3)
        meta.pack(fill='x', pady=(0, 7))
        self.add_group_action(content,row)
        for widget in (card, content, meta):
            widget.bind('<Button-1>', lambda event, p=path: self.select(p))
        button.bind('<Return>', lambda event, p=path: self.select(p))
        button.bind('<Double-Button-1>', lambda event, p=path: self._open_path(p))
        button.bind('<Down>', lambda event, p=path: self._move_selection(p, 1))
        button.bind('<Up>', lambda event, p=path: self._move_selection(p, -1))
        button.bind('<Control-c>',lambda event,p=path:self.copy_path(p))
        button.bind('<Button-3>',lambda event,p=path:self.file_menu(event,p))
        if not bind_file_drag(button,lambda p=path:[p],self.transfer_status):
            self.transfer_status('파일 복사 후 붙일 곳에서 Ctrl+V를 누르세요.')
        tk.Frame(self.list_frame, bg=LINE, height=1).pack(fill='x', padx=3)
        self.cards[path] = (card, button, meta, marker)
        self.file_buttons.append(button)

    def _move_selection(self, path, delta):
        paths = [row['path'] for row in self.displayed_rows]
        index = max(0, min(len(paths) - 1, paths.index(path) + delta))
        self.select(paths[index])
        self.cards[paths[index]][1].focus_set()
        card = self.cards[paths[index]][0]
        top = self.list_canvas.canvasy(0)
        y = card.winfo_y()
        height = max(1, self.list_frame.winfo_height())
        if y < top:
            self.list_canvas.yview_moveto(y / height)
        elif y + card.winfo_height() > top + self.list_canvas.winfo_height():
            self.list_canvas.yview_moveto((y + card.winfo_height() - self.list_canvas.winfo_height()) / height)
        return 'break'

    def show_more(self):
        self.visible_count += self.PAGE_SIZE
        self.refresh(preserve_view=True)

    def toggle_candidates(self):
        self.candidates_open = not self.candidates_open
        self.refresh(preserve_view=True, preserve_selection=False)

    def selected(self):
        return next((row for row in self.rows if row['path'] == self.selected_path), None)

    def select(self, path):
        if path != self.selected_path:
            self.page_index = 0
        self.selected_path = path
        for value, (card, button, meta, marker) in self.cards.items():
            color = SELECTED if value == path else BG
            card.configure(bg=color)
            button.master.configure(bg=color)
            button.configure(bg=color, activebackground=color)
            meta.configure(bg=color)
            marker.configure(bg=ACCENT if value == path else color)
        self.preview()

    def preview(self):
        row = self.selected()
        available=bool(row and Path(row['path']).is_file())
        self._update_pin_button(row,available)
        self.final_state_changed()
        self.open_button.configure(state='normal' if available else 'disabled')
        self.copy_button.configure(state='normal' if available else 'disabled')
        self.collection_button.configure(state='normal' if available and hasattr(self.app,'add_to_collection') else 'disabled')
        self.compare_button.configure(state='normal' if available else 'disabled')
        self.folder_button.configure(state='normal' if row else 'disabled')
        self.title.configure(text=row.get('name', Path(row['path']).name) if row else '파일을 골라 주세요')
        self.badge.pack_forget()
        if row and not is_match(row):
            self.badge.configure(text='비슷한 파일이에요. 내용을 확인해 주세요.')
            self.badge.pack(fill='x', before=self.preview_area, pady=(0, 8))
        self._fill_details()
        key = (row['path'], row.get('mtime'), row.get('size'), row.get('body'), self.page_index,available) if row else None
        if key == self.preview_key and row:
            return
        self.preview_key = key
        self.generation += 1
        self.preview_result = None
        self.photo = None
        self.page_count = 0
        self.page_nav.pack_forget()
        self._hide_preview()
        if not row:
            self.empty_label.configure(text='파일을 골라 주세요.' if self.rows else '☆를 누른 파일이 여기에 모여요.' if self.pinned_view else '다른 검색어로 다시 찾아보세요.')
            self.empty_label.pack(fill='both', expand=True)
            return
        if not available:
            self.empty_label.configure(text='파일이 이동되었거나 삭제됐어요.\n고정 해제로 목록에서 뺄 수 있어요.' if self._is_pinned(row['path']) else '파일이 이동되었거나 삭제됐어요.\n다시 찾아 주세요.')
            self.empty_label.pack(fill='both',expand=True)
            return
        self.empty_label.configure(text='문서를 불러오는 중이에요…')
        self.empty_label.pack(fill='both', expand=True)
        request = (self.generation, dict(row), self.page_index)
        try:
            self._requests.get_nowait()
        except queue.Empty:
            pass
        self._requests.put_nowait(request)

    def _hide_preview(self):
        for widget in (self.empty_label, self.preview_canvas, self.preview_bar, self.body, self.body_bar):
            widget.pack_forget()

    @staticmethod
    def _render_worker(requests, responses):
        while True:
            request = requests.get()
            if request is None:
                return
            generation, row, page = request
            try:
                result = render_document_preview(row, page, max_size=(1300, 1800))
                responses.put((generation, result, None))
            except Exception:
                responses.put((generation, None, '미리보기를 불러오지 못했어요. 파일을 열어 확인해 주세요.'))

    def _poll_preview(self):
        self._poll_timer = None
        if self.closed:
            return
        try:
            while True:
                generation, result, error = self._responses.get_nowait()
                if generation != self.generation:
                    continue
                self._hide_preview()
                self.preview_result = result
                if result is not None and result.image is not None:
                    self.page_index = result.page
                    self.page_count = result.page_count
                    self.preview_bar.pack(side='right', fill='y')
                    self.preview_canvas.pack(fill='both', expand=True)
                    self._draw_preview()
                    self.preview_canvas.yview_moveto(0)
                    if self.page_count > 1:
                        self.page_label.configure(text=f'{self.page_index + 1} / {self.page_count}쪽')
                        self.previous_page.configure(state='normal' if self.page_index > 0 else 'disabled')
                        self.next_page.configure(state='normal' if self.page_index + 1 < self.page_count else 'disabled')
                        self.page_nav.pack(fill='x', before=self.preview_area, pady=(0, 8))
                else:
                    message = error or (result.error if result else '')
                    text = ((self.selected() or {}).get('body') or '').strip()
                    self.body.configure(state='normal')
                    self.body.delete('1.0', 'end')
                    self.body.insert('1.0', (message + '\n\n' if message else '') +
                                     ('문서에서 읽은 내용\n\n' + text[:30000] if text else '파일 열기를 눌러 내용을 확인해 주세요.'))
                    self.body.configure(state='disabled')
                    self.body_bar.pack(side='right', fill='y')
                    self.body.pack(fill='both', expand=True)
        except queue.Empty:
            pass
        self._poll_timer = self.win.after(60, self._poll_preview)

    def _fit_preview(self, event=None):
        if self._resize_timer:
            self.win.after_cancel(self._resize_timer)
        self._resize_timer = self.win.after(100, self._draw_preview)

    def _draw_preview(self):
        self._resize_timer = None
        if self.closed or not self.preview_result or self.preview_result.image is None:
            return
        original = self.preview_result.image
        width = max(160, self.preview_canvas.winfo_width() - 12)
        scale = min(1.0, width / original.width)
        image = original.resize((max(1, round(original.width * scale)), max(1, round(original.height * scale))))
        self.photo = ImageTk.PhotoImage(image, master=self.win)
        self.preview_canvas.delete('all')
        self.preview_canvas.create_image(max(6, (self.preview_canvas.winfo_width() - image.width) // 2), 0,
                                         anchor='nw', image=self.photo)
        self.preview_canvas.configure(scrollregion=(0, 0, image.width, image.height + 10))

    def change_page(self, delta):
        page = max(0, min(self.page_count - 1, self.page_index + delta))
        if page != self.page_index:
            self.page_index = page
            self.preview()

    def toggle_details(self):
        self.details_open = not self.details_open
        self.details_button.configure(text=('▾' if self.details_open else '▸') + ' 파일 정보')
        if self.details_open:
            self._fill_details()
            self.details_content.pack(fill='x')
        else:
            self.details_content.pack_forget()

    def _fill_details(self):
        row = self.selected()
        text = ''
        if row:
            try:
                date = datetime.fromtimestamp(row['mtime']).strftime('%Y년 %m월 %d일') if row.get('mtime') else '확인할 수 없어요'
            except (ValueError, OSError, OverflowError):
                date = '확인할 수 없어요'
            text = (f"저장된 곳\n{row['path']}\n\n수정한 날짜\n{date}\n\n찾은 이유\n" +
                    row.get('reason', '이름 또는 문서 내용에서 찾았어요.') + '\n\n검색한 곳\n' +
                    '\n'.join(self._coverage.get('roots', [])) + '\n\n' + coverage_text(self._coverage))
        source=self._mail_source(row)
        self.mail_button.pack_forget()
        if source:
            text+='\n\n받은 메일\n보낸 사람: '+str(source.get('sender',''))
            text+='\n제목: '+str(source.get('subject',''))+'\n메일에 적힌 날짜: '+str(source.get('date',''))
            text+='\n메일에서 가져온 사본이에요.'
            if source.get('source_eml'):
                self.mail_button.configure(text='원본 메일 보기',state='normal' if Path(source['source_eml']).is_file() else 'disabled')
                self.mail_button.pack(anchor='w')
            elif self._mailbox_url(source):
                self.mail_button.configure(text='메일함 열기',state='normal')
                self.mail_button.pack(anchor='w')
        self.details_text.configure(state='normal')
        self.details_text.delete('1.0', 'end')
        self.details_text.insert('1.0', text)
        self.details_text.configure(state='disabled')

    def _mail_source(self,row=None):
        row=row or self.selected()
        lookup=getattr(self.app,'mail_source',None)
        return lookup(row['path']) if row and callable(lookup) else None

    def open_mail_source(self):
        source=self._mail_source()
        if source and source.get('source_eml'):
            self.app.open_file(source['source_eml'])
        elif source and self._mailbox_url(source):
            import webbrowser
            webbrowser.open(self._mailbox_url(source))

    @staticmethod
    def _mailbox_url(source):
        from urllib.parse import urlsplit
        url=str(source.get('webmail_url',''))
        parsed=urlsplit(url)
        allowed={'mail.naver.com','mail.google.com','mail.worksmobile.com','outlook.office.com','outlook.live.com'}
        return url if parsed.scheme=='https' and parsed.hostname in allowed and not parsed.username else ''

    def final_state_changed(self):
        if self.closed:return
        row=self.selected()
        store=getattr(self.app,'final_versions',None)
        try:
            description=store.describe(row['path']) if row and store is not None else {'state':'none','label':''}
        except (OSError,ValueError,sqlite3.Error):
            description={'state':'unknown','label':'최종본 표시를 확인할 수 없어요.'}
        marked=description['state']!='none'
        self.final_badge.pack_forget()
        if marked:
            self.final_badge.configure(text='✓ '+description['label'] if description['state']=='final' else description['label'],
                                       fg='#287154' if description['state']=='final' else '#806027')
            self.final_badge.pack(fill='x',before=self.preview_area,pady=(0,8))
        enabled=store is not None and row and (marked or Path(row['path']).is_file())
        self.final_button.configure(text='최종본 표시 해제' if marked else '내 최종본으로 표시',
                                    state='normal' if enabled else 'disabled')

    def toggle_final(self):
        row=self.selected()
        store=getattr(self.app,'final_versions',None)
        if not row or store is None:return
        try:
            if store.describe(row['path'])['state']!='none':store.clear(row['path'])
            else:store.mark(row['path'],candidates=self.rows)
        except (OSError,ValueError,sqlite3.Error) as error:
            messagebox.showerror('최종본 표시',str(error),parent=self.win)
            return
        update=getattr(self.app,'final_versions_changed',None)
        if callable(update):update()
        else:self.final_state_changed()

    def _open_path(self, path):
        self.select(path)
        self.open()
        return 'break'

    def add_to_collection(self):
        row=self.selected()
        if row and Path(row['path']).is_file():
            self.app.add_to_collection([row['path']])

    def _is_pinned(self,path):
        check=getattr(self.app,'is_pinned',None)
        return bool(check(path)) if check else False

    def _update_pin_button(self,row=None,available=None):
        row=row or self.selected()
        pinned=bool(row and self._is_pinned(row['path']))
        if available is None:available=bool(row and Path(row['path']).is_file())
        enabled=callable(getattr(self.app,'toggle_pinned',None)) and (pinned or available)
        self.pin_button.configure(text='★ 고정 해제' if pinned else '☆ 고정하기',state='normal' if enabled else 'disabled')

    def toggle_pinned(self):
        row=self.selected()
        if row:
            state=self.app.toggle_pinned(row['path'],parent=self.win)
            if state is not None:
                self.transfer_status('고정했어요. 처음 화면에서 바로 열 수 있어요.' if state else '고정을 해제했어요. 원본 파일은 그대로예요.')

    def pin_state_changed(self):
        if self.closed:return
        if self.pinned_view and not self.pending:
            self.update_results(self.app.pinned_result_rows(),self.search_query)
        self._update_pin_button()

    def _pin_focus(self,event):
        if event.widget is self.win:
            self.pin_state_changed()
            self.final_state_changed()

    def transfer_status(self,text):
        if not self.closed:self.transfer_note.configure(text=text)

    def copy_selected(self):
        row=self.selected()
        if row:return self.copy_path(row['path'])
        return 'break'

    def copy_path(self,path):
        if self.app.copy_result_files([path],parent=self.win):
            self.transfer_status(COPY_MESSAGE)
        return 'break'

    def file_menu(self,event,path):
        self.select(path)
        previous=getattr(self,'file_context_menu',None)
        if previous is not None:previous.destroy()
        menu=tk.Menu(self.win,tearoff=False,font=(FONT,12))
        self.file_context_menu=menu
        menu.add_command(label='파일 열기',command=lambda p=path:self.app.open_file(p))
        menu.add_command(label='파일 복사',command=lambda p=path:self.copy_path(p))
        if callable(getattr(self.app,'toggle_pinned',None)):
            menu.add_command(label='고정 해제' if self._is_pinned(path) else '고정하기',
                             command=lambda p=path:self.app.toggle_pinned(p,parent=self.win))
        menu.add_command(label='폴더에서 보기',command=lambda p=path:self.app.reveal(p))
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()

    def compare(self):
        row=self.selected()
        if row and Path(row['path']).is_file():
            if hasattr(self.app,'compare_file'):return self.app.compare_file(row,self.rows)
            from file_compare_ui import show_comparison
            from file_comparison import version_candidates
            return show_comparison(self.app,row,version_candidates(row,self.rows))

    def open(self, preview=False):
        row = self.selected()
        if row:
            self.app.open_file(row['path'])

    def reveal(self):
        row = self.selected()
        if row:
            self.app.reveal(row['path'])

    def _destroy(self, event):
        if event.widget is not self.win or self.closed:
            return
        self.closed = True
        self.stop_grouping()
        self.generation += 1
        for timer in (self._poll_timer, self._resize_timer, self._search_timer, self._sash_timer):
            if timer:
                self.win.after_cancel(timer)
        self._poll_timer = self._resize_timer = self._search_timer = self._sash_timer = None
        try:
            self._requests.get_nowait()
        except queue.Empty:
            pass
        self._requests.put_nowait(None)
        self.app.result_browsers.discard(self)
        self.photo = None
        self.preview_result = None
        # A closed browser may remain in a caller's history or a reference cycle.
        # Release Tcl-owned objects here on the UI thread, before a later worker
        # allocation can trigger Python's cyclic garbage collector.
        self.query = None
        self.ime = None
