#!/usr/bin/env python3
"""Offline native preview of issue #203. Uses temporary fictional history only.

PYTHONPATH=src python3 scripts/preview_map_history.py
No application discovery, process probe, monitor, game, network or profile control.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.play_history import PlayHistoryStore, context, digest, summarize, ISSUE_PREFIX
from smr_launcher.history_ui import HistoryPanel, history_card

EXAMPLES = ("Never opened", "Launch unconfirmed", "Crash during load", "Crash during play",
            "Later success after crash", "Fix implemented · not tested", "Fix tested · issue open",
            "Fix tested · issue closed", "Completed by retirement", "Completed by goals",
            "New edition · related issues", "GitHub unavailable", "Resource stop", "Unknown exit")


def fixture(library, name):
    record = SimpleNamespace(name="A Map with a Longer Scenario Name", archive_sha256=digest(name, "archive"),
                             variant_id=digest(name, "variant"), scenarios=("Scenario 1.xml", "Scenario 2.xml"))
    store = PlayHistoryStore(library)
    ctx = context(record, digest("fictional build"), record.scenarios[0])
    def event(kind, second, **fields):
        return store.event(ctx, kind, at=f"2026-10-11T12:00:{second:02}+00:00", **fields)
    first, second, game, fix = (digest(name, key) for key in ("open1", "open2", "game", "fix"))
    if name == "Never opened":
        return record, store, ctx
    if name == "Launch unconfirmed":
        store.append(event("launch_attempted", 0, attempt_id=first, source="observed"))
        return record, store, ctx
    if name == "New edition · related issues":
        older = {**ctx, "variant_id": digest("old variant")}
        store.append(store.event(older, "process_confirmed", attempt_id=first, source="observed"),
                     store.event(older, "game_started", attempt_id=first, game_id=game),
                     store.event(older, "retired", attempt_id=first, game_id=game))
        for number in (9001, 9002):
            store.link({**ctx, "scenario": ""}, ISSUE_PREFIX+str(number), scope="related")
        return record, store, ctx
    store.append(event("process_confirmed", 0, attempt_id=first, source="observed"))
    if name in ("Completed by retirement", "Completed by goals"):
        store.append(event("game_started", 1, attempt_id=first, game_id=game),
                     event("retired" if name.endswith("retirement") else "goals_met", 2, attempt_id=first, game_id=game))
    elif name == "Unknown exit":
        store.append(event("unknown_exit", 1, attempt_id=first, source="observed"))
    elif name == "Resource stop":
        store.append(event("resource_stop", 1, attempt_id=first, incident_id=digest(name, "incident"), source="observed"))
    else:
        store.append(event("map_loaded", 1, attempt_id=first))
        failure = event("crash_play" if name == "Crash during play" else "crash_load", 2,
                        attempt_id=first, incident_id=digest(name, "incident"), checkpoint="playable")
        store.append(failure)
        if name not in ("Crash during load", "Crash during play"):
            store.link(ctx, ISSUE_PREFIX+"9001")
            if name.startswith("Fix"):
                store.append(event("fix_implemented", 3, fix_id=fix, issue_url=ISSUE_PREFIX+"9001"))
            else:
                store.append(event("fix_proposed", 3, fix_id=fix, issue_url=ISSUE_PREFIX+"9001"))
            store.append(event("process_confirmed", 4, attempt_id=second, source="observed"),
                         event("game_started", 5, attempt_id=second, game_id=game),
                         event("playable", 6, attempt_id=second))
            if name.startswith("Fix tested"):
                store.append(event("fix_test_passed", 7, fix_id=fix, attempt_id=second,
                                   target_id=failure["event_id"], checkpoint="playable", issue_url=ISSUE_PREFIX+"9001"))
            state = "unavailable" if name == "GitHub unavailable" else "closed" if name.endswith("issue closed") else "open"
            store.append(event("issue_state", 8, source="observed", issue_url=ISSUE_PREFIX+"9001", issue_state=state))
    return record, store, ctx


class Preview:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="smr-history-preview-")
        self.root = tk.Tk()
        self.root.title("Issue #203 — implemented history · synthetic data")
        self.root.geometry("1110x780")
        self.root.minsize(940, 690)
        self.examples = {name: fixture(Path(self.temporary.name).resolve() / str(i), name) for i, name in enumerate(EXAMPLES)}
        self.selected = tk.StringVar(value="Later success after crash")
        header = ttk.Frame(self.root, padding=20); header.pack(fill="x")
        ttk.Label(header, text="Map card history", font=("Helvetica Neue", 23, "bold")).pack(anchor="w")
        ttk.Label(header, text="Implemented components · Fictional records in a temporary library · Compatibility programme paused").pack(anchor="w", pady=(4, 12))
        choices = ttk.Combobox(header, textvariable=self.selected, values=EXAMPLES, state="readonly", width=48)
        choices.pack(anchor="w"); choices.bind("<<ComboboxSelected>>", lambda _: self.render())
        self.body = ttk.Frame(self.root, padding=(20, 0, 20, 20)); self.body.pack(fill="both", expand=True)
        self.render()
        self.root.after_idle(self.audit)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def render(self):
        for child in self.body.winfo_children(): child.destroy()
        record, store, ctx = self.examples[self.selected.get()]
        self.card = history_card(self.body, title=record.name, summary=summarize(store.records(), ctx),
            verification="Not verified", metadata=SimpleNamespace(author="Example Author", created="15 June 2010"),
            palette=("#252527", "#f3f3f4", "#b5b5b8", "#454549", "#6d9ee8"))
        self.card.pack(side="left", anchor="n", padx=(0, 20))
        notebook = ttk.Notebook(self.body); notebook.pack(fill="both", expand=True)
        self.panel = HistoryPanel(notebook, record, store, ctx["game_executable_sha256"], changed=self.render)

    def audit(self):
        receipts = []
        for name in EXAMPLES:
            self.selected.set(name); self.render(); self.root.update_idletasks()
            left, top = self.card.winfo_rootx(), self.card.winfo_rooty()
            right, bottom = left+self.card.winfo_width(), top+self.card.winfo_height()
            def children(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from children(child)
            clipped = [str(w) for w in children(self.card) if w.winfo_rootx() < left or w.winfo_rooty() < top or
                       w.winfo_rootx()+w.winfo_width() > right or w.winfo_rooty()+w.winfo_height() > bottom]
            receipts.append(dict(example=name, width=self.card.winfo_width(), height=self.card.winfo_height(), clipped=clipped))
        print(json.dumps(dict(pid=os.getpid(), geometry=self.root.geometry(), cards=receipts)), flush=True)
        self.selected.set("Later success after crash"); self.render()

    def close(self):
        self.root.destroy(); self.temporary.cleanup()


if __name__ == "__main__":
    Preview().root.mainloop()
