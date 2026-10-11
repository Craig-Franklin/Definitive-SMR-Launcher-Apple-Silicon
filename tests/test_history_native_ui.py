"""Native widgets with injected fictional records. No LauncherWindow initialization."""

import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
import time
from unittest.mock import Mock, patch

from smr_launcher.gui import LauncherWindow
from smr_launcher.history_ui import HistoryPanel, history_card, gallery_columns
from smr_launcher.play_history import summarize, digest
from smr_launcher.text_links import render_linked_text
from smr_launcher.map_metadata import MapMetadata


class NativeHistoryContracts(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest("Native Tk display unavailable: " + str(exc))
        self.root.title("Issue #203 · synthetic widget checks")
        self.addCleanup(self.root.destroy)
        self.root.geometry("1050x780")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.library = Path(temporary.name).resolve()
        self.palette = ("#252527", "#f3f3f4", "#b5b5b8", "#454549", "#6d9ee8")
        path = Path(__file__).resolve().parents[1] / "scripts/preview_map_history.py"
        spec = importlib.util.spec_from_file_location("history_preview", path)
        self.preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.preview)

    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from NativeHistoryContracts.descendants(child)

    def assert_inside(self, card):
        left, top = card.winfo_rootx(), card.winfo_rooty()
        right, bottom = left + card.winfo_width(), top + card.winfo_height()
        for child in self.descendants(card):
            self.assertGreaterEqual(child.winfo_rootx(), left)
            self.assertGreaterEqual(child.winfo_rooty(), top)
            self.assertLessEqual(child.winfo_rootx() + child.winfo_width(), right, str(child))
            self.assertLessEqual(child.winfo_rooty() + child.winfo_height(), bottom, str(child))

    def test_fourteen_states_and_long_metadata_wrap_without_clipping(self):
        for i, name in enumerate(self.preview.EXAMPLES):
            record, store, ctx = self.preview.fixture(self.library / str(i), name)
            card = history_card(
                self.root,
                title=record.name + " with an exceptionally long title extension",
                summary=summarize(store.records(), ctx),
                verification="Not verified",
                metadata=SimpleNamespace(
                    author="An author with an exceptionally long author name",
                    created="15 June 2010",
                ),
                palette=self.palette,
            )
            card.pack(anchor="nw")
            self.root.update_idletasks()
            self.assertEqual(card.winfo_width(), 230)
            self.assert_inside(card)
            card.destroy()

    def test_actual_gallery_reflows_and_equalizes_each_row(self):
        window = LauncherWindow.__new__(LauncherWindow)
        window.root = self.root
        window.surface, window.text, window.secondary, window.border, window.accent = self.palette
        window.selected_profile = None
        window.search = tk.StringVar()
        window.crash_monitor = None
        window.closed = window.busy = False
        window.view = "maps"
        window.map_filter = tk.StringVar(value="All maps")
        window.sort_order = tk.StringVar(value="Name A–Z")
        window.status = tk.StringVar()
        window.gallery_count = Mock()
        window.images = {}
        window._localize_widgets = Mock()
        window._background = lambda operation, finished, **kw: finished(True, operation())
        records = [
            SimpleNamespace(
                name="Map " + ("long title " * (i + 1)),
                archive_sha256=hex(i + 1)[2:] * 64,
                variant_id=hex(i + 5)[2:] * 64,
                profile_id=str(i),
                archive_filename=str(i) + ".7z",
            )
            for i in range(4)
        ]
        window.app = SimpleNamespace(
            library=self.library,
            installation=SimpleNamespace(executable_sha256="f" * 64),
            catalogue=lambda: records,
            map_metadata=lambda _: MapMetadata(
                author="Synthetic author", created="11 October 2026"
            ),
            verification_for=lambda _: None,
            map_icon=lambda _: None,
        )
        window._art = lambda _: None
        window._selection_details = Mock()
        window.canvas = tk.Canvas(self.root, highlightthickness=0)
        window.canvas.pack(fill="both", expand=True)
        window.grid_frame = ttk.Frame(window.canvas)
        window.canvas_window = window.canvas.create_window(
            (0, 0), window=window.grid_frame, anchor="nw"
        )
        window.columns = 0
        for width in (1000, 790, 550, 290):
            self.root.geometry(f"{width}x780")
            self.root.update_idletasks()
            window._resize_gallery(SimpleNamespace(width=width))
            deadline = time.monotonic() + 3
            while (
                len(window.cards) < len(records) or window._gallery_after_id is not None
            ) and time.monotonic() < deadline:
                self.root.update()
            self.assertEqual(len(window.cards), len(records))
            self.assertEqual(window.columns, gallery_columns(width))
            cards = list(window.cards.values())
            for card in cards:
                self.assert_inside(card)
                self.assertLessEqual(card.winfo_x() + card.winfo_width(), width)
            for offset in range(0, len(cards), window.columns):
                self.assertEqual(
                    len({c.winfo_height() for c in cards[offset : offset + window.columns]}), 1
                )

    def test_history_links_and_confirmation_forms_use_real_native_widgets(self):
        record, store, ctx = self.preview.fixture(self.library, "Later success after crash")
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)
        panel = HistoryPanel(notebook, record, store, ctx["game_executable_sha256"])
        self.root.update_idletasks()
        box = panel.boxes["Play History"]
        self.assertGreater(len(box.tag_ranges("url_0")), 0)
        self.assertNotIn("archive_sha256", box.get("1.0", "end"))
        self.assertIn(ctx["archive_sha256"], panel.boxes["History Scope"].get("1.0", "end"))
        panel.checkpoint_dialog()
        panel.fix_dialog()
        panel.link_dialog()
        self.root.update_idletasks()
        # Dialogs are children of their native history tab, rather than root.
        dialogs = [w for w in self.descendants(self.root) if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(dialogs), 3)
        for dialog in dialogs:
            self.assertGreater(dialog.winfo_reqheight(), 100)
            dialog.destroy()
        panel.checkpoint_dialog()
        dialog = next(w for w in self.descendants(self.root) if isinstance(w, tk.Toplevel))
        combos = {
            int(w.grid_info()["row"]): w
            for w in self.descendants(dialog)
            if isinstance(w, ttk.Combobox)
        }
        combos[2].set(record.scenarios[0])
        combos[2].event_generate("<<ComboboxSelected>>")
        combos[3].set("Playable")
        confirm = next(
            w
            for w in self.descendants(dialog)
            if isinstance(w, ttk.Button) and w.cget("text") == "Record confirmation"
        )
        before = len(store.records())
        with patch("smr_launcher.history_ui.messagebox.showerror") as error:
            confirm.invoke()
            error.assert_not_called()
        self.assertEqual(len(store.records()), before + 1)
        self.assertEqual(store.records()[-1]["source"], "player_confirmed")
        opener = Mock()
        render_linked_text(box, "https://example.org/synthetic", opener=opener)
        self.root.update()
        bounds = box.bbox("1.0")
        self.assertIsNotNone(bounds)
        x, y, _, height = bounds
        box.event_generate("<Motion>", x=x + 2, y=y + height // 2)
        box.event_generate("<Button-1>", x=x + 2, y=y + height // 2)
        self.root.update()
        opener.assert_called_once_with("https://example.org/synthetic")

    def test_after_exit_later_keeps_outcome_unknown_and_deduplicates_prompt(self):
        record, store, ctx = self.preview.fixture(self.library, "Unknown exit")
        event = next(e for e in store.records() if e["kind"] == "unknown_exit")
        record.profile_id = "synthetic"
        window = LauncherWindow.__new__(LauncherWindow)
        window.root = self.root
        window.closed = False
        window.app = SimpleNamespace(
            library=self.library,
            installation=SimpleNamespace(executable_sha256=ctx["game_executable_sha256"]),
        )
        window._records = lambda: (record,)
        window.status = tk.StringVar()
        window._history_exit_prompt(event)
        self.root.update_idletasks()
        buttons = [
            w
            for w in self.descendants(self.root)
            if isinstance(w, ttk.Button) and w.cget("text") == "Later"
        ]
        self.assertEqual(len(buttons), 1)
        buttons[0].invoke()
        window._history_exit_prompt(event)
        self.assertEqual([w for w in self.descendants(self.root) if isinstance(w, tk.Toplevel)], [])
        prompts = [e for e in store.records() if e["kind"] == "prompt_offered"]
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["event_id"], digest(event["attempt_id"], "prompt"))
        self.assertEqual(summarize(store.records(), ctx).label, "Outcome unknown")
