"""Synthetic files/callbacks only: never touches the system clipboard."""

import gc
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

from file_transfer import COPY, DND_FILES, REFUSE_DROP, bind_file_drag, validate_file_paths


class FakeTk:
    def __init__(self):
        self.bindings = {}
        self.calls = []

    def call(self, *args):
        self.calls.append(args)
        if args[0] == "bind":
            key = args[1:3]
            if len(args) == 4:
                self.bindings[key] = args[3]
            return self.bindings.get(key, "")
        return ""


class FakeWidget:
    def __init__(self, *, fail_at=None):
        self.tk = FakeTk()
        self._w = ".file"
        self._tclCommands = []
        self.commands = {}
        self.tags = (self._w, "Button", ".", "all")
        self.registered = False
        self.fail_at = fail_at
        self.serial = 0

    def bindtags(self, tags=None):
        if tags is not None:
            self.tags = tags
        return self.tags

    def drag_source_register(self, button, dtype):
        assert (button, dtype) == (1, DND_FILES)
        self.registered = True
        self.tags = (self.tags[0], "TkDND_Drag1") + self.tags[1:]
        self.tk.call("bind", self._w, "<<DragSourceTypes>>", dtype)
        if self.fail_at == "register":
            raise RuntimeError("no tkdnd engine")

    def drag_source_unregister(self):
        self.registered = False
        self.tags = tuple(t for t in self.tags if t != "TkDND_Drag1")

    def _command(self, callback):
        self.serial += 1
        name = f"command{self.serial}"
        self._tclCommands.append(name)
        self.commands[name] = callback
        return name

    def dnd_bind(self, sequence, callback):
        name = self._command(callback)
        if self.fail_at == sequence:
            raise RuntimeError("failure after Python callback registration")
        self.tk.call("bind", self._w, sequence, f"{name} substitutions")
        return name

    def _bind(self, what, sequence, callback, add):
        name = self._command(callback)
        self.tk.call(*what, sequence, name)
        return name

    def deletecommand(self, name):
        self._tclCommands.remove(name)
        self.commands.pop(name)

    def winfo_class(self):
        return "Button"


class FileTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="file-transfer-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.first = self.base / "급여 {확인} 6월.txt"
        self.second = self.base / "일반 파일.txt"
        self.first.write_text("synthetic one", encoding="utf-8")
        self.second.write_text("synthetic two", encoding="utf-8")
        self.originals = {path: path.read_bytes() for path in (self.first, self.second)}
        self.addCleanup(self.assert_originals_unchanged)

    def assert_originals_unchanged(self):
        for path, data in self.originals.items():
            self.assertEqual(path.read_bytes(), data)

    def bind(self, provider=None, status=None, widget=None):
        widget = widget or FakeWidget()
        self.assertTrue(bind_file_drag(widget, provider or (lambda: [self.first]), status))
        self.addCleanup(widget._file_drag.close)
        return widget, widget._file_drag

    def test_single_string_and_path_are_whole_file(self):
        expected = (str(self.first.resolve()),)
        self.assertEqual(validate_file_paths(str(self.first)), expected)
        self.assertEqual(validate_file_paths(self.first), expected)

    def test_relative_paths_and_duplicate_aliases(self):
        relative = os.path.relpath(self.first)
        paths = validate_file_paths([self.first, relative, self.second])
        self.assertEqual(paths, (str(self.first.resolve()), str(self.second.resolve())))

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive paths")
    def test_windows_case_variants_are_deduplicated(self):
        self.assertEqual(len(validate_file_paths([str(self.first), str(self.first).upper()])), 1)

    def test_empty_invalid_url_bytes_and_directory_rejected(self):
        for invalid in ([], (), None, "", b"file.txt", "bad\0name", self.base,
                        "https://example.com/a.txt", "file:///C:/example.txt", [4]):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(ValueError):
                    validate_file_paths(invalid)

    def test_missing_item_rejects_whole_request(self):
        with self.assertRaises(ValueError):
            validate_file_paths([self.first, self.base / "missing.txt", self.second])

    def test_iterator_accepted(self):
        self.assertEqual(validate_file_paths(p for p in [self.first]), (str(self.first),))

    def test_begin_only_provides_copy_file_tuple(self):
        statuses = []
        widget, binding = self.bind(lambda: [self.first, self.second], statuses.append)
        self.assertEqual(binding.begin(), (COPY, DND_FILES, (str(self.first), str(self.second))))
        self.assertTrue(statuses)
        self.assertIn(("tk::ButtonLeave", widget._w), widget.tk.calls)
        self.assertIn(("tk::ButtonUp", widget._w), widget.tk.calls)

    def test_tcl_callback_payload_roundtrips_unicode_spaces_and_braces(self):
        # This is a Tcl-only interpreter: no GUI, clipboard or native drag.
        import tkinter as tk
        interpreter = tk.Tcl()
        _, binding = self.bind(lambda: [self.first, self.second])
        interpreter.tk.createcommand("file_transfer_fixture", binding.begin)
        try:
            result = interpreter.splitlist(interpreter.eval("file_transfer_fixture"))
            self.assertEqual(result[:2], (COPY, DND_FILES))
            self.assertEqual(interpreter.splitlist(result[2]), (str(self.first), str(self.second)))
        finally:
            interpreter.tk.deletecommand("file_transfer_fixture")
            del interpreter

    def test_provider_is_evaluated_fresh_for_each_drag(self):
        current = [self.first]
        _, binding = self.bind(lambda: list(current))
        self.assertEqual(binding.begin()[2], (str(self.first),))
        current[:] = [self.second]
        self.assertEqual(binding.begin()[2], (str(self.second),))

    def test_file_removed_after_binding_cancels_instead_of_partial_drag(self):
        disposable = self.base / "disposable.txt"
        disposable.write_text("fixture", encoding="utf-8")
        statuses = []
        _, binding = self.bind(lambda: [self.first, disposable], statuses.append)
        disposable.unlink()
        self.assertEqual(binding.begin(), REFUSE_DROP)
        self.assertIn("없거나", statuses[-1])

    def test_provider_failure_and_no_selection_cancel_cleanly(self):
        def fail():
            raise RuntimeError("fixture")
        for provider in (fail, lambda: []):
            with self.subTest(provider=provider):
                _, binding = self.bind(provider)
                self.assertEqual(binding.begin(), REFUSE_DROP)
                self.assertEqual(binding._release(), "break")

    def test_regular_clicks_unchanged_and_drag_release_suppressed(self):
        widget = FakeWidget()
        widget.tk.call("bind", widget._w, "<Double-Button-1>", "original-double")
        widget.tk.call("bind", widget._w, "<ButtonRelease-1>", "original-release")
        _, binding = self.bind(widget=widget)
        self.assertIsNone(binding._press())
        self.assertIsNone(binding._release())
        binding.begin()
        self.assertEqual(binding._release(), "break")
        self.assertIsNone(binding._release())
        binding.begin()
        binding._press()  # Native OLE may consume its own release.
        self.assertIsNone(binding._release())
        self.assertEqual(widget.tk.call("bind", widget._w, "<Double-Button-1>"), "original-double")
        self.assertEqual(widget.tk.call("bind", widget._w, "<ButtonRelease-1>"), "original-release")

    def test_delayed_drag_release_does_not_clear_other_buttons_hover_state(self):
        widget, binding = self.bind()
        binding.begin()
        # Native OLE may finish after the pointer has entered another button.
        # A second tk::ButtonLeave(source) would clear Tk's *global* window
        # hover variable; ButtonDown does not restore it for the next click.
        calls_before_release = list(widget.tk.calls)
        self.assertEqual(binding._release(), "break")
        self.assertEqual(widget.tk.calls, calls_before_release)
        binding._press()
        self.assertIsNone(binding._release())

    def test_end_never_claims_attachment_success_or_changes_files(self):
        statuses = []
        _, binding = self.bind(status=statuses.append)
        for action in (COPY, "move", "link", "none", REFUSE_DROP, "unexpected", ""):
            with self.subTest(action=action):
                result = binding.end(SimpleNamespace(action=action))
                self.assertEqual(result, COPY if action == COPY else REFUSE_DROP)
                self.assertNotIn("첨부 완료", statuses[-1])
                self.assertNotIn("첨부했", statuses[-1])
        binding.end(SimpleNamespace(action=COPY))
        self.assertIn("확인", statuses[-1])

    def test_status_callback_may_close_panel_or_raise(self):
        def unavailable(_message):
            raise RuntimeError("closed synthetic panel")
        _, binding = self.bind(status=unavailable)
        self.assertEqual(binding.begin()[0], COPY)
        self.assertEqual(binding.end(SimpleNamespace(action=COPY)), COPY)

    def test_registration_failure_fully_rolls_back(self):
        for fail_at in ("register", "<<DragInitCmd>>", "<<DragEndCmd>>"):
            with self.subTest(fail_at=fail_at):
                widget = FakeWidget(fail_at=fail_at)
                original_tags = widget.bindtags()
                self.assertFalse(bind_file_drag(widget, lambda: [self.first]))
                self.assertFalse(widget.registered)
                self.assertEqual(widget.bindtags(), original_tags)
                self.assertEqual(widget.commands, {})
                self.assertEqual(widget._tclCommands, [])
                self.assertFalse(hasattr(widget, "_file_drag"))
                for sequence in ("<<DragInitCmd>>", "<<DragEndCmd>>", "<<DragSourceTypes>>"):
                    self.assertEqual(widget.tk.call("bind", widget._w, sequence), "")

    def test_existing_unrelated_drag_owner_is_preserved(self):
        widget = FakeWidget()
        widget.tk.call("bind", widget._w, "<<DragInitCmd>>", "some-other-owner")
        original_tags = widget.bindtags()
        self.assertFalse(bind_file_drag(widget, lambda: [self.first]))
        self.assertEqual(widget.bindtags(), original_tags)
        self.assertEqual(widget.tk.call("bind", widget._w, "<<DragInitCmd>>"), "some-other-owner")

    def test_unsupported_widget_and_unavailable_package_return_false(self):
        self.assertFalse(bind_file_drag(SimpleNamespace(), lambda: [self.first]))
        with patch.dict("sys.modules", {"tkinterdnd2": None}):
            self.assertFalse(bind_file_drag(FakeWidget(), lambda: [self.first]))

    def test_destroy_releases_commands_and_callback_owners_immediately(self):
        class Owner:
            def paths(self):
                return []
            def status(self, message):
                pass
        owner = Owner()
        owner_ref = weakref.ref(owner)
        widget, binding = self.bind(owner.paths, owner.status)
        del owner
        self.assertIsNotNone(owner_ref())
        binding._destroy(SimpleNamespace(widget=widget))
        self.assertTrue(binding.closed)
        self.assertIsNone(owner_ref())  # No worker-thread cyclic GC required.
        self.assertEqual(widget.commands, {})
        self.assertEqual(widget._tclCommands, [])
        self.assertFalse(hasattr(widget, "_file_drag"))
        self.assertEqual(binding.begin(), REFUSE_DROP)
        binding.close()

    def test_rebinding_closes_previous_owner_and_keeps_only_new_commands(self):
        widget, previous = self.bind(lambda: [self.first])
        self.assertTrue(bind_file_drag(widget, lambda: [self.second]))
        current = widget._file_drag
        self.addCleanup(current.close)
        self.assertTrue(previous.closed)
        self.assertEqual(len(widget.commands), 5)
        self.assertEqual(current.begin()[2], (str(self.second),))

    def test_close_preserves_other_callbacks_and_later_bindtags(self):
        widget, binding = self.bind()
        widget.bindtags(widget.bindtags() + ("later-feature",))
        widget.tk.call("bind", widget._w, "<<DragEndCmd>>", "new-owner-command")
        binding.close()
        self.assertIn("later-feature", widget.bindtags())
        self.assertEqual(widget.tk.call("bind", widget._w, "<<DragEndCmd>>"), "new-owner-command")

    def test_worker_close_refuses_without_releasing_tk_references_off_thread(self):
        widget, binding = self.bind()
        errors = []
        def close_from_worker():
            try:
                binding.close()
            except RuntimeError as exc:
                errors.append(exc)
        thread = threading.Thread(target=close_from_worker)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertFalse(binding.closed)
        self.assertTrue(widget.registered)
        binding.close()

    def test_worker_thread_binding_is_refused_without_tk_calls(self):
        widget = FakeWidget()
        results = []
        thread = threading.Thread(target=lambda: results.append(bind_file_drag(widget, lambda: [])))
        thread.start()
        thread.join()
        self.assertEqual(results, [False])
        self.assertEqual(widget.tk.calls, [])

    def test_widget_is_not_kept_alive_by_binding(self):
        widget, binding = self.bind()
        reference = weakref.ref(widget)
        binding.close()
        del widget
        gc.collect()
        self.assertIsNone(reference())


if __name__ == "__main__":
    unittest.main()
