"""Validated, copy-only file transfers. No file is changed by this module."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import threading
import weakref


COPY_MESSAGE = "파일을 복사했어요. 붙일 곳에서 Ctrl+V를 누르세요."
COPY = "copy"
DND_FILES = "DND_Files"
REFUSE_DROP = "refuse_drop"


def validate_file_paths(paths) -> tuple[str, ...]:
    """Return canonical, deduplicated local files, or reject the entire request.

    A single string/Path is one file. All supplied items are checked, including
    duplicates. This is shared by native clipboard and drag sources; it neither
    reads file contents nor writes files. Relative paths resolve from the cwd.
    """
    if isinstance(paths, (str, os.PathLike)):
        paths = (paths,)
    try:
        items = iter(paths)
    except TypeError as exc:
        raise ValueError("먼저 보낼 파일을 골라 주세요.") from exc
    result, seen = [], set()
    try:
        for item in items:
            try:
                value = os.fspath(item)
            except TypeError as exc:
                raise ValueError("파일 경로를 확인할 수 없어요.") from exc
            if not isinstance(value, str) or not value or "\0" in value:
                raise ValueError("파일 경로를 확인할 수 없어요.")
            scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", value)
            if scheme and not (os.name == "nt" and len(scheme[1]) == 1):
                raise ValueError("웹 주소 대신 컴퓨터에 저장된 파일을 골라 주세요.")
            try:
                path = Path(value).expanduser().resolve(strict=True)
                if not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError("폴더 대신 파일을 골라 주세요.")
            except (OSError, RuntimeError) as exc:
                raise ValueError("파일이 없거나 읽을 수 없어요. 다시 찾아 주세요.") from exc
            canonical = str(path)
            key = os.path.normcase(canonical)
            if key not in seen:
                seen.add(key)
                result.append(canonical)
    except (TypeError, OSError) as exc:
        raise ValueError("파일 경로를 확인할 수 없어요.") from exc
    if not result:
        raise ValueError("먼저 보낼 파일을 골라 주세요.")
    return tuple(result)


class _FileDragBinding:
    """Own only our Tcl commands and release them on the UI thread.

    Exposed on widget._file_drag for deterministic tests without native drags.
    begin/end are the actual TkDND callbacks. close() is idempotent and must be
    called on the main thread, as must bind_file_drag and widget.destroy().
    """

    def __init__(self, widget, paths_provider, on_status):
        self._widget = weakref.ref(widget)
        self._paths_provider = paths_provider
        self._on_status = on_status
        self._tag = f"FileDrag_{id(self)}"
        self._commands = []
        self._previous = {}
        self._installed = {}
        self._registered = False
        self._suppress_release = False
        self.closed = False

    def install(self):
        widget = self._widget()
        # A second owner of these events would silently alter its drag contract.
        if "TkDND_Drag1" in widget.bindtags():
            raise RuntimeError("Widget already has a drag source")
        sequences = ("<<DragInitCmd>>", "<<DragEndCmd>>", "<<DragSourceTypes>>")
        self._previous = {
            sequence: widget.tk.call("bind", widget._w, sequence)
            for sequence in sequences
        }
        if any(self._previous[seq] for seq in sequences[:2]):
            raise RuntimeError("Widget already has drag callbacks")
        self._registered = True  # Also roll back a partially failed registration.
        before = set(widget._tclCommands or ())
        try:
            widget.drag_source_register(1, DND_FILES)
            widget.dnd_bind("<<DragInitCmd>>", self.begin)
            widget.dnd_bind("<<DragEndCmd>>", self.end)
            # A separate tag preserves ordinary button and double-click binds.
            # Register via this widget, not bind_class (which owns commands on
            # the root and would retain every removed result card until exit).
            for sequence, callback in (
                ("<ButtonPress-1>", self._press),
                ("<ButtonRelease-1>", self._release),
                ("<Destroy>", self._destroy),
            ):
                widget._bind(("bind", self._tag), sequence, callback, False)
            widget.bindtags((self._tag,) + tuple(widget.bindtags()))
        finally:
            self._commands = [
                command for command in (widget._tclCommands or ())
                if command not in before
            ]
            for sequence in sequences:
                self._installed[sequence] = widget.tk.call("bind", widget._w, sequence)

    def _status(self, message):
        if not self.closed and self._on_status:
            try:
                self._on_status(message)
            except Exception:
                # A result panel may have been closed during the native drag.
                pass

    def _disarm_button(self):
        widget = self._widget()
        if widget is None:
            return
        try:
            kind = widget.winfo_class()
            if kind == "Button":
                # Tk's ButtonUp invokes only if Priv(window) is still this
                # button. Leave clears it first, then Up safely resets pressed
                # state and repeat timers without running the button command.
                widget.tk.call("tk::ButtonLeave", widget._w)
                widget.tk.call("tk::ButtonUp", widget._w)
            elif kind == "TButton":
                widget.state(("!pressed",))
        except Exception:
            pass

    def begin(self, event=None):
        if self.closed:
            return REFUSE_DROP
        # DragInit runs only after the movement threshold. Ordinary clicks are
        # untouched; a cancelled drag must not become an accidental open.
        self._suppress_release = True
        self._disarm_button()
        try:
            paths = validate_file_paths(self._paths_provider())
        except ValueError as exc:
            self._status(str(exc))
            return REFUSE_DROP
        except Exception:
            self._status("파일을 가져오지 못했어요. 다시 골라 주세요.")
            return REFUSE_DROP
        self._status("붙일 곳으로 끌어다 놓으세요. 원본은 그대로 있어요.")
        # A nested tuple lets Tcl encode each complete path (including spaces,
        # Korean text and braces). Hand-built brace quoting corrupts filenames.
        return COPY, DND_FILES, paths

    def end(self, event=None):
        if self.closed:
            return REFUSE_DROP
        action = getattr(event, "action", "")
        if action == COPY:
            self._status("끌어놓기를 마쳤어요. 받는 앱에서 첨부 여부를 확인해 주세요.")
        elif action in ("", "none", REFUSE_DROP):
            self._status("끌어놓기가 취소됐거나 지원되지 않는 곳이에요. 파일 복사도 이용할 수 있어요.")
        else:
            # Even an unexpected target response never grants us permission to
            # delete the source. COPY is the only action offered in begin().
            self._status("복사만 지원해요. 받는 앱에서 파일 상태를 확인해 주세요.")
        return COPY if action == COPY else REFUSE_DROP

    def _press(self, event=None):
        self._suppress_release = False

    def _release(self, event=None):
        if self._suppress_release:
            self._suppress_release = False
            # begin() already cleared the source's pressed state. A delayed
            # release after OLE can arrive after Enter on another button;
            # ButtonLeave here would erase that button's global hover state
            # and prevent its next click from invoking its command.
            return "break"
        return None

    def _destroy(self, event):
        if event.widget is self._widget():
            self.close()

    def close(self):
        if self.closed:
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("File drag bindings must be closed on the main thread")
        self.closed = True
        widget = self._widget()
        # Release callbacks (which can retain a Tk window) on this thread.
        self._paths_provider = self._on_status = None
        if widget is None:
            return
        if self._registered:
            try:
                widget.drag_source_unregister()
            except Exception:
                pass
        for sequence, installed in self._installed.items():
            try:
                if widget.tk.call("bind", widget._w, sequence) == installed:
                    widget.tk.call("bind", widget._w, sequence, self._previous.get(sequence, ""))
            except Exception:
                pass
        for sequence in ("<ButtonPress-1>", "<ButtonRelease-1>", "<Destroy>"):
            try:
                widget.tk.call("bind", self._tag, sequence, "")
            except Exception:
                pass
        try:
            widget.bindtags(tuple(tag for tag in widget.bindtags() if tag != self._tag))
        except Exception:
            pass
        for command in self._commands:
            try:
                widget.deletecommand(command)
            except Exception:
                pass
        self._commands.clear()
        if getattr(widget, "_file_drag", None) is self:
            del widget._file_drag


def bind_file_drag(widget, paths_provider, on_status=None) -> bool:
    """Enable COPY-only file dragging, or return False for clipboard fallback.

    The application must first enable tkdnd on its Tk root. No native drag is
    started here. A provider must return this row's own paths, not a previously
    selected row (Button.command usually runs only on release).
    """
    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        import tkinterdnd2  # noqa: F401 — installs widget methods, no Tk root
    except (ImportError, OSError, RuntimeError):
        return False
    if not callable(paths_provider) or not all(
        callable(getattr(widget, name, None))
        for name in ("drag_source_register", "drag_source_unregister", "dnd_bind")
    ):
        return False
    previous = getattr(widget, "_file_drag", None)
    if previous is not None:
        previous.close()
    binding = _FileDragBinding(widget, paths_provider, on_status)
    try:
        binding.install()
    except Exception:
        binding.close()
        return False
    widget._file_drag = binding
    return True
