"""Photo-first, paginated search results with local, bounded thumbnail decoding."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, messagebox
import weakref

from PIL import Image, ImageDraw, ImageOps, ImageTk
from accessibility import apply_fonts, fit_window, scroll_page
from photo_library import PHOTO_EXTS


PAGE_SIZE = 24
BG = '#FAF8F3'
INK = '#292C3E'
ACCENT = '#6651BD'
MUTED = '#666878'
BORDER = '#E6E1D9'


def _button(parent, text, command, primary=False, **kwargs):
    return tk.Button(parent, text=text, command=command, relief='flat', bd=0,
                     bg=ACCENT if primary else '#FFFFFF', fg='white' if primary else INK,
                     activebackground='#534099' if primary else '#EDE8F9',
                     activeforeground='white' if primary else INK, cursor='hand2',
                     padx=12, pady=7, font=('맑은 고딕', 10, 'bold' if primary else 'normal'),
                     highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                     **kwargs)


def _short(value, length=27):
    value = str(value).replace('\n', ' ')
    return value if len(value) <= length else value[:length - 1] + '…'


def _badge(row):
    if row.get('status') == 'PC에 내려받지 않은 사진':
        return '다운로드 필요', '#FAE8DA', '#864723'
    if (row.get('fields') or {}).get('photo_error'):
        return '원본 확인 필요', '#FAE8DA', '#864723'
    if row.get('detected_objects'):
        return '사물 단서 발견', '#E5F1E8', '#306445'
    if '사진 색상:' in row.get('reason', ''):
        return '색상이 비슷해요', '#ECE8F8', '#59449D'
    if row.get('reason') and row.get('group') == '관련 후보':
        return '모습이 비슷한 후보', '#ECE8F8', '#59449D'
    return ('사진 후보', '#F1EEE8', '#666052') if _analyzed(row) else ('모습 분석 대기', '#F1EEE8', '#666052')


def _photo_rows(rows):
    return [row for row in rows if Path(row.get('path', '')).suffix.lower() in PHOTO_EXTS]


def _analyzed(row):
    return bool(row.get('visual')) or (row.get('fields') or {}).get('_objects_version') == 1


def _date(row):
    taken = (row.get('fields') or {}).get('taken_date')
    if taken:
        return '찍은 날 ' + str(taken).replace('-', '.')
    try:
        return '수정 ' + datetime.fromtimestamp(row['mtime']).strftime('%Y.%m.%d') if row.get('mtime') else '날짜 정보 없음'
    except (ValueError, OSError, OverflowError):
        return '날짜 정보 없음'


def _reason(row):
    if row.get('status') == 'PC에 내려받지 않은 사진':
        return 'PC에 내려받지 않은 사진 · 저장된 폴더에서 다운로드한 뒤 다시 분석해 주세요.'
    if (row.get('fields') or {}).get('photo_error'):
        return str(row['fields']['photo_error']) + ' · 원본으로 확인해 주세요.'
    reason = row.get('reason', '').strip()
    if reason:
        return reason
    return '사진 모습 분석 전 · 직접 보면서 고를 수 있어요.' if not _analyzed(row) else '사진 후보 · 원하던 모습인지 확인해 주세요.'


def _decode(path, size):
    """JPEG draft avoids decoding every full-size camera image for small cards."""
    try:
        path = Path(path)
        if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
            return None
        with Image.open(path) as image:
            if image.width * image.height > 60_000_000:
                return None
            image.draft('RGB', size)
            image.thumbnail(size, Image.Resampling.LANCZOS, reducing_gap=3)
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail(size, Image.Resampling.LANCZOS)
            return image.copy()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _decode_sources(sources, size):
    for source in sources:
        picture = _decode(source, size)
        if picture is not None:
            return picture
    return None


class PhotoGallery:
    def __init__(self, app, rows, query=None):
        self.app = app
        self.rows = _photo_rows(rows)
        self.search_query = getattr(app, 'last_submitted', '') if query is None else query
        self.visible = []
        self.page = 0
        self.selected_path = None
        self.cards = []
        self.photos = {}
        self.preview_photo = None
        self.viewer = None
        self.filter_dialog = None
        self.saved = OrderedDict()
        self._deleting_paths = set()
        self._removed_paths = set()
        self.columns = 1
        self._generation = 0
        self._closed = False
        self._short_layout = None
        self._filters_shown = False
        self._poll_id = None
        self._layout_id = None
        self._preview_layout_id = None
        self._preview_size = (320, 280)
        self._jobs = set()
        self._images = OrderedDict()
        self._queue = queue.Queue()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='photo-thumbnails')
        if not hasattr(app, 'result_browsers'):
            app.result_browsers = weakref.WeakSet()
        app.result_browsers.add(self)
        self.win = tk.Toplevel(app.root)
        self.win.title('짱구의 사진 상자 · 찾은 사진')
        self.win.configure(bg=BG)
        fit_window(self.win, 1240, 820, 620, 460)
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        self.win.bind('<Destroy>', self._destroyed, add='+')
        self.win.bind('<Escape>', lambda event: self.close())

        self.saved_only = tk.BooleanVar(value=False)
        self.card_size = tk.StringVar(value='편하게')
        header = tk.Frame(self.win, bg=BG, padx=20, pady=12)
        header.pack(fill='x')
        self.header = header
        heading_row = tk.Frame(header, bg=BG)
        heading_row.pack(fill='x')
        self.filter_toggle = _button(heading_row, '상세 조건 ▾', self.toggle_filters)
        self.filter_toggle.pack(side='right', padx=(8, 0))
        if hasattr(app, 'sprites'):
            icon = app.sprites.render(app.settings.get('outfit', 0), 60).copy()
            bounds = icon.getbbox()
            if bounds:
                icon = icon.crop(bounds)
            icon.thumbnail((32, 32), Image.Resampling.LANCZOS)
            self._brand_photo = ImageTk.PhotoImage(icon, master=self.win)
            tk.Label(heading_row, image=self._brand_photo, bg=BG).pack(side='left', padx=(0, 8))
        self.heading = tk.Label(heading_row, text='짱구의 사진 상자', font=('맑은 고딕', 20, 'bold'),
                                bg=BG, fg=INK, anchor='w')
        self.heading.pack(side='left')
        self.result_total = tk.Label(heading_row, bg=BG, fg=ACCENT, font=('맑은 고딕', 12, 'bold'))
        self.result_total.pack(side='left', padx=12)
        self.request = tk.Label(header, text=self.search_query, font=('맑은 고딕', 10), bg=BG,
                                fg=MUTED, anchor='w', justify='left', wraplength=1100)
        self.request.pack(fill='x', pady=(3, 10))

        self.refine_box = tk.Frame(header, bg='#F0ECFA', padx=12, pady=10)
        if callable(getattr(app, 'refine_photo', None)):
            self.refine_box.pack(fill='x')
            self.refine_box.columnconfigure(0, weight=1)
            self.refine_query = tk.StringVar()
            from ime_entry import attach
            self.refine_entry = ttk.Entry(self.refine_box, textvariable=self.refine_query, font=('맑은 고딕', 12))
            self.refine_entry.grid(row=0, column=0, sticky='ew', ipady=7)
            self.refine_ime = attach(self.refine_entry)
            self.refine_entry.bind('<Return>', lambda event: self._commit(self.refine_ime, self.refine))
            self.refine_submit = _button(self.refine_box, '이 결과에서 더 찾기',
                                        lambda: self._commit(self.refine_ime, self.refine), primary=True)
            self.refine_submit.grid(row=0, column=1, padx=(10, 0), sticky='ns')
            self.quick_row = tk.Frame(self.refine_box, bg='#F0ECFA')
            self.quick_row.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(7, 0))
            tk.Label(self.quick_row, text='기억나는 모습은?', bg='#F0ECFA', fg='#5B5074',
                     font=('맑은 고딕', 9)).pack(side='left', padx=(0, 8))
            for label, phrase in [('파란색', '파란색인 것만'), ('사람 없이', '사람이 없는 것만'), ('더 밝게', '더 밝은 사진')]:
                _button(self.quick_row, label, lambda text=phrase: self.quick_refine(text)).pack(side='left', padx=(0, 5))
            if callable(getattr(app, 'undo_photo_query', None)):
                ttk.Button(self.quick_row, text='조건 한 단계 뒤로', command=app.undo_photo_query).pack(side='right')

        self.filter_section = tk.Frame(header, bg='white', padx=12, pady=10,
                                       highlightthickness=1, highlightbackground=BORDER)
        filters = tk.Frame(self.filter_section, bg='white')
        filters.pack(fill='x')
        filters.columnconfigure(1, weight=1)
        tk.Label(filters, text='이름·폴더 속 단어', bg='white', fg=MUTED).grid(row=0, column=0, sticky='w')
        self.query = tk.StringVar()
        self.entry = ttk.Entry(filters, textvariable=self.query)
        self.entry.grid(row=0, column=1, sticky='ew', padx=7, ipady=4)
        from ime_entry import attach
        self.ime = attach(self.entry)
        self.entry.bind('<Return>', lambda event: self._commit(self.ime, self.refresh))
        _button(filters, '적용', lambda: self._commit(self.ime, self.refresh)).grid(row=0, column=2)
        tools = tk.Frame(self.filter_section, bg='white')
        tools.pack(fill='x', pady=(7, 0))
        self.mode = tk.StringVar(value='모든 사진')
        mode = ttk.Combobox(tools, state='readonly', textvariable=self.mode,
                           values=('모든 사진', '모습 분석 완료', '모습 분석 대기'), width=16)
        mode.pack(side='left')
        mode.bind('<<ComboboxSelected>>', lambda event: self.refresh())
        if callable(getattr(app, 'choose_photo_folder', None)):
            _button(tools, '다른 위치도 찾기', app.choose_photo_folder).pack(side='left', padx=7)
        _button(tools, '조건 초기화', self.reset).pack(side='right')
        management = tk.Menu(self.win, tearoff=False)
        for title, method in [('검색·분석 멈추기', 'cancel_photo_search'), ('분석 계속하기', 'resume_photo_analysis'), ('사진 찾기 준비', 'setup_photo_ai')]:
            callback = getattr(app, method, None)
            if callable(callback):
                management.add_command(label=title, command=callback)
        if management.index('end') is not None:
            ttk.Menubutton(tools, text='분석 관리 ▾', menu=management).pack(side='right', padx=7)
        self.coverage = tk.Label(header, bg=BG, fg=MUTED, font=('맑은 고딕', 9),
                                 anchor='w', justify='left', wraplength=1100)
        self.coverage.pack(fill='x', pady=(8, 0))

        toolbar = tk.Frame(self.win, bg=BG, padx=20, pady=5)
        toolbar.pack(fill='x')
        self.all_button = _button(toolbar, '찾은 사진', lambda: self.show_saved(False))
        self.all_button.pack(side='left')
        self.saved_button = _button(toolbar, '♡ 찜한 사진 0', lambda: self.show_saved(True))
        self.saved_button.pack(side='left', padx=(6, 10))
        self.sort = tk.StringVar(value='관련도 순')
        sort = ttk.Combobox(toolbar, state='readonly', textvariable=self.sort,
                           values=('관련도 순', '최근 수정 순', '이름 순'), width=11)
        sort.pack(side='right', padx=(7, 0))
        sort.bind('<<ComboboxSelected>>', lambda event: self.refresh())
        size = ttk.Combobox(toolbar, state='readonly', textvariable=self.card_size,
                           values=('편하게', '크게'), width=6)
        size.pack(side='right', padx=5)
        size.bind('<<ComboboxSelected>>', lambda event: self.resize_cards())
        self.size_label = tk.Label(toolbar, text='사진 크기', bg=BG, fg=MUTED)
        self.size_label.pack(side='right')

        self.pane = tk.PanedWindow(self.win, orient='horizontal', sashwidth=12, bd=0, bg=BG)
        self.pane.pack(fill='both', expand=True, padx=16, pady=(6, 12))
        self.grid_panel = tk.Frame(self.pane, bg=BG)
        self.preview_panel = tk.Frame(self.pane, bg='white', highlightthickness=1, highlightbackground=BORDER)
        self.pane.add(self.grid_panel, minsize=220, stretch='always')
        self.pane.add(self.preview_panel, minsize=260, stretch='never', width=310)
        nav = tk.Frame(self.grid_panel, bg=BG, pady=7)
        nav.pack(side='bottom', fill='x')
        self.previous = _button(nav, '← 이전', lambda: self.paginate(-1))
        self.previous.pack(side='left')
        self.next = _button(nav, '다음 →', lambda: self.paginate(1))
        self.next.pack(side='right')
        self.count = tk.Label(nav, bg=BG, fg=MUTED, anchor='center', font=('맑은 고딕', 10))
        self.count.pack(side='left', fill='x', expand=True)
        self.mobile_actions = tk.Frame(self.grid_panel, bg=BG, pady=3)
        self.mobile_buttons = []
        for text, command in [('선택 사진 크게 보기', self.open_viewer), ('저장된 폴더 열기', self.reveal)]:
            control = _button(self.mobile_actions, text, command, primary=text.startswith('선택'))
            control.pack(side='left', fill='x', expand=True, padx=3)
            self.mobile_buttons.append(control)
        self.grid_frame = tk.Frame(self.grid_panel, bg=BG)
        self.grid_frame.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(self.grid_frame, bg=BG, highlightthickness=0, yscrollincrement=26)
        scrollbar = ttk.Scrollbar(self.grid_frame, command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.grid = tk.Frame(self.canvas, bg=BG)
        self.grid_id = self.canvas.create_window(0, 0, window=self.grid, anchor='nw')
        self.grid.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self._resize_grid)
        self.win.bind('<MouseWheel>', self._wheel, add='+')

        self.detail = scroll_page(self.preview_panel, '#FFFFFF')
        self.detail.configure(padx=16, pady=14)
        self.selection_count = tk.Label(self.detail, text='선택한 사진', bg='white', fg=ACCENT,
                                       font=('맑은 고딕', 10, 'bold'), anchor='w')
        self.selection_count.pack(fill='x')
        self.preview_image = tk.Label(self.detail, bg='#F3F0E9', text='사진을 선택해 주세요', height=6, cursor='hand2')
        self.preview_image.pack(fill='x', pady=12)
        self.preview_image.bind('<Button-1>', lambda event: self.open_viewer())
        self.name = tk.Label(self.detail, bg='white', fg=INK, anchor='w', justify='left',
                             wraplength=280, font=('맑은 고딕', 12, 'bold'))
        self.name.pack(fill='x', pady=(0, 5))
        self.date = tk.Label(self.detail, bg='white', fg=MUTED, anchor='w', font=('맑은 고딕', 9))
        self.date.pack(fill='x')
        self.actions = list(self.mobile_buttons)
        self.large_button = _button(self.detail, '크게 보고 넘겨보기  ↗', self.open_viewer, primary=True)
        self.large_button.pack(fill='x', pady=(14, 6))
        self.actions.append(self.large_button)
        action_row = tk.Frame(self.detail, bg='white')
        action_row.pack(fill='x')
        self.save_button = _button(action_row, '♡ 찜하기', lambda: self.toggle_saved(self.selected_path))
        self.save_button.pack(side='left', fill='x', expand=True, padx=(0, 6))
        self.actions.append(self.save_button)
        original = _button(action_row, '원본 열기', self.open)
        original.pack(side='left', fill='x', expand=True)
        self.actions.append(original)
        if callable(getattr(app, 'find_similar_photo', None)):
            similar = _button(self.detail, '이런 사진 더 찾기', self.find_similar)
            similar.pack(fill='x', pady=(6, 0))
            self.actions.append(similar)
        tk.Frame(self.detail, bg=BORDER, height=1).pack(fill='x', pady=16)
        tk.Label(self.detail, text='이 사진을 찾은 단서', bg='white', fg=INK,
                 font=('맑은 고딕', 10, 'bold'), anchor='w').pack(fill='x')
        self.reason = tk.Label(self.detail, bg='white', fg=MUTED, anchor='w', justify='left',
                               wraplength=280, font=('맑은 고딕', 10))
        self.reason.pack(fill='x', pady=(5, 14))
        tk.Label(self.detail, text='저장된 위치', bg='white', fg=INK, anchor='w',
                 font=('맑은 고딕', 10, 'bold')).pack(fill='x')
        self.location = tk.Text(self.detail, height=3, wrap='char', relief='flat', bg='#F6F4EE',
                                fg=MUTED, font=('맑은 고딕', 9), width=18, state='disabled')
        self.location.pack(fill='x', pady=(5, 6))
        reveal = _button(self.detail, '저장된 폴더 열기', self.reveal)
        reveal.pack(fill='x')
        self.actions.append(reveal)
        self.delete_button = _button(self.detail, '사진 삭제 · 휴지통으로', self.delete_photo)
        self.delete_button.configure(fg='#A73B42', activeforeground='#A73B42')
        self.delete_button.pack(fill='x', pady=(12, 0))
        tk.Label(self.detail, text='두 번 클릭 또는 Space로 크게 보기\n방향키로 이동 · Enter로 원본 열기',
                 bg='white', fg=MUTED, justify='left', wraplength=280, font=('맑은 고딕', 9)).pack(fill='x', pady=(16, 0))
        self.detail.bind('<Configure>', self._resize_detail, add='+')
        self.win.bind('<Configure>', self._resize_window, add='+')
        self.update_coverage(getattr(getattr(app, 'photo_library', getattr(app, 'active_library', getattr(app, 'library', None))), 'search_coverage', {}))
        self.refresh()
        self.apply_readability()
        self._poll_id = self.win.after(40, self._drain)

    @staticmethod
    def _commit(ime, callback):
        if ime:
            ime.commit_then(callback)
        else:
            callback()
        return 'break'

    def apply_readability(self):
        if self._closed:
            return
        self.scale = float(getattr(self.app, 'settings', {}).get('text_scale', 1.0))
        self.win._photo_text_scale = self.scale
        apply_fonts(self.win, self.scale)
        self._layout_cards()

    def update_coverage(self, coverage):
        self._coverage_data = coverage
        roots = coverage.get('roots', [])
        scope = '찾는 위치: ' + ' · '.join(Path(root).name or root for root in roots) if roots else '현재 연결한 폴더에서 찾은 사진이에요.'
        count = coverage.get('images', len(self.rows))
        analyzed = coverage.get('visual', sum(_analyzed(row) for row in self.rows))
        message = f'사진 모습 분석 {analyzed}/{count}개'
        if getattr(self.app, 'photo_indexing', False):
            message += ' · 찾은 사진은 지금 볼 수 있어요. 분석은 계속할게요.'
        elif analyzed < count:
            message += ' · 분석 전 사진은 모습 검색에서 빠질 수 있어요.'
        scan = coverage.get('photo_scan') or {}
        if isinstance(scan, dict) and scan.get('phase'):
            phase = {'catalog': '사진이 있는 위치를 확인 중', 'thumbnails': '미리보기 준비 중',
                     'analysis': '사진 모습을 분석 중', 'paused': '분석을 멈췄어요',
                     'complete': '현재 범위 확인 완료', 'error': '사진 분석을 계속하지 못했어요'}.get(scan['phase'], scan['phase'])
            completed, total = scan.get('completed'), scan.get('total')
            if self._filters_shown:
                message += '\n' + str(phase) + (f' · {completed}/{total}개' if total is not None else '')
        unavailable = coverage.get('offline_roots') or coverage.get('unavailable_roots') or []
        if unavailable:
            message += f' · 확인할 수 없는 위치 {len(unavailable)}곳'
        offline = coverage.get('offline', 0)
        if offline:
            message += f' · PC에 내려받지 않은 사진 {offline}개'
        unreadable = coverage.get('unreadable_images', coverage.get('unreadable', 0))
        if unreadable:
            message += f' · 읽을 수 없는 사진 {unreadable}개'
        if self._filters_shown:
            message += '\n' + scope
        self.coverage.configure(text=message)

    def update_results(self, rows, query):
        if self._closed or query != self.search_query:
            return
        self.update_coverage(getattr(getattr(self.app, 'photo_library', getattr(self.app, 'active_library', getattr(self.app, 'library', None))), 'search_coverage', {}))
        photos = [row for row in _photo_rows(rows) if row['path'] not in self._removed_paths]
        if self.rows == photos:
            return
        self.rows = photos
        for row in photos:
            if row['path'] in self.saved:
                self.saved[row['path']] = row
        self.refresh(preserve_view=True)

    def reset(self):
        self.query.set('')
        self.mode.set('모든 사진')
        self.sort.set('관련도 순')
        self.refresh()

    def toggle_filters(self):
        if self.win.winfo_height() < 640:
            return self._open_filter_dialog()
        self._set_filters(not self._filters_shown)

    def _open_filter_dialog(self):
        if self.filter_dialog and self.filter_dialog.winfo_exists():
            self.filter_dialog.lift()
            return
        dialog = self.filter_dialog = tk.Toplevel(self.win)
        dialog.title('사진 상세 조건')
        dialog.configure(bg=BG)
        fit_window(dialog, 620, 460, 440, 320)
        body = scroll_page(dialog, BG)
        body.configure(padx=18, pady=14)
        tk.Label(body, text='사진을 조금 더 좁혀볼까요?', bg=BG, fg=INK,
                 font=('맑은 고딕', 16, 'bold')).pack(anchor='w', pady=(0, 12))
        tk.Label(body, text='이름·폴더·설명에 포함된 단어', bg=BG, fg=MUTED).pack(anchor='w')
        entry = ttk.Entry(body, textvariable=self.query, font=('맑은 고딕', 12))
        entry.pack(fill='x', ipady=6, pady=(5, 12))
        from ime_entry import attach
        ime = attach(entry)
        mode = ttk.Combobox(body, state='readonly', textvariable=self.mode,
                           values=('모든 사진', '모습 분석 완료', '모습 분석 대기'))
        mode.pack(fill='x', pady=(0, 10))
        def apply():
            self.refresh()
            dialog.destroy()
        entry.bind('<Return>', lambda event: self._commit(ime, apply))
        _button(body, '조건 적용', lambda: self._commit(ime, apply), primary=True).pack(fill='x')
        _button(body, '조건 초기화', self.reset).pack(fill='x', pady=7)
        roots = getattr(self, '_coverage_data', {}).get('roots', [])
        tk.Label(body, text='찾는 위치\n' + '\n'.join(roots), bg=BG, fg=MUTED,
                 justify='left', wraplength=480).pack(fill='x', pady=12)
        for label, method in [('다른 위치도 찾기', 'choose_photo_folder'), ('분석 멈추기', 'cancel_photo_search'),
                              ('분석 계속하기', 'resume_photo_analysis'), ('사진 찾기 준비', 'setup_photo_ai')]:
            callback = getattr(self.app, method, None)
            if callable(callback):
                _button(body, label, callback).pack(fill='x', pady=3)
        _button(body, '닫기', dialog.destroy).pack(fill='x', pady=8)
        dialog.bind('<Escape>', lambda event: (dialog.destroy(), 'break')[-1])
        apply_fonts(dialog, getattr(self, 'scale', 1.0))

    def _set_filters(self, visible):
        self._filters_shown = visible
        if visible:
            self.filter_section.pack(fill='x', before=self.coverage, pady=(8, 0))
        else:
            self.filter_section.pack_forget()
        self.filter_toggle.configure(text='상세 조건 ▴' if visible else '상세 조건 ▾')
        self.update_coverage(getattr(self, '_coverage_data', {}))

    def refresh(self, preserve_view=False):
        if self._closed:
            return
        terms = self.query.get().casefold().split()
        rows = list(self.saved.values()) if self.saved_only.get() else list(self.rows)
        if terms:
            rows = [row for row in rows if all(term in ' '.join(str(row.get(key, '')) for key in
                    ('name', 'path', 'reason', 'body', 'tags', 'note')).casefold() for term in terms)]
        if self.mode.get() != '모든 사진':
            desired = self.mode.get() == '모습 분석 완료'
            rows = [row for row in rows if _analyzed(row) == desired]
        if self.sort.get() == '최근 수정 순':
            rows.sort(key=lambda row: row.get('mtime') or 0, reverse=True)
        elif self.sort.get() == '이름 순':
            rows.sort(key=lambda row: row.get('name', '').casefold())
        self.visible = rows
        index = next((i for i, row in enumerate(rows) if row['path'] == self.selected_path), None)
        if index is None or not preserve_view:
            self.selected_path = rows[0]['path'] if rows else None
        self.page = index // PAGE_SIZE if preserve_view and index is not None else 0
        self._render_page(preserve_scroll=preserve_view)
        self._show_selected()

    def show_saved(self, value=None):
        self.saved_only.set(not self.saved_only.get() if value is None else value)
        self.query.set('')
        self.mode.set('모든 사진')
        self.refresh()

    def toggle_saved(self, path):
        if not path:
            return
        if path in self.saved:
            del self.saved[path]
        else:
            row = next((row for row in self.rows if row['path'] == path), None)
            if row:
                self.saved[path] = dict(row)
        if self.saved_only.get():
            self.refresh(preserve_view=True)
        else:
            self._mark_selection()
            self._update_tabs()

    def _update_tabs(self):
        saved = self.saved_only.get()
        self.all_button.configure(bg='#EDE8FA' if not saved else 'white', fg=ACCENT if not saved else INK)
        self.saved_button.configure(text=f'♥ 찜한 사진 {len(self.saved)}' if self.saved else '♡ 찜한 사진 0',
                                    bg='#EDE8FA' if saved else 'white', fg=ACCENT if saved else INK)
        self.result_total.configure(text=f'{len(self.visible):,}장')
        self.save_button.configure(text='♥ 찜했어요' if self.selected_path in self.saved else '♡ 찜하기')
        self.win.title('짱구의 사진 상자 · ' + ('찜한 사진 (이 창을 닫기 전까지 보관)' if saved else self.search_query or '찾은 사진'))

    def quick_refine(self, text):
        self.refine_query.set(text)
        self.refine()

    def resize_cards(self):
        self._render_page(preserve_scroll=True)
        self._show_selected()

    def _card_image_size(self):
        return (310, 222) if self.card_size.get() == '크게' else (246, 180)

    def _render_page(self, preserve_scroll=False):
        self._generation += 1
        for future in tuple(self._jobs):
            future.cancel()
        old_scroll = self.canvas.yview()[0]
        for card in self.cards:
            card.destroy()
        self.cards = []
        self.photos = {}
        for child in self.grid.winfo_children():
            child.destroy()
        start = self.page * PAGE_SIZE
        rows = self.visible[start:start + PAGE_SIZE]
        for index, row in enumerate(rows):
            card = tk.Frame(self.grid, bg='white', bd=0, relief='flat', highlightthickness=2,
                            highlightbackground=BORDER, takefocus=True, cursor='hand2')
            card.row = row
            image = tk.Label(card, text='사진을 불러오는 중…',
                             bg='#F1EEE8', width=24, height=9, anchor='center')
            image.pack(fill='x', padx=6, pady=(6, 0))
            card.image_label = image
            badge, color, ink = _badge(row)
            top = tk.Frame(card, bg='white')
            top.pack(fill='x', padx=10, pady=(9, 3))
            card.save_control = tk.Button(top, text='♡', command=lambda path=row['path']: self.toggle_saved(path),
                                          bg='white', fg=ACCENT, activebackground='#EDE8FA', activeforeground=ACCENT,
                                          font=('맑은 고딕', 13), relief='flat', bd=0, cursor='hand2', padx=5)
            card.save_control.pack(side='right')
            card.delete_control = tk.Button(top, text='삭제', command=lambda path=row['path']: self.delete_photo(path),
                                           bg='white', fg='#A73B42', activebackground='#FBE8E7',
                                           font=('맑은 고딕', 9), relief='flat', bd=0, padx=4, cursor='hand2')
            card.delete_control.pack(side='right', padx=(0, 4))
            reason = tk.Label(top, text=badge, bg=color, fg=ink, padx=6, pady=3,
                              anchor='w', font=('맑은 고딕', 9))
            reason.pack(side='left')
            card.reason_label = reason
            title = tk.Label(card, text=_short(row.get('name', Path(row['path']).name)), bg='white', fg=INK,
                             justify='left', anchor='w', font=('맑은 고딕', 10, 'bold'))
            title.pack(fill='x', padx=11, pady=(2, 0))
            card.title_label = title
            source = tk.Label(card, text=_date(row), bg='white', fg=MUTED,
                              anchor='w', font=('맑은 고딕', 9))
            source.pack(fill='x', padx=11, pady=(3, 10))
            card.source_label = source
            for widget in (card, image, reason, title, source, top):
                widget.bind('<Button-1>', lambda event, path=row['path'], control=card: self.select(path, control))
                widget.bind('<Double-1>', lambda event, path=row['path']: self._open_path(path))
            card.bind('<FocusIn>', lambda event, path=row['path']: self.select(path, ensure=True))
            card.bind('<Return>', lambda event: self.open())
            card.bind('<space>', lambda event: self.open_viewer())
            card.bind('<Delete>', lambda event: self.delete_photo())
            for key, delta in [('Left', -1), ('Right', 1), ('Up', 'up'), ('Down', 'down')]:
                card.bind(f'<{key}>', lambda event, amount=delta: self.move_selection(amount))
            self.cards.append(card)
            self._load(row, self._card_image_size(), ('card', self._generation, row['path']))
        if not rows:
            empty = tk.Frame(self.grid, bg=BG, padx=24, pady=30)
            empty.grid(row=0, column=0, sticky='nsew')
            title = '마음에 드는 사진을 모아보세요' if self.saved_only.get() and not self.saved else '이 조건의 사진은 아직 없어요'
            message = ('사진의 ♡를 누르면 이곳에서 모아 볼 수 있어요.\n찜한 사진은 이 창을 닫기 전까지 보관해요.'
                       if self.saved_only.get() and not self.saved else
                       '조건을 초기화하거나 기억나는 모습을 다르게 말해 보세요.\n분석 중인 사진은 결과에 추가될 수 있어요.')
            tk.Label(empty, text='♡' if self.saved_only.get() else '⌕', bg=BG, fg=ACCENT,
                     font=('맑은 고딕', 30)).pack(anchor='w')
            tk.Label(empty, text=title, bg=BG, fg=INK, font=('맑은 고딕', 14, 'bold')).pack(anchor='w', pady=8)
            tk.Label(empty, text=message, bg=BG, fg=MUTED, justify='left', wraplength=380).pack(anchor='w')
            _button(empty, '찾은 사진으로 돌아가기' if self.saved_only.get() else '조건 초기화',
                    lambda: self.show_saved(False) if self.saved_only.get() else self.reset()).pack(anchor='w', pady=16)
        total = len(self.visible)
        self.count.configure(text=f'{start + 1}–{min(start + PAGE_SIZE, total)} / {total}장' if total else '사진 0장')
        self.previous.configure(state='normal' if self.page else 'disabled')
        self.next.configure(state='normal' if start + PAGE_SIZE < total else 'disabled')
        self._layout_cards()
        apply_fonts(self.grid, getattr(self, 'scale', 1.0))
        self._mark_selection()
        self.canvas.yview_moveto(old_scroll if preserve_scroll else 0)

    def _layout_cards(self):
        if self._closed:
            return
        width = max(200, self.canvas.winfo_width())
        minimum = round((338 if self.card_size.get() == '크게' else 274) * min(1.25, getattr(self, 'scale', 1.0)))
        columns = max(1, min(5, width // minimum))
        for column in range(5):
            self.grid.columnconfigure(column, weight=1 if column < columns else 0)
        self.columns = columns
        card_width = max(150, width // columns - 22)
        for index, card in enumerate(self.cards):
            card.grid(row=index // columns, column=index % columns, sticky='nsew', padx=5, pady=6)
            for label in (card.reason_label, card.title_label, card.source_label):
                label.configure(wraplength=card_width)
            card.title_label.configure(text=_short(card.row.get('name', Path(card.row['path']).name),
                                                    max(14, int(card_width / (10 * getattr(self, 'scale', 1.0))))))

    def _resize_grid(self, event):
        self.canvas.itemconfigure(self.grid_id, width=event.width)
        if self._layout_id:
            self.win.after_cancel(self._layout_id)
        self._layout_id = self.win.after(60, self._finish_layout)

    def _finish_layout(self):
        self._layout_id = None
        self._layout_cards()

    def _resize_detail(self, event):
        width = max(140, event.width - 28)
        for label in (self.reason, self.name):
            label.configure(wraplength=width)
        size = (min(640, width), 320)
        if abs(size[0] - self._preview_size[0]) >= 20:
            self._preview_size = size
            if self._preview_layout_id:
                self.win.after_cancel(self._preview_layout_id)
            self._preview_layout_id = self.win.after(100, self._resize_preview)

    def _resize_preview(self):
        self._preview_layout_id = None
        row = self.selected()
        if row:
            self._load(row, self._preview_size, ('preview', self._generation, row['path']))

    def _resize_window(self, event):
        if event.widget is not self.win:
            return
        self.request.configure(wraplength=max(200, event.width - 32))
        self.coverage.configure(wraplength=max(200, event.width - 32))
        short = event.height < 640
        compact = short or event.width < 1000
        layout = (short, compact)
        if layout != self._short_layout:
            self._short_layout = layout
            if short:
                self._set_filters(False)
                self.coverage.pack_forget()
                self.request.pack_forget()
                self.heading.configure(font=('맑은 고딕', round(14 * getattr(self, 'scale', 1)), 'bold'))
                self.header.configure(pady=2)
                self.refine_box.configure(pady=4)
            else:
                if self.refine_box.winfo_manager():
                    self.request.pack(fill='x', before=self.refine_box, pady=(3, 10))
                else:
                    self.request.pack(fill='x', pady=(3, 10))
                self.heading.configure(font=('맑은 고딕', round(20 * getattr(self, 'scale', 1)), 'bold'))
                self.coverage.pack(fill='x', pady=(8, 0))
                self.header.configure(pady=12)
                self.refine_box.configure(pady=10)
            if hasattr(self, 'quick_row'):
                if short:
                    self.quick_row.grid_remove()
                else:
                    self.quick_row.grid()
            if compact:
                self.pane.forget(self.preview_panel)
                self.mobile_actions.pack(side='bottom', fill='x', before=self.grid_frame)
            else:
                self.mobile_actions.pack_forget()
                if str(self.preview_panel) not in [str(pane) for pane in self.pane.panes()]:
                    self.pane.add(self.preview_panel, minsize=260, stretch='never', width=310)
        if event.width < 730:
            self.size_label.pack_forget()
        elif not self.size_label.winfo_manager():
            self.size_label.pack(side='right')

    def _wheel(self, event):
        name = str(event.widget)
        if name == str(self.canvas) or name.startswith(str(self.grid) + '.') or name == str(self.grid):
            if self.canvas.yview() != (0.0, 1.0):
                self.canvas.yview_scroll(-int(event.delta / 120), 'units')
                return 'break'

    def _load(self, row, size, target):
        sources = tuple(dict.fromkeys(str(value) for value in (row.get('thumbnail'), row['path']) if value))
        # Existence/stat calls also run in the worker: a disconnected share must
        # not freeze the Tk input loop before thumbnail work can be scheduled.
        key = (sources, row.get('mtime', 0), size)
        if row.get('status') == 'PC에 내려받지 않은 사진':
            self._queue.put((target, key, None))
            return
        if key in self._images:
            self._images.move_to_end(key)
            self._queue.put((target, key, self._images[key]))
            return
        future = self._pool.submit(_decode_sources, sources, size)
        self._jobs.add(future)
        def done(job):
            try:
                picture = None if job.cancelled() else job.result()
            except Exception:
                picture = None
            self._queue.put((target, key, picture))
        future.add_done_callback(done)

    def _drain(self):
        self._poll_id = None
        if self._closed:
            return
        self._jobs = {job for job in self._jobs if not job.done()}
        for _ in range(40):
            try:
                target, key, picture = self._queue.get_nowait()
            except queue.Empty:
                break
            if picture is not None:
                self._images[key] = picture
                self._images.move_to_end(key)
                while len(self._images) > 72:
                    self._images.popitem(last=False)
            kind, generation, path = target
            if generation != self._generation:
                continue
            if kind == 'card':
                card = next((item for item in self.cards if item.row['path'] == path), None)
                if card is None or key[2] != self._card_image_size():
                    continue
                if picture is None:
                    card.image_label.configure(text='미리보기를 만들 수 없어요\n원본 열기로 확인해 주세요', height=8)
                else:
                    picture = ImageOps.pad(picture, self._card_image_size(), color='#F1EEE8', method=Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(picture, master=self.win)
                    self.photos[path] = photo
                    card.image_label.configure(image=photo, text='', width=0, height=self._card_image_size()[1])
            elif path == self.selected_path and key[2] == self._preview_size:
                if picture is None:
                    self.preview_image.configure(image='', text='미리보기를 만들 수 없어요\n아래에서 원본을 열어 주세요', height=6)
                else:
                    picture = picture.copy()
                    draw = ImageDraw.Draw(picture)
                    for detected in (self.selected() or {}).get('detected_objects', []):
                        box = detected.get('box', {})
                        try:
                            values = [max(0, min(1, float(box[key]))) for key in ('xmin', 'ymin', 'xmax', 'ymax')]
                            draw.rectangle((values[0]*picture.width, values[1]*picture.height,
                                            values[2]*picture.width, values[3]*picture.height), outline='#17764B', width=3)
                        except (KeyError, TypeError, ValueError):
                            pass
                    self.preview_photo = ImageTk.PhotoImage(picture, master=self.win)
                    self.preview_image.configure(image=self.preview_photo, text='', height=0)
        self._poll_id = self.win.after(50, self._drain)

    def selected(self):
        return next((row for row in self.visible if row['path'] == self.selected_path), None)

    def select(self, path, control=None, ensure=False):
        self.selected_path = path
        self._mark_selection()
        self._show_selected()
        card = next((item for item in self.cards if item.row['path'] == path), None)
        if control is not None:
            control.focus_set()
        if ensure and card is not None:
            self.grid.update_idletasks()
            y = card.winfo_y()
            height = max(1, self.grid.winfo_height())
            top = self.canvas.canvasy(0)
            if y < top:
                self.canvas.yview_moveto(max(0, y - 6) / height)
            elif y + card.winfo_height() > top + self.canvas.winfo_height():
                self.canvas.yview_moveto((y + card.winfo_height() - self.canvas.winfo_height() + 6) / height)
        return 'break'

    def _mark_selection(self):
        for card in self.cards:
            selected = card.row['path'] == self.selected_path
            card.configure(highlightbackground=ACCENT if selected else BORDER,
                           highlightcolor=ACCENT)
            card.save_control.configure(text='♥' if card.row['path'] in self.saved else '♡')
            card.delete_control.configure(state='normal' if self._can_delete(card.row) else 'disabled',
                                          text='처리 중' if card.row['path'] in self._deleting_paths else '삭제')
        row = self.selected()
        pending = bool(row and row['path'] in self._deleting_paths)
        self.delete_button.configure(state='normal' if self._can_delete(row) else 'disabled',
                                     text='휴지통으로 보내는 중…' if pending else '사진 삭제 · 휴지통으로')
        if self.viewer:
            self.viewer.set_delete_pending(pending)
        self._update_tabs()

    def _show_selected(self):
        row = self.selected()
        for button in self.actions:
            button.configure(state='normal' if row else 'disabled')
        self.preview_photo = None
        self.preview_image.configure(image='', text='사진을 불러오는 중…' if row else '사진을 선택해 주세요', height=6)
        self.reason.configure(text=_reason(row) if row else '')
        self.name.configure(text=row.get('name', Path(row['path']).name) if row else '')
        self.date.configure(text=_date(row) if row else '')
        index = next((i for i, item in enumerate(self.visible) if item['path'] == self.selected_path), 0)
        self.selection_count.configure(text=f'{index + 1} / {len(self.visible):,} · 선택한 사진' if row else '선택한 사진')
        self.location.configure(state='normal')
        self.location.delete('1.0', 'end')
        if row:
            self.location.insert('1.0', row['path'])
            self._load(row, self._preview_size, ('preview', self._generation, row['path']))
        self.location.configure(state='disabled')
        self._update_tabs()
        if self.viewer:
            if row:
                self.viewer.show(row, index, len(self.visible))
                self.viewer.set_delete_pending(row['path'] in self._deleting_paths)
            else:
                self.close_viewer()

    def move_selection(self, amount):
        if not self.visible:
            return 'break'
        if amount == 'up':
            amount = -self.columns
        elif amount == 'down':
            amount = self.columns
        current = next((i for i, row in enumerate(self.visible) if row['path'] == self.selected_path), 0)
        index = max(0, min(len(self.visible) - 1, current + amount))
        self.selected_path = self.visible[index]['path']
        if index // PAGE_SIZE != self.page:
            self.page = index // PAGE_SIZE
            self._render_page()
        card = next((item for item in self.cards if item.row['path'] == self.selected_path), None)
        self.select(self.selected_path, card, ensure=True)
        return 'break'

    def paginate(self, delta):
        maximum = max(0, (len(self.visible) - 1) // PAGE_SIZE)
        page = max(0, min(maximum, self.page + delta))
        if page == self.page:
            return
        self.page = page
        self.selected_path = self.visible[page * PAGE_SIZE]['path']
        self._render_page()
        self._show_selected()
        if self.cards:
            self.cards[0].focus_set()

    def _open_path(self, path):
        self.select(path)
        return self.open_viewer()

    def open_viewer(self):
        row = self.selected()
        if not row:
            return 'break'
        if self.viewer is None:
            from photo_viewer import PhotoViewer
            self.viewer = PhotoViewer(self.win, self.move_selection, self.open, self.reveal, self._viewer_closed,
                                      on_delete=self.delete_photo)
        index = next((i for i, item in enumerate(self.visible) if item['path'] == row['path']), 0)
        self.viewer.show(row, index, len(self.visible))
        self.viewer.set_delete_pending(row['path'] in self._deleting_paths)
        self.viewer.win.lift()
        self.viewer.win.focus_set()
        return 'break'

    def _viewer_closed(self):
        self.viewer = None
        if not self._closed:
            card = next((item for item in self.cards if item.row['path'] == self.selected_path), None)
            if card is not None:
                card.focus_set()

    def close_viewer(self):
        viewer = self.viewer
        self.viewer = None
        if viewer is not None:
            viewer.close()
        return 'break'

    def open(self, preview=False):
        row = self.selected()
        if row:
            if preview:
                self.app.preview(row)
            else:
                self.app.open_file(row['path'])
        return 'break'

    def reveal(self):
        row = self.selected()
        if row:
            self.app.reveal(row['path'])

    def _can_delete(self, row):
        return bool(row and callable(getattr(self.app, 'recycle_photo', None))
                    and row['path'] not in self._deleting_paths
                    and row.get('status') != 'PC에 내려받지 않은 사진')

    def delete_photo(self, path=None):
        path = path or self.selected_path
        row = next((item for item in list(self.rows) + list(self.saved.values()) if item['path'] == path), None)
        if not self._can_delete(row):
            return 'break'
        # Capture the exact row before opening a modal: an analysis update can
        # reorder the gallery while the confirmation is on screen.
        row = dict(row)
        parent = self.viewer.win if self.viewer else self.win
        if not messagebox.askyesno('사진을 휴지통으로 보낼까요?',
            f"{row.get('name') or Path(path).name}\n\n{path}\n\n원본 사진을 휴지통으로 보냅니다.\n잘못 삭제한 사진은 Windows 휴지통에서 복원할 수 있어요.",
            parent=parent, icon='warning', default='no'):
            return 'break'
        self._deleting_paths.add(path)
        self._mark_selection()
        def finished(result):
            self._deleting_paths.discard(path)
            if self._closed:
                return
            if result.get('ok'):
                self.remove_photo(path)
                self.count.configure(text=f'휴지통으로 보냈어요 · {len(self.visible):,}장')
            else:
                self._mark_selection()
                if not result.get('cancelled'):
                    parent = self.viewer.win if self.viewer else self.win
                    messagebox.showerror('사진을 삭제하지 못했어요',
                                         result.get('error') or '잠시 뒤 다시 시도해 주세요.', parent=parent)
        try:
            self.app.recycle_photo(row, finished)
        except Exception as error:
            finished(dict(ok=False, cancelled=False, error=str(error)))
        return 'break'

    def remove_photo(self, path):
        if self._closed:
            return
        if path in self._removed_paths and not any(row['path']==path for row in self.rows) and path not in self.saved:
            return
        old_index = next((index for index, row in enumerate(self.visible) if row['path'] == self.selected_path), 0)
        remaining = [row for row in self.visible if row['path'] != path]
        if self.selected_path == path:
            self.selected_path = remaining[min(old_index, len(remaining) - 1)]['path'] if remaining else None
        self._removed_paths.add(path)
        self.rows = [row for row in self.rows if row['path'] != path]
        self.saved.pop(path, None)
        # Drop in-memory pixels too; late decoders are ignored by generation.
        self._images.clear()
        self.refresh(preserve_view=True)

    def refine(self):
        text = self.refine_query.get().strip()
        if text:
            if getattr(self.app, 'busy', False):
                self.set_searching(True)
                return
            self.saved_only.set(False)
            self.query.set('')
            self.mode.set('모든 사진')
            self.app.refine_photo(text)

    def set_searching(self, searching):
        if not self._closed and hasattr(self, 'refine_submit'):
            self.refine_submit.configure(text='찾는 중…' if searching else '이 결과에서 더 찾기',
                                         state='disabled' if searching else 'normal')

    def find_similar(self):
        row = self.selected()
        if row:
            self.app.find_similar_photo(row)

    def _destroyed(self, event):
        if event.widget is self.win:
            self._dispose()

    def _dispose(self):
        if self._closed:
            return
        self._closed = True
        self.close_viewer()
        if self.filter_dialog and self.filter_dialog.winfo_exists():
            self.filter_dialog.destroy()
        for name in ('_poll_id', '_layout_id', '_preview_layout_id'):
            identifier = getattr(self, name)
            if identifier:
                try:
                    self.win.after_cancel(identifier)
                except tk.TclError:
                    pass
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.app.result_browsers.discard(self)
        self._images.clear()

    def close(self):
        self._dispose()
        if self.win.winfo_exists():
            self.win.destroy()
