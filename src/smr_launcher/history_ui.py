"""Native history cards and explicit player confirmations. No game control."""

from pathlib import Path
import hashlib
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser

from .play_history import (
    HistoryError,
    LABELS,
    CHECKPOINTS,
    SUCCESS,
    FAILURES,
    IDENTITIES,
    context,
    digest,
    same_map,
    same_scope,
    summarize,
    issue_readback,
    issue_number,
    utcnow,
)
from .scenario_evidence import open_plain
from .text_links import render_linked_text
from .play_history import instant

CARD_WIDTH = 230


def gallery_columns(width):
    return max(1, (width - 34) // (CARD_WIDTH + 16))


def history_card(
    parent, *, title, summary, verification, metadata, palette, photo=None, selected=False, error=""
):
    """The approved Roomier layout; natural height, full wrapping, no hidden facts."""
    surface, text, secondary, border, accent = palette
    card = tk.Frame(
        parent,
        width=CARD_WIDTH,
        background=surface,
        takefocus=True,
        highlightbackground=accent if selected else border,
        highlightthickness=2,
    )
    art = tk.Canvas(
        card, width=CARD_WIDTH - 4, height=144, background=surface, highlightthickness=0
    )
    art.pack(fill="x")
    if photo is None:
        art.create_text(CARD_WIDTH // 2, 72, text="▦", fill=secondary, font=("Helvetica Neue", 30))
    else:
        art.create_image(CARD_WIDTH // 2, 72, image=photo)
    content = tk.Frame(card, background=surface)
    content.pack(fill="x", padx=12, pady=(0, 12))

    def label(value, *, color=text, size=10, bold=False, pady=0):
        item = tk.Label(
            content,
            text=value,
            background=surface,
            foreground=color,
            font=("Helvetica Neue", size, "bold" if bold else "normal"),
            anchor="w",
            justify="left",
            wraplength=202,
        )
        item.pack(fill="x", pady=pady)
        return item

    label(title, size=12, bold=True)
    dark = surface != "#ffffff"
    colors = dict(
        grey=secondary,
        green="#75cf91" if dark else "#226b39",
        amber="#f2b544" if dark else "#855600",
        red="#ff9999" if dark else "#b3261e",
    )
    label(
        "●  " + ("History needs inspection" if error else summary.label),
        color=secondary if error else colors[summary.light],
        bold=True,
        size=11,
        pady=(5, 8),
    )
    metrics = tk.Frame(content, background=surface)
    metrics.pack(fill="x")
    for i, (count, word) in enumerate(
        zip(
            (summary.opens, summary.started, summary.completed, summary.crashes),
            ("opens", "started", "completed", "crashes"),
        )
    ):
        cell = tk.Frame(metrics, background=surface)
        cell.grid(row=i // 2, column=i % 2, sticky="w", pady=2)
        tk.Label(
            cell,
            text="—" if error else str(count),
            background=surface,
            foreground=text,
            font=("Helvetica Neue", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            cell,
            text=" " + word,
            background=surface,
            foreground=secondary,
            font=("Helvetica Neue", 10),
        ).pack(side="left")
    metrics.columnconfigure(0, minsize=106)
    label("Last · " + ("Needs inspection" if error else summary.last), color=secondary, pady=(6, 8))
    tk.Frame(content, height=1, background=border).pack(fill="x", pady=(0, 8))
    facts = tk.Frame(content, background=surface)
    facts.pack(fill="x")
    for i, (key, value) in enumerate(
        (("Issue", summary.issue), ("Fix", summary.fix), ("Test", summary.test))
    ):
        tk.Label(
            facts,
            text=key,
            background=surface,
            foreground=secondary,
            font=("Helvetica Neue", 10),
            anchor="nw",
        ).grid(row=i, column=0, sticky="nw", padx=(0, 10), pady=2)
        tk.Label(
            facts,
            text="Needs inspection" if error else value,
            background=surface,
            foreground=text,
            font=("Helvetica Neue", 10),
            anchor="nw",
            justify="left",
            wraplength=148,
        ).grid(row=i, column=1, sticky="nw", pady=2)
    tk.Frame(content, height=1, background=border).pack(fill="x", pady=(9, 7))
    label(verification, color=secondary)
    label(
        f"{metadata.author or 'Author not provided'}\nCreated {metadata.created or 'date not provided'}",
        color=secondary,
        pady=(3, 0),
    )
    card.update_idletasks()
    card.configure(height=card.winfo_reqheight())
    card.pack_propagate(False)
    return card


def save_digest(path):
    """Read only an explicitly chosen save; retain its digest, never its path/bytes."""
    fd = open_plain(Path(path), os.O_RDONLY)
    try:
        before = os.fstat(fd)
        if not 0 < before.st_size <= 512 * 1024 * 1024:
            raise HistoryError("Choose a nonempty save of at most 512 MiB")
        total, value = 0, hashlib.sha256()
        while total <= before.st_size:
            data = os.read(fd, min(65536, before.st_size + 1 - total))
            if not data:
                break
            total += len(data)
            value.update(data)
        after = os.fstat(fd)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (
            total != before.st_size
            or signature(before) != signature(after)
            or signature(after) != signature(Path(path).lstat())
        ):
            raise HistoryError("The selected save changed while reading")
        return value.hexdigest()
    finally:
        os.close(fd)


def confirm_checkpoint(
    store, ctx, kind, attempt_id, *, game="", save_id="", failure=None, checkpoint="", at=None
):
    """One explicit confirmation, optionally binding a new/resumed game first."""
    rows = store.records()
    when = at or utcnow()
    batch, game_id = [], ""
    if game:
        if not ctx["scenario"]:
            raise HistoryError("Choose the scenario before identifying a game")
        game_id = digest(os.urandom(32).hex()) if game == "new" else game
        binding = "game_started" if game == "new" else "game_resumed"
        bound = any(
            e["kind"] in ("game_started", "game_resumed")
            and same_scope(e, ctx)
            and e["attempt_id"] == attempt_id
            and e["game_id"] == game_id
            for e in rows
        )
        if not bound:
            batch.append(store.event(ctx, binding, at=when, attempt_id=attempt_id, game_id=game_id))
    if kind in ("game_started", "game_resumed"):
        if not game_id or (kind == "game_started") != (game == "new"):
            raise HistoryError("Choose the matching new or existing game")
        if not batch:
            raise HistoryError("This game is already bound to this open")
    else:
        fields = dict(attempt_id=attempt_id, game_id=game_id, save_id=save_id)
        if kind in FAILURES:
            fields.update(
                incident_id=failure["incident_id"] if failure else digest(os.urandom(32).hex()),
                checkpoint=checkpoint,
            )
            # Attribution enriches a retained incident without moving its occurrence.
            if failure:
                when = failure["at"]
        batch.append(store.event(ctx, kind, at=when, **fields))
    return store.append(*batch)


def timeline_text(events, *, detailed=False):
    if not events:
        return "No history recorded. Unobserved checkpoints remain unknown."
    sources = dict(
        observed="automatic observation",
        player_confirmed="player confirmed",
        historical="retained local fact",
    )
    identities = {
        key: {
            value: i + 1 for i, value in enumerate(dict.fromkeys(e[key] for e in events if e[key]))
        }
        for key in ("attempt_id", "game_id", "save_id", "incident_id", "fix_id", "target_id")
    }
    result = []
    for e in events:
        when = e["at"] if detailed else instant(e["at"]).strftime("%d %b %Y · %H:%M:%S UTC")
        parts = [
            f"{when}  ·  {LABELS[e['kind']]}",
            f"{e['scenario'] or 'Scenario unknown'} · {sources[e['source']]}",
        ]
        if detailed:
            parts.extend(f"{key}: {e[key]}" for key in ("event_id", *IDENTITIES))
        references = []
        for key, word in (
            ("attempt_id", "Open"),
            ("game_id", "Game"),
            ("save_id", "Save SHA-256"),
            ("incident_id", "Incident"),
            ("fix_id", "Fix"),
            ("target_id", "Failure event"),
        ):
            if e[key]:
                if detailed:
                    parts.append(f"{word}: {e[key]}")
                else:
                    references.append(
                        f"{'Save' if key == 'save_id' else word} {identities[key][e[key]]}"
                    )
        if references:
            parts.append(" · ".join(references))
        if e["checkpoint"]:
            parts.append("Failed/tested checkpoint: " + LABELS[e["checkpoint"]])
        if e["issue_url"]:
            parts.append(
                e["issue_url"]
                + " · "
                + e["scope"]
                + (" · " + e["issue_state"] if e["issue_state"] else "")
            )
        result.append("\n".join(parts))
    return "\n\n".join(result)


class HistoryPanel:
    def __init__(self, notebook, record, store, game_sha256, *, changed=lambda: None, submit=None):
        self.record, self.store, self.ctx = record, store, context(record, game_sha256)
        self.changed, self.submit = changed, submit
        self.tabs, self.boxes = {}, {}
        for name in ("Play History", "Issues & Fixes", "History Scope"):
            tab = ttk.Frame(notebook, padding=8)
            notebook.add(tab, text=name)
            self.tabs[name] = tab
            bar = ttk.Frame(tab)
            bar.pack(fill="x", pady=(0, 6))
            if name == "Play History":
                ttk.Button(bar, text="Record checkpoints…", command=self.checkpoint_dialog).pack(
                    side="left"
                )
            elif name == "Issues & Fixes":
                ttk.Button(bar, text="Link issue…", command=self.link_dialog).pack(side="left")
                ttk.Button(bar, text="Record fix / test…", command=self.fix_dialog).pack(
                    side="left", padx=6
                )
                ttk.Button(bar, text="Refresh issue states", command=self.refresh_issues).pack(
                    side="left"
                )
                ttk.Button(bar, text="Open linked issue…", command=self.open_issue).pack(
                    side="left", padx=6
                )
            box = tk.Text(
                tab,
                wrap="word",
                width=50,
                height=10,
                font=("Helvetica Neue", 11),
                padx=10,
                pady=10,
                relief="flat",
            )
            scroll = ttk.Scrollbar(tab, command=box.yview)
            box.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            box.pack(fill="both", expand=True)
            self.boxes[name] = box
        self.reload()

    def rows(self):
        return tuple(e for e in self.store.records() if same_map(e, self.ctx))

    def _safe_rows(self):
        try:
            return self.rows()
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Play history needs inspection", str(exc), parent=self.tabs["Play History"]
            )
            return None

    def reload(self):
        if not self.tabs["Play History"].winfo_exists():
            return
        try:
            rows = self.rows()
            summary = summarize(rows, self.ctx)
            history = (
                f"{summary.opens} opens · {summary.started} started · {summary.completed} completed · {summary.crashes} crashes\n"
                + summary.label
                + "\n\n"
                + timeline_text(rows)
            )
            issues = (
                "\n\n".join(
                    f"#{issue_number(e['url'])} · {e['label']}\n{e['url']}\n"
                    f"{e['scenario'] or 'Scenario unknown'} · {e['scope']} scope\nLast successful check: {e['checked_at'] or 'None'}"
                    for e in issue_readback(rows)
                )
                or "No linked issue."
            )
            fixes = [e for e in rows if e["kind"].startswith("fix_")]
            issues += (
                "\n\nFix implementation, fix testing, issue state and game completion are independent.\n\n"
                + timeline_text(fixes)
            )
            all_rows = self.store.records()
            older = [
                e
                for e in all_rows
                if e["archive_sha256"] == self.ctx["archive_sha256"] and not same_map(e, self.ctx)
            ]
            scope = (
                "Current edition and game build\n"
                + "\n".join(f"{k}: {self.ctx[k]}" for k in IDENTITIES)
                + "\n\nOpens count confirmed process incarnations. A resume adds an open and continues the same game. "
                "Completion counts each game once: retirement or all scenario goals, explicitly confirmed. "
                "Grey does not claim crash-free coverage. Green requires a completion with no unresolved failures. "
                "A later success leaves a past crash amber until the failed checkpoint is explicitly tested. "
                "A closed issue supplies no gameplay evidence. Verification remains separate.\n\n"
                "Migration: exact retained process sessions supply opens; map issue records supply failures with unknown scenario/phase. "
                "Legacy verification records do not invent games or completions. Saves and diagnostic contents stay private. "
                "Issue refresh sends only personal-repository issue numbers.\n\n"
                "Exact current event identities\n\n"
                + timeline_text(rows, detailed=True)
                + f"\n\n{len(older)} retained events on other variants/builds of this archive (excluded from this card).\n\n"
                + timeline_text(older, detailed=True)
            )
        except (OSError, ValueError) as exc:
            history = issues = scope = "History needs inspection: " + str(exc)
        for name, value in (
            ("Play History", history),
            ("Issues & Fixes", issues),
            ("History Scope", scope),
        ):
            box = self.boxes[name]
            render_linked_text(box, value)

    def _dialog(self, title):
        window = tk.Toplevel(self.tabs["Play History"])
        window.title(title)
        window.transient(self.tabs["Play History"].winfo_toplevel())
        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="Records your confirmation only. Unconfirmed checkpoints stay unknown.",
            wraplength=620,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        return window, body

    @staticmethod
    def _choice(body, row, label, values):
        ttk.Label(body, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=5)
        var = tk.StringVar(value=values[0] if values else "")
        box = ttk.Combobox(body, textvariable=var, values=values, state="readonly", width=58)
        box.grid(row=row, column=1, sticky="ew", pady=5)
        return var, box

    def _run(self, window, action):
        try:
            action()
        except (OSError, ValueError) as exc:
            messagebox.showerror("History confirmation", str(exc), parent=window)
            return
        self.reload()
        window.destroy()
        self.changed()

    def checkpoint_dialog(self, attempt_id=None):
        try:
            rows = self.rows()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Play history", str(exc), parent=self.tabs["Play History"])
            return
        opens = {
            f"{e['at']} · {'Open' if e['kind'] == 'process_confirmed' else 'Attempt'} {e['attempt_id'][:12]}": e[
                "attempt_id"
            ]
            for e in rows
            if e["kind"] in ("process_confirmed", "launch_attempted")
        }
        if not opens:
            messagebox.showinfo(
                "Record checkpoints",
                "No launch attempt or confirmed game process for this edition/build is recorded yet.",
                parent=self.tabs["Play History"],
            )
            return
        window, body = self._dialog("Record player-confirmed checkpoints")
        opened, _ = self._choice(body, 1, "Open / attempt", tuple(reversed(opens)))
        if attempt_id:
            opened.set(next((k for k, v in opens.items() if v == attempt_id), opened.get()))
        scenario, scbox = self._choice(body, 2, "Scenario", ("Unknown", *self.record.scenarios))
        kinds = {LABELS[k]: k for k in ("game_started", "game_resumed", *CHECKPOINTS)}
        checkpoint, _ = self._choice(body, 3, "Checkpoint", tuple(kinds))
        game, gamebox = self._choice(body, 4, "Game", ("Unknown", "Start a new game"))
        games = {}

        def scope_changed(_=None):
            games.clear()
            games.update(
                {
                    f"{e['at']} · game {e['game_id'][:12]}": e["game_id"]
                    for e in rows
                    if e["kind"] == "game_started" and e["scenario"] == scenario.get()
                }
            )
            gamebox.configure(values=("Unknown", "Start a new game", *games))
            game.set("Unknown")

        scbox.bind("<<ComboboxSelected>>", scope_changed)
        saves = tuple(
            dict.fromkeys(e["save_id"] for e in rows if e["kind"] == "save_made" and e["save_id"])
        )
        save, savebox = self._choice(body, 5, "Exact save SHA-256", ("Unknown", *saves))

        def choose_save():
            path = filedialog.askopenfilename(
                parent=window, title="Choose the exact save (read only)"
            )
            if path:
                try:
                    value = save_digest(path)
                    savebox.configure(values=("Unknown", *saves, value))
                    save.set(value)
                except (OSError, ValueError) as exc:
                    messagebox.showerror("Save identity", str(exc), parent=window)

        ttk.Button(body, text="Choose save for identity…", command=choose_save).grid(
            row=6, column=1, sticky="w"
        )
        failures = {}
        for e in rows:
            if e["kind"] in FAILURES:
                failures[e["incident_id"]] = e
        choices = {
            f"{e['at']} · {LABELS[e['kind']]} · {key[:12]}": e for key, e in failures.items()
        }
        incident, _ = self._choice(body, 7, "Failure attribution", ("New failure", *choices))
        failed, _ = self._choice(
            body, 8, "Failed checkpoint", ("Unknown", *(LABELS[k] for k in SUCCESS))
        )
        ttk.Label(
            body,
            text="For a retained failure, choose its incident to add scenario/phase attribution. Save identity is a hash; no save bytes or path are stored.",
            wraplength=620,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=10)

        def record():
            ctx = {**self.ctx, "scenario": "" if scenario.get() == "Unknown" else scenario.get()}
            game_id = "new" if game.get() == "Start a new game" else games.get(game.get(), "")
            kind = kinds[checkpoint.get()]
            if (
                kind not in ("quit", "cancelled", "crash_unknown", "resource_stop")
                and not ctx["scenario"]
            ):
                raise HistoryError("Choose the scenario for this checkpoint")
            confirm_checkpoint(
                self.store,
                ctx,
                kind,
                opens[opened.get()],
                game=game_id,
                save_id="" if save.get() == "Unknown" else save.get(),
                failure=choices.get(incident.get()),
                checkpoint=next((k for k in SUCCESS if LABELS[k] == failed.get()), ""),
            )

        ttk.Button(
            body, text="Record confirmation", command=lambda: self._run(window, record)
        ).grid(row=10, column=1, sticky="e")

    def link_dialog(self):
        window, body = self._dialog("Link a personal-repository issue")
        scenario, _ = self._choice(body, 1, "Exact scenario", ("Unknown", *self.record.scenarios))
        scope, _ = self._choice(
            body, 2, "Applicability", ("Exact scenario / edition", "Related original-map context")
        )
        ttk.Label(body, text="Issue URL").grid(row=3, column=0, sticky="w")
        url = tk.StringVar()
        ttk.Entry(body, textvariable=url, width=64).grid(row=3, column=1, pady=8)

        def record():
            related = scope.get().startswith("Related")
            if not related and scenario.get() == "Unknown":
                raise HistoryError("Exact linkage needs a known scenario")
            self.store.link(
                {**self.ctx, "scenario": "" if scenario.get() == "Unknown" else scenario.get()},
                url.get().strip(),
                scope="related" if related else "exact",
            )

        ttk.Button(body, text="Link locally", command=lambda: self._run(window, record)).grid(
            row=4, column=1, sticky="e"
        )

    def fix_dialog(self):
        rows = self._safe_rows()
        if rows is None:
            return
        window, body = self._dialog("Record fix implementation or test")
        scenario, _ = self._choice(
            body, 1, "Exact scenario", ("Choose scenario", *self.record.scenarios)
        )
        kinds = {
            LABELS[k]: k
            for k in ("fix_proposed", "fix_implemented", "fix_test_passed", "fix_test_failed")
        }
        kind, _ = self._choice(body, 2, "Fact", tuple(kinds))
        fixes = {
            f"{e['scenario']} · fix {e['fix_id'][:12]}": e["fix_id"]
            for e in rows
            if e["kind"] in ("fix_proposed", "fix_implemented")
        }
        fix, _ = self._choice(body, 3, "Fix identity", ("New fix", *fixes))
        latest_failures = {}
        for e in rows:
            if e["kind"] in FAILURES:
                latest_failures[e["incident_id"]] = e
        failures = {
            f"{e['scenario'] or 'Unknown scenario'} · {LABELS[e['kind']]} · {e['incident_id'][:12]}": e
            for e in latest_failures.values()
        }
        failure, _ = self._choice(body, 4, "Failure being tested", ("Choose failure", *failures))
        opens = {
            f"{e['at']} · {e['attempt_id'][:12]}": e["attempt_id"]
            for e in rows
            if e["kind"] == "process_confirmed"
        }
        opened, _ = self._choice(body, 5, "Tested open", ("Choose open", *reversed(opens)))
        urls = tuple(
            dict.fromkeys(
                e["issue_url"] for e in rows if e["kind"] == "issue_link" and e["scope"] == "exact"
            )
        )
        issue, _ = self._choice(body, 6, "Applicable issue", ("No linked issue", *urls))
        ttk.Label(
            body,
            text="A pass requires an implemented fix and a recorded success at the failed checkpoint, on a newly confirmed open after implementation, in this exact scope. Attribute failure phase/checkpoint in Record checkpoints. Closing an issue is separate.",
            wraplength=620,
        ).grid(row=7, column=0, columnspan=2, pady=12)

        def record():
            if scenario.get() == "Choose scenario":
                raise HistoryError("Choose the exact scenario")
            ctx = {**self.ctx, "scenario": scenario.get()}
            fix_id = digest(os.urandom(32).hex()) if fix.get() == "New fix" else fixes[fix.get()]
            if fix.get() != "New fix" and not any(
                e["fix_id"] == fix_id and same_scope(e, ctx) for e in rows
            ):
                raise HistoryError("Choose a fix from this scenario")
            fields = dict(fix_id=fix_id)
            if issue.get() != "No linked issue":
                fields["issue_url"] = issue.get()
            if kinds[kind.get()] in ("fix_test_passed", "fix_test_failed"):
                target = failures.get(failure.get())
                if target is None or not target["checkpoint"] or opened.get() not in opens:
                    raise HistoryError(
                        "Choose an attributed failure with a known failed checkpoint and a confirmed test open"
                    )
                fields.update(
                    target_id=target["event_id"],
                    checkpoint=target["checkpoint"],
                    attempt_id=opens[opened.get()],
                )
            self.store.append(self.store.event(ctx, kinds[kind.get()], **fields))

        ttk.Button(body, text="Record fact", command=lambda: self._run(window, record)).grid(
            row=8, column=1, sticky="e"
        )

    def refresh_issues(self):
        rows = self._safe_rows()
        if rows is None:
            return

        def finished(_):
            self.reload()
            self.changed()

        if self.submit:
            self.submit(
                "Refreshing linked public issue states…",
                lambda: self.store.refresh_issues(rows),
                finished,
            )
        else:
            # Standalone previews never connect to GitHub.
            messagebox.showinfo(
                "Issue states",
                "Issue refresh is available in the launcher.",
                parent=self.tabs["Issues & Fixes"],
            )

    def open_issue(self):
        rows = self._safe_rows()
        if rows is None:
            return
        links = issue_readback(rows)
        if not links:
            return
        window, body = self._dialog("Open linked issue")
        urls = {
            f"#{issue_number(e['url'])} · {e['scenario'] or 'Related context'}": e["url"]
            for e in links
        }
        selected, _ = self._choice(body, 1, "Issue", tuple(urls))
        ttk.Button(
            body,
            text="Open in browser",
            command=lambda: (webbrowser.open(urls[selected.get()]), window.destroy()),
        ).grid(row=2, column=1, sticky="e")
