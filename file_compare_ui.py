"""Two local originals side by side; background readers never retain Tk objects."""
from datetime import datetime
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageTk
from document_preview import DocumentPreview, render_document_preview
from document_diff import DocumentDiff, compare_documents
from final_versions import FinalVersions

BG = '#FFFDF9'
INK = '#292A28'
MUTED = '#65675F'
LINE = '#E3E4DC'
FONT = '맑은 고딕'
TEXT_LIMIT = 30_000
READ_LIMIT = 120_000
MEDIA = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff', '.ico'}


def _snapshot(row):
    """Send only immutable file metadata to readers, never app/widget references."""
    if not isinstance(row, dict) or not isinstance(row.get('path'), (str, os.PathLike)):
        return None
    path = str(row['path'])
    if not path.strip():
        return None
    return dict(path=path, name=str(row.get('name') or Path(path).name),
                body=str(row.get('body') or '')[:TEXT_LIMIT],
                mtime=row.get('mtime') if isinstance(row.get('mtime'), (int, float)) else None,
                size=row.get('size') if isinstance(row.get('size'), int) else None)


def comparison_candidates(selected, rows):
    seen = {os.path.normcase(os.path.abspath(selected['path']))}
    result = []
    for row in rows or ():
        row = _snapshot(row)
        if row is None:
            continue
        key = os.path.normcase(os.path.abspath(row['path']))
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _short(text, limit=52):
    return text if len(text) <= limit else text[:limit // 2] + '…' + text[-limit // 2:]


def _modified(value):
    try:
        return '수정 날짜: ' + datetime.fromtimestamp(value).strftime('%Y.%m.%d %H:%M')
    except (OSError, OverflowError, TypeError, ValueError):
        return '수정 날짜를 확인할 수 없어요.'


def _read_text(path):
    """A bounded preview of a chosen .txt file, including common Korean encodings."""
    with path.open('rb') as stream:
        data = stream.read(READ_LIMIT + 1)
    truncated = len(data) > READ_LIMIT
    data = data[:READ_LIMIT]
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = data.decode('utf-16', errors='replace')
    elif b'\x00' in data:
        return '글자로 표시할 수 없는 파일이에요. 원본을 열어 확인해 주세요.'
    else:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = data.decode('cp949', errors='replace')
    truncated = truncated or len(text) > TEXT_LIMIT
    return text[:TEXT_LIMIT] + ('\n\n앞부분만 표시했어요. 전체 내용은 원본을 열어 주세요.' if truncated else '')


def load_preview(row, page=0):
    """Read-only, non-Tk result suitable for a worker response queue."""
    path = Path(row['path'])
    try:
        stat = path.stat()
        if not path.is_file():
            raise FileNotFoundError()
        current = (row.get('mtime') in (None, stat.st_mtime) and
                   row.get('size') in (None, stat.st_size))
        body = (row.get('body') or '')[:TEXT_LIMIT] if current else ''
        preview = (render_document_preview(row, page, (1100, 1500)) if path.suffix.lower() in MEDIA
                   else DocumentPreview())
        if preview.image is not None:
            return dict(preview=preview, text='', mtime=stat.st_mtime)
        if path.suffix.lower() == '.txt':
            body = _read_text(path)
        text = ('문서에서 읽은 내용\n\n' + body if body else
                '이 파일은 원본을 열어 확인해 주세요.')
        if preview.error:
            text = preview.error + '\n\n' + text
        elif not current and not body:
            text = '파일이 바뀌어 저장된 미리보기를 사용할 수 없어요.\n원본을 열어 확인해 주세요.'
        return dict(preview=preview, text=text, mtime=stat.st_mtime)
    except (OSError, ValueError):
        return dict(preview=DocumentPreview(), text='파일이 이동되었거나 읽을 수 없어요.', mtime=None)


def compare_message(left, right, cancelled):
    from file_comparison import group_identical_files
    result = group_identical_files([left, right], cancelled=cancelled)
    if result.cancelled:
        return ''
    if result.unverified:
        return '내용을 확인하지 못한 파일이 있어요. 원본을 열어 확인해 주세요.'
    if any(len(group) > 1 for group in result.groups):
        return '이름이 달라도 두 파일의 저장된 내용은 같아요.'
    return '두 파일이 완전히 같지는 않아요. 화면에서 내용을 비교해 보세요.'


def _reader(requests, responses, stopped):
    # This function and its arguments own no Tk values, even after window close.
    while not stopped.is_set():
        request = requests.get()
        if request is None:
            return
        kind, token, payload, cancelled = request
        if cancelled.is_set() or stopped.is_set():
            continue
        try:
            if kind == 'preview':
                value = load_preview(*payload)
            elif kind == 'diff':
                value = compare_documents(*payload, cancelled.is_set)
            elif kind == 'final_states':
                directory, paths = payload
                memory = FinalVersions(directory)
                value = [memory.describe(path) if path else None for path in paths]
            elif kind == 'final_action':
                directory, action, path, candidates = payload
                memory = FinalVersions(directory)
                value = memory.mark(path, candidates) if action == 'mark' else memory.clear(path)
                value = dict(ok=True)
            else:
                value = compare_message(*payload, cancelled.is_set)
        except Exception:
            if kind == 'preview':
                value = dict(preview=DocumentPreview(), text='미리보기를 불러오지 못했어요. 원본을 열어 주세요.', mtime=None)
            elif kind == 'diff':
                value = DocumentDiff(summary='글자를 읽지 못해 변경 내용을 판단할 수 없어요.')
            elif kind.startswith('final_'):
                value = dict(ok=False, error='최종본 표시를 저장하거나 읽지 못했어요. 잠시 뒤 다시 눌러 주세요.')
            else:
                value = '내용을 확인하지 못했어요. 원본을 열어 확인해 주세요.'
        if not cancelled.is_set() and not stopped.is_set():
            responses.put((kind, token, value))


class FileComparisonWindow:
    def __init__(self, app, selected_row, candidate_rows):
        self.app = app
        self.selected = _snapshot(selected_row)
        if self.selected is None:
            raise ValueError('비교할 파일을 먼저 선택해 주세요.')
        self.candidates = comparison_candidates(self.selected, candidate_rows)
        self.other = self.candidates[0] if self.candidates else None
        self.closed = False
        self.generation = 0
        self.panes = []
        self.diff_mode = False
        self.diff_result = None
        self.diff_pending = False
        self._final_busy = False
        memory = getattr(app, 'final_versions', None)
        self.final_directory = str(memory.data) if memory is not None else str(app.data) if hasattr(app, 'data') else None
        self._requests = queue.Queue()
        self._responses = queue.Queue()
        self._stopped = threading.Event()
        self._jobs = []
        self._poll_timer = self._resize_timer = None
        self.win = tk.Toplevel(app.root)
        self.win.comparison = self
        self.win.title('파일 나란히 비교')
        self.win.configure(bg=BG)
        from accessibility import fit_window
        fit_window(self.win, 1100, 760, 680, 500)
        self.win.bind('<Destroy>', self._destroy, add='+')
        self.win.bind('<Escape>', lambda event: self.win.destroy())

        top = tk.Frame(self.win, bg=BG, padx=16, pady=10)
        top.pack(fill='x')
        self.heading = self._label(top, '어느 파일이 필요하세요?', 18, bold=True)
        self.heading.pack(anchor='w')
        choices = tk.Frame(top, bg=BG)
        choices.pack(fill='x', pady=(8, 0))
        self.choose_button = self._button(choices, '다른 파일 고르기', self.choose_file)
        self.choose_button.pack(side='right', padx=(8, 0))
        self.changes_button = self._button(choices, '바뀐 내용 보기', self.toggle_changes)
        self.changes_button.pack(side='right', padx=(8, 0))
        self.selector = ttk.Combobox(choices, state='readonly', font=(FONT, 12), width=10)
        self.selector.pack(side='left', fill='x', expand=True, ipady=4)
        self.selector.bind('<<ComboboxSelected>>', self._select_candidate)
        self._update_choices()
        self.status = self._label(top, '', 11, fg=MUTED)
        self.status.pack(fill='x', pady=(7, 0))
        self.status.bind('<Configure>', lambda event: self.status.configure(wraplength=max(150, event.width)))

        foot = self._label(self.win, '수정 날짜가 늦어도 최종본이 아닐 수 있어요.', 10, fg=MUTED)
        foot.pack(side='bottom', fill='x', padx=16, pady=8)
        body = tk.Frame(self.win, bg=LINE)
        body.pack(fill='both', expand=True, padx=12)
        body.columnconfigure((0, 1), weight=1, uniform='files')
        body.rowconfigure(0, weight=1)
        for side in range(2):
            self.panes.append(self._make_pane(body, side))
        from accessibility import apply_fonts
        apply_fonts(self.win, getattr(app, 'settings', {}).get('text_scale', 1.0))
        # No bound method goes to the worker: it cannot keep the app/Tk alive.
        self.worker = threading.Thread(target=_reader, args=(self._requests, self._responses, self._stopped),
                                       name='file-comparison', daemon=True)
        self.worker.start()
        self._refresh_pair()
        self._poll_timer = self.win.after(50, self._poll)

    @staticmethod
    def _label(parent, text, size=12, bold=False, fg=INK):
        return tk.Label(parent, text=text, bg=BG, fg=fg, anchor='w', justify='left',
                        font=(FONT, size, 'bold' if bold else 'normal'))

    @staticmethod
    def _button(parent, text, command):
        return tk.Button(parent, text=text, command=command, font=(FONT, 12),
                         bg='white', fg=INK, activebackground='#FFF0E8', relief='solid', bd=1,
                         padx=9, pady=7, cursor='hand2')

    def _make_pane(self, parent, side):
        frame = tk.Frame(parent, bg=BG, padx=10, pady=8)
        frame.grid(row=0, column=side, sticky='nsew', padx=(0, 1) if side == 0 else (1, 0))
        title = self._label(frame, '', 13, bold=True)
        title.configure(height=2, wraplength=280)
        title.pack(fill='x')
        modified = self._label(frame, '', 10, fg=MUTED)
        modified.pack(fill='x')
        folder = self._label(frame, '', 10, fg=MUTED)
        folder.pack(fill='x', pady=(2, 6))
        actions = tk.Frame(frame, bg=BG)
        actions.pack(side='bottom', fill='x', pady=(8, 0))
        opened = self._button(actions, '원본 열기', lambda: self.open_file(side))
        opened.pack(fill='x')
        final_label = self._label(actions, '', 10, fg='#286442')
        final_label.configure(wraplength=260)
        final_button = self._button(actions, '이 파일을 최종본으로 표시', lambda: self.toggle_final(side))
        final_button.configure(font=(FONT, 10), wraplength=175)
        if self.final_directory:
            opened.pack_forget()
            actions.columnconfigure(0, weight=1)
            actions.columnconfigure(1, weight=2)
            opened.grid(row=0, column=0, sticky='nsew', padx=(0, 5))
            final_button.grid(row=0, column=1, sticky='nsew')
        navigation = tk.Frame(frame, bg=BG)
        navigation.pack(side='bottom', fill='x', pady=(5, 0))
        previous = self._button(navigation, '이전', lambda: self.change_page(side, -1))
        previous.pack(side='left')
        following = self._button(navigation, '다음', lambda: self.change_page(side, 1))
        following.pack(side='right')
        pages = self._label(navigation, '', 10)
        pages.configure(anchor='center')
        pages.pack(fill='x', expand=True)
        area = tk.Frame(frame, bg='white')
        area.pack(fill='both', expand=True)
        bar = ttk.Scrollbar(area, orient='vertical')
        bar.pack(side='right', fill='y')
        canvas = tk.Canvas(area, bg='#F1F2ED', highlightthickness=0, width=150, height=120,
                           yscrollcommand=bar.set)
        text = tk.Text(area, font=(FONT, 12), wrap='word', bg='white', fg=INK,
                       relief='flat', bd=0, padx=8, pady=8, width=12, height=5, yscrollcommand=bar.set)
        canvas.bind('<Configure>', lambda event, index=side: self._resize(index, event.width))
        canvas.bind('<MouseWheel>', lambda event, widget=canvas: widget.yview_scroll(-int(event.delta / 120), 'units'))
        frame.bind('<Configure>', lambda event: title.configure(wraplength=max(100, event.width - 20)))
        return dict(frame=frame, title=title, modified=modified, folder=folder, open=opened,
                    final_label=final_label, final_button=final_button, final_state=None, preview_text='',
                    previous=previous, next=following, pages=pages, canvas=canvas, text=text,
                    area=area, navigation=navigation, scrollbar=bar,
                    photo=None, result=None, page=0, count=0, serial=0,
                    cancel=None, row=None)

    def _update_choices(self):
        self.selector.configure(values=[row['name'] + ' · ' + str(Path(row['path']).parent)
                                        for row in self.candidates])
        if self.other is not None:
            self.selector.current(self.candidates.index(self.other))
        else:
            self.selector.set('비교할 다른 파일을 골라 주세요')

    def _select_candidate(self, event=None):
        index = self.selector.current()
        if 0 <= index < len(self.candidates):
            self.other = self.candidates[index]
            self._refresh_pair()

    def choose_file(self):
        path = filedialog.askopenfilename(parent=self.win, title='나란히 볼 파일 선택',
                                         filetypes=[('모든 파일', '*.*')])
        if not path or self.closed:
            return
        rows = comparison_candidates(self.selected, self.candidates + [dict(path=path)])
        key = os.path.normcase(os.path.abspath(path))
        candidate = next((row for row in rows if os.path.normcase(os.path.abspath(row['path'])) == key), None)
        if candidate is None:
            self.status.configure(text='왼쪽과 다른 파일을 골라 주세요.')
            return
        self.candidates = rows
        self.other = candidate
        self._update_choices()
        self._refresh_pair()

    def _refresh_pair(self):
        self.generation += 1
        self.diff_result = None
        self.diff_pending = False
        self._final_busy = False
        self.changes_button.configure(state='normal' if self.other else 'disabled')
        for event in self._jobs:
            event.set()
        self._jobs.clear()
        for side, row in enumerate((self.selected, self.other)):
            pane = self.panes[side]
            pane.update(row=row, page=0, count=0, result=None, photo=None)
            pane['title'].configure(text=_short(row['name']) if row else '비교할 파일을 골라 주세요')
            pane['modified'].configure(text=_modified(row.get('mtime')) if row else '')
            pane['folder'].configure(text='폴더: ' + _short(str(Path(row['path']).parent), 48) if row else '')
            pane['open'].configure(state='normal' if row else 'disabled')
            pane['final_button'].configure(state='disabled')
            pane['final_label'].configure(text='')
            pane['final_state'] = None
            self._page_controls(pane)
            self._show_text(pane, '미리보기를 불러오는 중이에요…' if row else
                            '위의 “다른 파일 고르기”를 눌러 주세요.')
            if row:
                self._request_preview(side)
        if self.other:
            self.status.configure(text='두 파일의 내용을 확인하고 있어요…')
            cancelled = threading.Event()
            self._jobs.append(cancelled)
            self._requests.put(('compare', self.generation, (self.selected, self.other), cancelled))
        else:
            self.status.configure(text='비교할 파일을 고르면 두 파일을 나란히 보여 드려요.')
        self._request_final_states()
        if self.diff_mode and self.other:
            self._request_diff()

    def _request_final_states(self):
        if not self.final_directory:
            return
        cancelled = threading.Event()
        self._jobs.append(cancelled)
        self._requests.put(('final_states', self.generation,
                            (self.final_directory, [row['path'] if row else None for row in (self.selected, self.other)]), cancelled))

    def toggle_final(self, side):
        if self.closed or self._final_busy or not self.final_directory:
            return
        pane = self.panes[side]
        if pane['row'] is None or pane['final_state'] is None:
            return
        action = 'clear' if pane['final_state']['state'] != 'none' else 'mark'
        self._final_busy = True
        for item in self.panes:
            item['final_button'].configure(state='disabled')
        self.status.configure(text='최종본 표시를 저장하고 있어요…')
        cancelled = threading.Event()
        self._jobs.append(cancelled)
        self._requests.put(('final_action', self.generation,
            (self.final_directory, action, pane['row']['path'], [self.selected] + self.candidates), cancelled))

    def toggle_changes(self):
        if self.closed or self.other is None:
            return
        self.diff_mode = not self.diff_mode
        self.changes_button.configure(text='나란히 보기' if self.diff_mode else '바뀐 내용 보기')
        if self.diff_mode:
            if self.diff_result is not None:
                self._show_diff()
            else:
                self._request_diff()
        else:
            for pane in self.panes:
                self._display_preview(pane)
            self.status.configure(text='원본을 나란히 보고 있어요. 수정 날짜와 최종본 표시는 별개예요.')

    def _request_diff(self):
        if self.diff_pending or self.other is None:
            return
        self.diff_pending = True
        self.status.configure(text='두 문서에서 바뀐 글자를 찾고 있어요…')
        for pane in self.panes:
            pane['navigation'].pack_forget()
            self._show_text(pane, '현재 원본의 글자를 읽고 있어요…')
        cancelled = threading.Event()
        self._jobs.append(cancelled)
        self._requests.put(('diff', self.generation, (self.selected, self.other), cancelled))

    def _show_diff(self):
        result = self.diff_result
        if result is None:
            return
        self.status.configure(text=result.summary)
        for side, pane in enumerate(self.panes):
            pane['navigation'].pack_forget()
            self._show_text(pane, '')
            text = pane['text']
            text.configure(state='normal')
            text.tag_configure('removed', background='#FCE6E6', foreground='#842C2C')
            text.tag_configure('added', background='#E4F2E7', foreground='#205A35')
            text.tag_configure('note', foreground=MUTED)
            text.insert('end', ('− 왼쪽에서 빠진 내용' if side == 0 else '+ 오른쪽에 추가된 내용') + '\n\n')
            if not result.rows:
                text.insert('end', result.summary + '\n\n')
            for row in result.rows:
                value = row['left' if side == 0 else 'right']
                changed = row['kind'] != 'context' and bool(value)
                tag = ('removed' if side == 0 else 'added') if changed else 'note'
                text.insert('end', (('− ' if side == 0 else '+ ') if changed else '  ') + value + '\n', tag)
            if result.notes:
                text.insert('end', '\n' + '\n'.join(result.notes), 'note')
            text.configure(state='disabled')
            text.yview_moveto(0)

    def _display_preview(self, pane):
        self._page_controls(pane)
        result = pane['result']
        if result is not None and result.image is not None:
            pane['text'].pack_forget()
            pane['canvas'].pack(side='left', fill='both', expand=True)
            pane['scrollbar'].configure(command=pane['canvas'].yview)
            self._draw(pane)
        else:
            self._show_text(pane, pane['preview_text'])

    def _request_preview(self, side):
        pane = self.panes[side]
        if pane['cancel'] is not None:
            pane['cancel'].set()
        pane['serial'] += 1
        cancelled = threading.Event()
        pane['cancel'] = cancelled
        self._jobs.append(cancelled)
        token = (self.generation, side, pane['serial'])
        self._requests.put(('preview', token, (pane['row'], pane['page']), cancelled))

    def _show_text(self, pane, value):
        pane['canvas'].pack_forget()
        pane['text'].pack(side='left', fill='both', expand=True)
        pane['scrollbar'].configure(command=pane['text'].yview)
        pane['text'].configure(state='normal')
        pane['text'].delete('1.0', 'end')
        pane['text'].insert('1.0', value)
        pane['text'].configure(state='disabled')

    def _page_controls(self, pane):
        if pane['count'] > 1 and not self.diff_mode:
            pane['navigation'].pack(side='bottom', fill='x', pady=(5, 0), before=pane['area'])
        else:
            pane['navigation'].pack_forget()
        pane['previous'].configure(state='normal' if pane['page'] > 0 else 'disabled')
        pane['next'].configure(state='normal' if pane['page'] + 1 < pane['count'] else 'disabled')
        pane['pages'].configure(text=f"{pane['page'] + 1} / {pane['count']}쪽" if pane['count'] else '')

    def change_page(self, side, direction):
        pane = self.panes[side]
        page = pane['page'] + direction
        if self.closed or not 0 <= page < pane['count']:
            return
        pane['page'] = page
        self._page_controls(pane)
        self._request_preview(side)

    def open_file(self, side):
        row = self.panes[side]['row']
        if row and not self.closed:
            self.app.open_file(row['path'])

    def _resize(self, side, width):
        if self.closed:
            return
        if self._resize_timer is not None:
            self.win.after_cancel(self._resize_timer)
        self._resize_timer = self.win.after(100, self._draw_all)

    def _draw_all(self):
        self._resize_timer = None
        if not self.closed:
            for pane in self.panes:
                self._draw(pane)

    def _draw(self, pane):
        if self.diff_mode:
            return
        result = pane['result']
        if result is None or result.image is None:
            return
        canvas = pane['canvas']
        width = max(120, canvas.winfo_width() - 16)
        image = result.image.copy()
        image.thumbnail((width, 4000), Image.Resampling.LANCZOS)
        pane['photo'] = ImageTk.PhotoImage(image, master=self.win)
        canvas.delete('all')
        canvas.create_image(max(width / 2 + 8, image.width / 2 + 8), 8, image=pane['photo'], anchor='n')
        canvas.configure(scrollregion=(0, 0, max(width + 16, image.width + 16), image.height + 16))

    def _poll(self):
        self._poll_timer = None
        if self.closed:
            return
        try:
            while True:
                kind, token, value = self._responses.get_nowait()
                if kind == 'diff':
                    if token == self.generation:
                        self.diff_pending = False
                        self.diff_result = value
                        if self.diff_mode:
                            self._show_diff()
                    continue
                if kind in ('final_states', 'final_action'):
                    if token != self.generation:
                        continue
                    if isinstance(value, dict) and value.get('ok') is False:
                        self._final_busy = False
                        self.status.configure(text=value['error'])
                        if kind == 'final_action':
                            for pane in self.panes:
                                state = pane['final_state']
                                marked = bool(state and state['state'] != 'none')
                                pane['final_button'].configure(state='normal' if state and
                                    (marked or str(pane['open'].cget('state')) != 'disabled') else 'disabled')
                        continue
                    if kind == 'final_action':
                        self._final_busy = False
                        self.status.configure(text='최종본 표시를 저장했어요. 원본 파일은 바꾸지 않았어요.')
                        self._request_final_states()
                        callback = getattr(self.app, 'final_versions_changed', None)
                        if callable(callback):
                            callback()
                        continue
                    for pane, state in zip(self.panes, value):
                        pane['final_state'] = state
                        pane['final_label'].configure(text=state['label'] if state else '')
                        if state and state['label']:
                            pane['final_label'].grid(row=1, column=0, columnspan=2, sticky='ew', pady=(5, 0))
                        else:
                            pane['final_label'].grid_remove()
                        marked = bool(state and state['state'] != 'none')
                        pane['final_button'].configure(text='최종본 표시 해제' if marked else '이 파일을 최종본으로 표시',
                            state='normal' if state and (marked or str(pane['open'].cget('state')) != 'disabled') else 'disabled')
                    continue
                if kind == 'compare':
                    if token == self.generation and value and not self.diff_mode:
                        self.status.configure(text=value)
                    continue
                generation, side, serial = token
                pane = self.panes[side]
                if generation != self.generation or serial != pane['serial']:
                    continue
                result = value['preview']
                pane.update(result=result, photo=None, page=result.page, count=result.page_count, preview_text=value['text'])
                pane['modified'].configure(text=_modified(value['mtime']))
                pane['open'].configure(state='normal' if value['mtime'] is not None else 'disabled')
                self._page_controls(pane)
                if not self.diff_mode:
                    self._display_preview(pane)
        except queue.Empty:
            pass
        self._poll_timer = self.win.after(50, self._poll)

    def _destroy(self, event):
        if event.widget is not self.win or self.closed:
            return
        self.closed = True
        self.generation += 1
        self._stopped.set()
        for job in self._jobs:
            job.set()
        self._jobs.clear()
        for timer in (self._poll_timer, self._resize_timer):
            if timer is not None:
                self.win.after_cancel(timer)
        self._poll_timer = self._resize_timer = None
        while True:
            try:
                self._requests.get_nowait()
            except queue.Empty:
                break
        self._requests.put(None)
        # ImageTk objects must be released here, on Tk's owning thread. There are
        # deliberately no StringVar objects for a later worker GC to finalize.
        for pane in self.panes:
            pane['photo'] = None
            pane['result'] = None


def show_comparison(app, selected_row, candidate_rows):
    return FileComparisonWindow(app, selected_row, candidate_rows).win
