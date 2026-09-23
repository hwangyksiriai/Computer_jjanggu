"""Small mail connection form. Worker arguments contain no Tk objects."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from mail_providers import PROVIDERS, profile_values
from mail_store import MailStore
from mail_sync import ImportResult, import_eml, sync_profile

BG = '#FFFDF9'
INK = '#292A28'
FONT = '맑은 고딕'


def _worker(data, request, responses, cancelled):
    """Never retain a widget, app, bound UI method, or tkinter.Variable here."""
    result, identity = ImportResult(), None
    try:
        store = MailStore(data)
        if request['kind'] == 'eml':
            result = import_eml(store, request['paths'], cancelled.is_set)
        else:
            profile = store.save_profile(request['provider'], request['address'], request['host'])
            identity = profile['id']
            secret = request.pop('secret', '') or store.saved_secret(identity)
            if not request['remember']:
                store.store_secret(identity, None)
            result = sync_profile(store, profile, secret, cancelled.is_set)
            if not result.error and not result.cancelled and request['remember']:
                try:
                    store.store_secret(identity, secret)
                except ValueError:
                    result.error = '파일은 가져왔지만 비밀번호는 저장하지 못했어요. 다음에 다시 입력해 주세요.'
            secret = ''
    except ValueError as error:
        result.error = str(error)
    except Exception:
        result.error = '메일을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.'
    finally:
        request.pop('secret', None)
        responses.put((result, identity))


class MailConnectionsWindow:
    def __init__(self, app):
        self.app = app
        self.store = MailStore(app.data)
        self.closed, self.busy = False, False
        self.responses = queue.Queue()
        self.cancelled = threading.Event()
        self.timer = None
        self.profiles = []
        self.selected_id = None
        self.win = tk.Toplevel(app.root)
        self.win.mail_connections = self
        self.win.title('메일 첨부파일')
        self.win.configure(bg=BG)
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        self.win.bind('<Destroy>', self._destroy, add='+')
        from accessibility import fit_window, scroll_page, apply_fonts
        fit_window(self.win, 690, 760, 520, 460)
        self.footer = tk.Frame(self.win, bg=BG)
        self.footer.pack(side='bottom', fill='x', padx=18, pady=12)
        self.status = tk.Label(self.footer, text='필요할 때 가져오기 버튼을 눌러 주세요.', anchor='w', justify='left', bg=BG, fg=INK, font=(FONT, 10), wraplength=610)
        self.status.pack(fill='x', pady=(0,8))
        actions = tk.Frame(self.footer, bg=BG)
        actions.pack(fill='x')
        self.connect_button = tk.Button(actions, text='첨부파일 가져오기', command=self.connect, font=(FONT, 12, 'bold'), bg='#F9D675', relief='flat', padx=16, pady=8)
        self.connect_button.pack(side='left')
        self.stop_button = tk.Button(actions, text='멈추기', command=self.stop, state='disabled', font=(FONT, 11), relief='flat')
        self.stop_button.pack(side='left', padx=12)
        tk.Button(actions, text='닫기', command=self.close, font=(FONT,11), relief='flat').pack(side='right')
        self.body = scroll_page(self.win, BG)
        body = self.body
        body.configure(padx=18, pady=16)
        self._label(body, '메일 속 파일도 찾아드릴게요', 18, True).pack(anchor='w')
        self._label(body, '최근 90일 받은 편지함에서 최대 100통을 확인해요.\n원본 메일과 읽음 표시는 그대로 두어요.', 10).pack(anchor='w', pady=(8,16))
        imports = tk.Frame(body, bg=BG)
        imports.pack(fill='x', pady=(0,18))
        self.import_button = tk.Button(imports, text='저장한 메일(.eml) 가져오기', command=self.import_files, font=(FONT,11), relief='flat', bg='#F1F0EB', padx=8, pady=7)
        self.import_button.pack(side='left')
        self.files_button = tk.Button(imports, text='가져온 파일 보기', command=self.show_files, font=(FONT,11), relief='flat')
        self.files_button.pack(side='left', padx=8)
        self._label(body, '연결할 메일', 12, True).pack(anchor='w')
        self.accounts = ttk.Combobox(body, state='readonly', font=(FONT,11))
        self.accounts.pack(fill='x', pady=(6,12))
        self.accounts.bind('<<ComboboxSelected>>', self.select_account)
        self.providers = [key for key in PROVIDERS if key != 'eml']
        self.provider = ttk.Combobox(body, values=[PROVIDERS[x]['label'] for x in self.providers], state='readonly', font=(FONT,11))
        self.provider.current(0)
        self.provider.pack(fill='x', pady=(0,10))
        self.provider.bind('<<ComboboxSelected>>', self.provider_changed)
        self._label(body, '메일 주소').pack(anchor='w')
        self.address = tk.Entry(body, font=(FONT,12), relief='solid', bd=1)
        self.address.pack(fill='x', ipady=6, pady=(4,10))
        self.host_frame = tk.Frame(body, bg=BG)
        self._label(self.host_frame, '받는 메일 서버 (SSL IMAP)').pack(anchor='w')
        self.host = tk.Entry(self.host_frame, font=(FONT,12), relief='solid', bd=1)
        self.host.pack(fill='x', ipady=6, pady=(4,10))
        self.password_label = self._label(body, '앱 비밀번호')
        self.password_label.pack(anchor='w')
        self.password = tk.Entry(body, show='●', font=(FONT,12), relief='solid', bd=1)
        self.password.pack(fill='x', ipady=6, pady=(4,6))
        self.remember = tk.BooleanVar(self.win, False)
        self.remember_button = tk.Checkbutton(body, text='이 Windows 계정에 비밀번호 저장', variable=self.remember, bg=BG, font=(FONT,10), anchor='w')
        self.remember_button.pack(fill='x')
        self.note = self._label(body, '', 10)
        self.note.pack(fill='x', pady=(10,4))
        self.help_button = tk.Button(body, text='설정 방법 보기', command=self.help, font=(FONT,10), bg=BG, fg='#446688', relief='flat', anchor='w')
        self.help_button.pack(anchor='w')
        self.manage = tk.Frame(body, bg=BG)
        self.manage.pack(fill='x', pady=(16,0))
        self.disconnect_button = tk.Button(self.manage, text='연결 해제', command=self.disconnect, font=(FONT,10), relief='flat')
        self.disconnect_button.pack(side='left')
        self.clear_button = tk.Button(self.manage, text='이 컴퓨터의 첨부파일 비우기', command=self.clear_cache, font=(FONT,10), relief='flat')
        self.clear_button.pack(side='left', padx=8)
        body.bind('<Configure>', self.resize, add='+')
        self.refresh_accounts()
        self.provider_changed()
        apply_fonts(self.win, getattr(app, 'settings', {}).get('text_scale', 1.0))

    def _label(self, parent, text, size=11, bold=False):
        return tk.Label(parent, text=text, bg=BG, fg=INK, justify='left', anchor='w', font=(FONT,size,'bold' if bold else 'normal'), wraplength=580)

    def resize(self, event=None):
        if self.closed:
            return
        self.note.configure(wraplength=max(280,self.body.winfo_width()-40))
        self.status.configure(wraplength=max(280,self.win.winfo_width()-40))

    def refresh_accounts(self, selected=None):
        self.profiles = self.store.profiles(include_disabled=True)
        labels = ['새 메일 연결'] + [(p['address'] or '저장한 메일(.eml)') + (' (연결 해제됨)' if not p['enabled'] else '') for p in self.profiles]
        self.accounts.configure(values=labels)
        index = next((i+1 for i,p in enumerate(self.profiles) if p['id']==selected), 0)
        self.accounts.current(index)
        self.select_account()

    def select_account(self, event=None):
        index = self.accounts.current()-1
        profile = self.profiles[index] if 0 <= index < len(self.profiles) else None
        self.selected_id = profile['id'] if profile else None
        for entry in (self.address,self.host,self.password):
            entry.configure(state='normal')
            entry.delete(0,'end')
        if profile and profile['provider'] != 'eml':
            self.provider.current(self.providers.index(profile['provider']))
            self.address.insert(0,profile['address'])
            self.host.insert(0,profile['host'])
        self.remember.set(bool(profile and profile['has_saved_secret']))
        self.provider_changed()
        self.disconnect_button.configure(state='normal' if profile and profile['enabled'] and profile['provider'] != 'eml' else 'disabled')
        self.clear_button.configure(state='normal' if profile else 'disabled')
        self.password_label.configure(text='앱 비밀번호 (저장된 비밀번호 사용 가능)' if profile and profile['has_saved_secret'] else '앱 비밀번호')

    def provider_changed(self, event=None):
        provider = self.providers[max(0,self.provider.current())]
        if provider == 'custom':
            self.host_frame.pack(fill='x', before=self.password_label)
        else:
            self.host_frame.pack_forget()
        self.note.configure(text=PROVIDERS[provider]['note'])
        self.help_button.configure(state='normal' if PROVIDERS[provider]['help'] else 'disabled')
        available = provider != 'outlook' and not self.busy
        self.connect_button.configure(state='normal' if available else 'disabled')
        self.password.configure(state='normal' if available else 'disabled')
        self.remember_button.configure(state='normal' if available else 'disabled')

    def help(self):
        provider = self.providers[max(0,self.provider.current())]
        url = PROVIDERS[provider]['help']
        if url:
            webbrowser.open(url)

    def connect(self):
        if self.busy:
            return
        provider = self.providers[max(0,self.provider.current())]
        try:
            values = profile_values(provider,self.address.get(),self.host.get())
        except ValueError as error:
            self.status.configure(text=str(error))
            return
        secret = self.password.get()
        if provider in {'gmail','naver','works'}:
            secret = secret.replace(' ','')
        request = dict(kind='sync', secret=secret, remember=self.remember.get(), **values)
        self.password.delete(0,'end')
        self.start(request)

    def import_files(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self.win, title='저장한 메일 선택', filetypes=[('저장한 메일','*.eml')])
        if paths:
            self.start(dict(kind='eml', paths=list(paths)))

    def start(self, request):
        if self.busy or self.closed:
            return
        self.busy = True
        self.cancelled.clear()
        for widget in (self.connect_button,self.import_button,self.disconnect_button,self.clear_button,self.provider,self.accounts):
            widget.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.status.configure(text='메일 첨부파일을 가져오고 있어요…')
        # Root-owned polling survives closing this window so committed attachments
        # still reach app search; no Tk references are passed to the daemon.
        threading.Thread(target=_worker,args=(str(self.app.data),request,self.responses,self.cancelled),daemon=True).start()
        self.timer = self.app.root.after(70,self.poll)

    def poll(self):
        self.timer = None
        try:
            result, identity = self.responses.get_nowait()
        except queue.Empty:
            self.timer = self.app.root.after(70,self.poll)
            return
        self.busy = False
        if result.files:
            callback = getattr(self.app,'mail_attachments_changed',None)
            if callback:
                callback()
        if self.closed:
            return
        self.status.configure(text=result.summary())
        self.import_button.configure(state='normal')
        self.provider.configure(state='readonly')
        self.accounts.configure(state='readonly')
        self.stop_button.configure(state='disabled')
        self.refresh_accounts(identity)

    def stop(self):
        self.cancelled.set()
        if not self.closed:
            self.status.configure(text='멈추고 있어요. 서버 응답을 기다리는 중이면 잠시 걸릴 수 있어요.')
            self.stop_button.configure(state='disabled')

    def show_files(self):
        callback = getattr(self.app,'show_mail_attachments',None)
        if callback:
            callback()

    def disconnect(self):
        if self.busy or not self.selected_id:
            return
        self.store.disconnect(self.selected_id)
        self.refresh_accounts(self.selected_id)
        self.status.configure(text='연결을 해제했어요. 가져온 파일은 이 컴퓨터에 남아 있어요.')

    def clear_cache(self):
        if self.busy or not self.selected_id:
            return
        if not messagebox.askyesno('가져온 파일 비우기','이 연결에서 가져온 첨부파일을 이 컴퓨터에서 지울까요?\n원본 메일과 직접 저장한 원본 파일은 그대로 남아요.',parent=self.win):
            return
        try:
            self.store.clear_cache(self.selected_id)
        except (ValueError,OSError):
            self.status.configure(text='보관함을 비우지 못했어요. 열려 있는 첨부파일을 닫고 다시 해주세요.')
            return
        callback = getattr(self.app,'mail_attachments_changed',None)
        if callback:
            callback()
        self.status.configure(text='이 컴퓨터에 가져온 첨부파일을 비웠어요.')

    def close(self):
        if self.closed:
            return
        self.cancelled.set()
        self.win.destroy()

    def _destroy(self,event):
        if event.widget is not self.win or self.closed:
            return
        self.closed = True
        self.cancelled.set()
        # tkinter.Variable cleanup must occur on this UI thread, not a worker.
        self.remember = None
        if getattr(self.app,'mail_connections_window',None) is self.win:
            self.app.mail_connections_window = None


def show_mail_connections(app):
    existing = getattr(app,'mail_connections_window',None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                return existing
        except tk.TclError:
            pass
    controller = MailConnectionsWindow(app)
    app.mail_connections_window = controller.win
    return controller.win
