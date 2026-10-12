#!/usr/bin/env python3
"""Root-only visible baseline: exact exported Tk HistoryPanel, synthetic temp data.

Export the pinned source using the documented git archive command. This never
constructs LauncherWindow. Only its history form is instantiated. Metadata facts
are displayed in a clearly labeled synthetic review worksheet: the original Tk
baseline had no personal-details editor, and this does not pretend otherwise.
"""

import argparse
import importlib
import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import tkinter as tk
from tkinter import ttk

from workflow_fixture import ATTEMPT, BUILD, SCENARIO, records, seed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--post-exit", action="store_true")
    args = parser.parse_args()
    expected = {
        "history_ui.py": "374c2be032d6c460d91e22f80c326d4bbdc5cc632c86b84987f7f78f5160a09a",
        "play_history.py": "20fa490d2f57c5e1aaa8a3fcfb5f58bb2f3ecf36e7db91804458b9b59ecd4bee",
    }
    for name, checksum in expected.items():
        source = args.source_root / "src" / "smr_launcher" / name
        if hashlib.sha256(source.read_bytes()).hexdigest() != checksum:
            parser.error("The exported baseline source does not match 5c1185b: " + name)
    # Remove current modules before loading the immutable exported baseline.
    for name in tuple(sys.modules):
        if name == "smr_launcher" or name.startswith("smr_launcher."):
            del sys.modules[name]
    sys.path.insert(0, str(args.source_root.resolve() / "src"))
    history = importlib.import_module("smr_launcher.history_ui")
    ledger = importlib.import_module("smr_launcher.play_history")
    root = tk.Tk()
    root.title("ISOLATED SYNTHETIC BASELINE 5c1185b — no launcher service")
    root.geometry("1100x820")
    with TemporaryDirectory(prefix="smr-workflow-baseline-") as folder:
        save = Path(folder) / "synthetic.sav"
        save.write_bytes(b"Fictional save fixture; not a game asset.")
        store = ledger.PlayHistoryStore(Path(folder).resolve())
        record = records()[0]
        seed(store, record)
        head = ttk.Frame(root, padding=16)
        head.pack(fill="x")
        ttk.Label(
            head,
            text="Exact baseline checkpoint form — synthetic identified session",
            font=("Helvetica Neue", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            head,
            text="Original source: 5c1185bacb50bd34aa8f53a822ad303b84da8101. Temporary ledger only; no game or application owner.",
        ).pack(anchor="w")
        ttk.Label(
            head,
            text=f"Known session: {ATTEMPT[:12]} · {SCENARIO} · recorded game binding. Enter Played, Saved, Reopened, Continued as separate baseline checkpoints.",
        ).pack(anchor="w")
        ttk.Label(head, text="Exact synthetic save (read only): " + str(save)).pack(anchor="w")
        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=16, pady=16)
        panel = history.HistoryPanel(notebook, record, store, BUILD)
        # The baseline's issue refresh/open buttons are outside this task and must
        # never contact a provider. Disable every toolbar except checkpoints.
        for name, tab in panel.tabs.items():
            if name != "Play History":
                for child in tab.winfo_children():
                    if isinstance(child, ttk.Frame):
                        for button in child.winfo_children():
                            if isinstance(button, ttk.Button):
                                button.configure(state="disabled")
        ttk.Button(
            head,
            text="Open identified post-exit form (attempt prefilled)",
            command=lambda: panel.checkpoint_dialog(ATTEMPT),
        ).pack(anchor="w", pady=8)
        if args.post_exit:
            root.after_idle(lambda: panel.checkpoint_dialog(ATTEMPT))
        worksheet = ttk.LabelFrame(
            root,
            text="External synthetic collection worksheet — absent from original launcher",
            padding=12,
        )
        worksheet.pack(fill="x", padx=16, pady=(0, 16))
        ttk.Label(
            worksheet,
            text="Known package facts: Synthetic Known · Synthetic Author · 1.0 · 12 June 2010 · Fictional coast railway.\nMissing example: author/date/source/image unknown. No fact may be invented. Original baseline cannot save personal overrides.",
        ).pack(anchor="w")
        ttk.Entry(worksheet, width=100).pack(fill="x", pady=8)
        ttk.Label(
            worksheet,
            text="Use only if the coordinator accepts external transcription as representative. No automatic action-reduction claim follows.",
        ).pack(anchor="w")
        root.mainloop()


if __name__ == "__main__":
    main()
