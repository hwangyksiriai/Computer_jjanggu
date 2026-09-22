"""A quiet, keyboard-friendly large photo view that never changes source files."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import tkinter as tk
from tkinter import font as tkfont

from PIL import ImageTk

from accessibility import apply_fonts, fit_window


BG = '#101827'
PANEL = '#182336'
INK = '#F4F6FA'
MUTED = '#B6C3D5'
ACCENT = '#F8D982'
OFFLINE = 'PC에 내려받지 않은 사진'


def _local_file(path):
    """Metadata checks do not hydrate a cloud placeholder."""
    if not path:
        return False
    try:
        info = Path(path).stat()
        return not bool(getattr(info, 'st_file_attributes', 0) & (0x1000 | 0x400000)) and Path(path).is_file()
    except OSError:
        return False


def _load_photo(row, size):
    # Imported here: the gallery creates this viewer, so module-level imports
    # would create a circular dependency. All file work stays off the Tk thread.
    from photo_gallery import _decode_sources

    original = str(row.get('path') or '')
    thumbnail = str(row.get('thumbnail') or '')
    offline = row.get('status') == OFFLINE
    original_exists = not offline and _local_file(original)
    try:
        can_reveal = bool(original) and Path(original).parent.is_dir()
    except OSError:
        can_reveal = False
    if original_exists:
        picture = _decode_sources((original,), size)
        if picture is not None:
            return picture, '사진 전체를 화면에 맞춰 보여드려요.', True, can_reveal
    # Never access the original through a thumbnail alias on cloud rows.
    if thumbnail and Path(thumbnail) != Path(original) and _local_file(thumbnail):
        picture = _decode_sources((thumbnail,), size)
        if picture is not None:
            if offline:
                message = '저장된 미리보기예요. 원본은 저장된 폴더에서 내려받아 주세요.'
            elif not original_exists:
                message = '원본을 찾을 수 없어 저장된 미리보기를 보여드려요.'
            else:
                message = '저장된 미리보기예요. 더 선명하게 보려면 원본을 열어 주세요.'
            return picture, message, original_exists, can_reveal
    if offline:
        message = '이 사진은 아직 PC에 없어요.\n저장된 폴더에서 내려받은 뒤 다시 열어 주세요.'
    elif not original_exists:
        message = '원본 사진을 찾을 수 없어요.\n파일이 이동됐거나 저장 장치가 연결되지 않았을 수 있어요.'
    else:
        message = '이 사진의 미리보기를 만들 수 없어요.\n원본 열기로 확인해 주세요.'
    return None, message, original_exists, can_reveal


class PhotoViewer:
    def __init__(self, parent, on_move, on_open, on_reveal, on_close, on_delete=None):
        self.on_move = on_move
        self.on_open = on_open
        self.on_reveal = on_reveal
        self.on_close = on_close
        self.on_delete = on_delete
        self._delete_pending = False
        self._can_delete = False
        self.row = None
        self.index = 0
        self.total = 0
        self.photo = None
        self._closed = False
        self._token = 0
        self._displayed_path = None
        self._size = (800, 550)
        self._requested_key = None
        self._name_text = ''
        self._reason_text = ''
        self._text_scale = getattr(parent, '_photo_text_scale', 1)
        self._resize_id = None
        self._poll_id = None
        self._jobs = set()
        self._queue = queue.Queue()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='photo-large-view')

        self.win = tk.Toplevel(parent)
        self.win.title('사진 크게 보기 · 짱구 주머니')
        self.win.configure(bg=BG)
        fit_window(self.win, 1100, 800, 560, 420)
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        self.win.bind('<Escape>', self._escape)
        self.win.bind('<Left>', lambda event: self.move(-1))
        self.win.bind('<Right>', lambda event: self.move(1))
        self.win.bind('<Delete>', lambda event: self.delete_current())
        self.win.bind('<Destroy>', self._destroyed, add='+')

        header = tk.Frame(self.win, bg=BG, padx=22, pady=16)
        header.pack(fill='x')
        self.close_button = self._button(header, '닫기  ×', self.close)
        self.close_button.pack(side='right')
        self.delete_button = self._button(header, '사진 삭제', self.delete_current)
        self.delete_button.configure(fg='#FFC5C5', state='disabled')
        if on_delete:
            self.delete_button.pack(side='right', padx=(0, 8))
        tk.Label(header, text='사진 크게 보기', bg=BG, fg=INK,
                 font=('맑은 고딕', 16, 'bold')).pack(side='left')
        self.position = tk.Label(header, bg=BG, fg=ACCENT, font=('맑은 고딕', 11, 'bold'))
        self.position.pack(side='left', padx=18)

        footer = tk.Frame(self.win, bg=PANEL, padx=22, pady=14)
        footer.pack(side='bottom', fill='x')
        action_row = tk.Frame(footer, bg=PANEL)
        action_row.pack(side='bottom', fill='x', pady=(10, 0))
        self.open_button = self._button(action_row, '원본 열기 ↗', self.open_original, primary=True)
        self.open_button.pack(side='right', padx=(8, 0))
        self.reveal_button = self._button(action_row, '저장된 폴더', self.reveal)
        self.reveal_button.pack(side='right')
        self.hint = tk.Label(action_row, text='← → 이전·다음   ·   Esc 닫기', bg=PANEL,
                             fg=MUTED, font=('맑은 고딕', 9), anchor='w')
        self.hint.pack(side='left', fill='x', expand=True)
        self.name = tk.Label(footer, bg=PANEL, fg=INK, anchor='w', justify='left',
                             font=('맑은 고딕', 12, 'bold'), height=1)
        self.name.pack(fill='x')
        self.reason = tk.Label(footer, bg=PANEL, fg=ACCENT, anchor='w', justify='left',
                               font=('맑은 고딕', 10), height=1)
        self.reason.pack(fill='x', pady=(4, 0))

        self.status = tk.Label(self.win, bg=BG, fg=MUTED, font=('맑은 고딕', 10),
                               justify='center', wraplength=950, height=2)
        self.status.pack(side='bottom', fill='x', padx=24, pady=(4, 12))
        stage = tk.Frame(self.win, bg=BG)
        stage.pack(fill='both', expand=True, padx=14)
        self.previous = self._button(stage, '‹', lambda: self.move(-1))
        self.previous.configure(font=('맑은 고딕', 24), padx=13, pady=5)
        self.previous.pack(side='left', padx=(0, 8))
        self.next = self._button(stage, '›', lambda: self.move(1))
        self.next.configure(font=('맑은 고딕', 24), padx=13, pady=5)
        self.next.pack(side='right', padx=(8, 0))
        self.canvas = tk.Canvas(stage, bg=BG, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', self._resized)
        self.win.bind('<Configure>', self._window_resized, add='+')
        apply_fonts(self.win, self._text_scale)
        self._poll_id = self.win.after(40, self._drain)

    def _button(self, parent, text, command, primary=False):
        bg = ACCENT if primary else '#26344A'
        return tk.Button(parent, text=text, command=command, bg=bg,
                         fg=BG if primary else INK, activebackground='#FFE7A5' if primary else '#364B67',
                         activeforeground=BG if primary else INK, disabledforeground='#65748A',
                         relief='flat', bd=0, padx=14, pady=9, cursor='hand2',
                         font=('맑은 고딕', 10, 'bold'), takefocus=True,
                         highlightthickness=2, highlightbackground=bg, highlightcolor=ACCENT)

    def show(self, row, index, total):
        if self._closed:
            return
        source_changed = self.row is None or self._source_key(self.row) != self._source_key(row)
        self.row = dict(row)
        self.index = index
        self.total = total
        self.position.configure(text=f'{index + 1:,} / {total:,}')
        name = str(row.get('name') or Path(row.get('path') or '').name)
        self._name_text = ' '.join(name.split())
        self._reason_text = ' '.join(str(row.get('reason') or '').split())
        self._fit_captions()
        self.previous.configure(state='normal' if index > 0 else 'disabled')
        self.next.configure(state='normal' if index + 1 < total else 'disabled')
        if source_changed:
            self._can_delete = False
            self._delete_pending = False
            self.set_delete_pending(False)
            self.open_button.configure(state='disabled')
            self.reveal_button.configure(state='disabled')
            self.canvas.delete('all')
            self.photo = None
            self._displayed_path = None
            self.status.configure(text='사진을 크게 불러오고 있어요…')
        self.win.title(f'{name[:90]} · 사진 크게 보기')
        # Background result updates call show too. Only the gallery's explicit
        # open action should raise this window or move keyboard focus into it.
        self._request()

    @staticmethod
    def _source_key(row):
        return tuple(row.get(key) for key in ('path', 'mtime', 'size', 'status', 'thumbnail'))

    def _fit_captions(self):
        # Fixed one-line captions preserve photo space even with a very long
        # filename or a multi-line AI explanation at enlarged text sizes.
        available = max(180, self.win.winfo_width() - 52)
        for label, text in ((self.name, self._name_text), (self.reason, self._reason_text)):
            font = tkfont.Font(root=self.win, font=label.cget('font'))
            if font.measure(text) > available:
                low, high = 0, len(text)
                while low < high:
                    middle = (low + high + 1) // 2
                    if font.measure(text[:middle] + '…') <= available:
                        low = middle
                    else:
                        high = middle - 1
                text = text[:low] + '…'
            label.configure(text=text)

    def _request(self):
        if self._resize_id:
            self.win.after_cancel(self._resize_id)
        self._resize_id = None
        if self._closed or not self.row:
            return
        self._size = (max(120, self.canvas.winfo_width() - 20), max(100, self.canvas.winfo_height() - 20))
        request_key = self._source_key(self.row), self._size
        if request_key == self._requested_key:
            return
        self._requested_key = request_key
        self._token += 1
        token = self._token
        path = self.row.get('path')
        for job in list(self._jobs):
            job.cancel()
        future = self._pool.submit(_load_photo, dict(self.row), self._size)
        self._jobs.add(future)

        def done(job):
            if job.cancelled():
                return
            try:
                result = job.result()
            except Exception:
                result = (None, '사진을 불러오지 못했어요. 저장된 폴더에서 다시 확인해 주세요.', False, False)
            self._queue.put((token, path, result))

        future.add_done_callback(done)

    def _drain(self):
        self._poll_id = None
        if self._closed:
            return
        self._jobs = {job for job in self._jobs if not job.done()}
        for _ in range(12):
            try:
                token, path, (picture, message, can_open, can_reveal) = self._queue.get_nowait()
            except queue.Empty:
                break
            if token != self._token:
                continue
            self.canvas.delete('all')
            self.photo = ImageTk.PhotoImage(picture, master=self.win) if picture is not None else None
            self._displayed_path = path
            if self.photo:
                self.canvas.create_image(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                         image=self.photo, tags='photo')
            else:
                self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                        text='사진을 표시할 수 없어요', fill=MUTED,
                                        font=('맑은 고딕', round(15 * self._text_scale)), tags='empty')
            self.status.configure(text=message)
            self.open_button.configure(state='normal' if can_open else 'disabled')
            self.reveal_button.configure(state='normal' if can_reveal else 'disabled')
            self._can_delete = can_open and self.row.get('status') != OFFLINE
            self.set_delete_pending(self._delete_pending)
        self._poll_id = self.win.after(40, self._drain)

    def _resized(self, event):
        if self._closed:
            return
        self.canvas.coords('photo', event.width // 2, event.height // 2)
        self.canvas.coords('empty', event.width // 2, event.height // 2)
        if self._resize_id:
            self.win.after_cancel(self._resize_id)
        self._resize_id = self.win.after(160, self._request)

    def _window_resized(self, event):
        if event.widget is not self.win:
            return
        width = max(250, event.width - 48)
        self.status.configure(wraplength=width)
        self._fit_captions()
        self.hint.configure(text='← → 이동 · Esc 닫기' if event.width < 720 else '← → 이전·다음   ·   Esc 닫기')

    def move(self, delta):
        if not self._closed and 0 <= self.index + delta < self.total:
            self.on_move(delta)
        return 'break'

    def open_original(self):
        if not self._closed and str(self.open_button.cget('state')) != 'disabled':
            self.on_open()
        return 'break'

    def reveal(self):
        if not self._closed and str(self.reveal_button.cget('state')) != 'disabled':
            self.on_reveal()
        return 'break'

    def set_delete_pending(self, pending):
        self._delete_pending = pending
        self.delete_button.configure(text='처리 중…' if pending else '사진 삭제',
                                     state='normal' if self.on_delete and self._can_delete and not pending else 'disabled')

    def delete_current(self):
        if not self._closed and str(self.delete_button.cget('state')) != 'disabled' and self.on_delete:
            self.on_delete()
        return 'break'

    def _escape(self, event=None):
        self.close()
        return 'break'

    def _destroyed(self, event):
        if event.widget is self.win:
            self.close()

    def close(self):
        if self._closed:
            return 'break'
        self._closed = True
        self._token += 1
        for identifier in (self._resize_id, self._poll_id):
            if identifier:
                try:
                    self.win.after_cancel(identifier)
                except tk.TclError:
                    pass
        self._resize_id = self._poll_id = None
        for job in list(self._jobs):
            job.cancel()
        self._jobs.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.photo = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.on_close()
        return 'break'
