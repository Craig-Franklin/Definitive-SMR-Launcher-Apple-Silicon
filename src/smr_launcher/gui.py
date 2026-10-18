"""Mac map gallery built from native Tk controls and local, user-owned map art."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from typing import Callable, Optional
from datetime import datetime
import getpass
import math
import time
import re
import subprocess
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .gallery import gallery_snapshot, filter_snapshot
from .ui_work import Activities, LatestValue, Mailbox, DownloadEvents
from .crash_monitor import CrashMonitor
from .play_history import (
    PlayHistoryStore,
    context as history_context,
    summarize,
    digest,
    IDENTITIES,
)
from .history_ui import history_card, gallery_columns, HistoryPanel
from .text_links import render_linked_text
from .activity import ActivityLog
from .map_updates import find_map_updates
from .ratings import fetch_rating, read_cached_rating
from .language import (
    LANGUAGES,
    LanguagePreferences,
    set_language,
    tr,
    translate_briefing,
    open_translation_settings,
)
from .voices import list_voices, speak, open_voice_settings
from .verification import CHECKS, VerificationSummary
from .activation import ORIGINAL
from .application import LauncherApplication, MapRecord
from .collection import (
    RemoteMap,
    download_all_maps,
    fetch_catalogue,
    read_cached_catalogue,
    write_cached_catalogue,
)
from .map_metadata import safe_source_url
from .community import fetch_community_index, read_cached_community_index
from . import APP_VERSION
from .updates import (
    Release,
    UpdatePreferences,
    current_app_bundle,
    download_release,
    fetch_latest_release,
    pending_install,
    pending_install_manual,
    prepare_install,
    stage_release,
    start_install_helper,
    trusted_team_from_bundle,
)


class LaunchCancelledBeforeDispatch(ValueError):
    """No launch child was attempted; profile work may already have completed."""


def _display_name(name: str) -> str:
    return re.sub(r"\bv(\d+) (\d{2})\b", r"v\1.\2", name.replace("_", " "))


MAC_TEST_LABELS = dict(
    scenario_loaded="Fresh scenario loaded successfully",
    gameplay="Track, stations and trains worked during gameplay",
    autosave_written="An autosave was written",
    manual_save_written="A manual save was written",
    manual_save_reloaded="Quit, reopened the game and loaded the manual save",
    continued_after_reload="Continued playing successfully after reloading",
)


def _checks_text(report):
    if report is None:
        return "No current static report. Choose Run Import Checks. Gameplay remains unverified until a Mac play test is recorded."
    findings = report["warnings"]
    xml_scan = report.get("xml_scan", {})
    models = report.get("model_headers", {})
    xml_status = {True: "complete", False: "incomplete"}.get(xml_scan.get("complete"), "unknown")
    model_status = {True: "complete", False: "incomplete"}.get(models.get("complete"), "unknown")
    coverage = (
        f"Loose XML scan coverage: {xml_status}.\n"
        "Complete means all discovered loose XML examined successfully within scan limits.\n"
    )
    if xml_scan:
        coverage += f"XML files successfully examined: {xml_scan['files_examined']} / {xml_scan['files_discovered']}\n"
        if xml_scan.get("incomplete_reasons"):
            coverage += "XML scan gaps: " + ", ".join(xml_scan["incomplete_reasons"]) + "\n"
    coverage += f"Model header coverage: {model_status} (loose headers only).\n"
    coverage += (
        "Native dependency resolution: unknown; packed assets and resource precedence are unverified.\n"
        "Game compatibility and gameplay remain unverified.\n"
    )
    if report.get("input_scope"):
        coverage += "Input scope: " + report["input_scope"] + "\n"
    return (
        f"Checked: {report['checked_at']}\nXML files attempted (includes refused or unparsed files): {report['xml_files_checked']}\n"
        + coverage
        + "\n"
        + (
            "Potential problems to inspect:\n\n" + "\n\n".join(findings)
            if findings
            else "No findings reported by the implemented static checks; see coverage above."
        )
        + "\n\n"
        + report["limitations"]
    )


class TranslatedChoice(tk.StringVar):
    """Display translated choices while retaining stable filtering keys."""

    def __init__(self, *, value, choices):
        self.choices = tuple(choices)
        super().__init__(value=tr(value))

    def get(self):
        displayed = super().get()
        return next((key for key in self.choices if tr(key) == displayed), displayed)

    def set(self, value):
        super().set(tr(value))


class LauncherWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Definitive SMR Launcher — Apple Silicon")
        self.root.geometry("1120x720")
        self.root.minsize(900, 590)
        self.app: Optional[LauncherApplication] = None
        self.crash_monitor = None
        self.crash_status = tk.StringVar(
            value="Crash monitoring is unavailable until the game is found."
        )
        self.busy = False
        self.closed = False
        self._search_refresh_after_id = None
        self._search_refresh_generation = 0
        self.results: Queue[tuple[bool, object, Optional[Callable[[object], None]]]] = Queue()
        self.download_events = DownloadEvents()
        self.background_results = Mailbox()
        self._startup_generation = 0
        self._collection_generation = 0
        self._gallery_generation = 0
        self._gallery_after_id = None
        self._gallery_snapshot = ()
        self._gallery_app = None
        self.initializing = True
        self.collection_refreshing = False
        self.collection_saved = False
        self.collection_notice = ""
        self.download_states: dict[str, tuple[str, int, int, str]] = {}
        self.bulk_cancel = Event()
        self.bulk_active = False
        self.close_when_idle = False
        self.view = "maps"
        self.selected_profile: Optional[str] = None
        self.remote_records: tuple[RemoteMap, ...] = ()
        self.remote_rows: dict[str, RemoteMap] = {}
        self.selected_remote: Optional[RemoteMap] = None
        self.selected_remotes: tuple[RemoteMap, ...] = ()
        self.collection_loaded = False
        self.update_library = (
            Path.home() / "Library/Application Support/Definitive SMR Launcher Apple Silicon"
        )
        self.activity = ActivityLog(self.update_library)
        self.language_preferences = LanguagePreferences(self.update_library)
        self.voices = ()
        self.community_index = {}
        self.community_notice = ""
        self.imported_names = set()
        self.update_preferences = UpdatePreferences(self.update_library)
        automatic = False
        initial_update_status = "No update check yet."
        self.auto_updates = tk.BooleanVar(value=automatic)
        self.update_status = tk.StringVar(value=initial_update_status)
        self.available_update: Optional[Release] = None
        self.pending_update: Optional[Path] = None
        self.pending_update_manual = False
        self.update_request_manual = False
        self.search = tk.StringVar()
        self.sort_order = TranslatedChoice(
            value="Name A–Z",
            choices=("Name A–Z", "Created newest", "Created oldest", "Updated newest", "Author"),
        )
        self.map_filter = TranslatedChoice(
            value="All maps",
            choices=(
                "All maps",
                "Single player",
                "Multiplayer",
                "Verified",
                "Not verified",
                "Known issue",
            ),
        )
        self.metadata_cache = {}
        self.speech_process = None
        self.status = tk.StringVar(value="Checking the Steam Mac game…")
        self.images: dict[str, tk.PhotoImage] = {}
        self.columns = 0
        self.cards = {}
        self._maps_scroll = 0.0
        self._activities = Activities()
        self._activity_widgets = []
        self._gallery_progress = LatestValue()

        self.background = self.root.cget("background")
        dark = sum(self.root.winfo_rgb(self.background)) < 3 * 32768
        self.surface = "#252527" if dark else "#ffffff"
        self.text = "#f3f3f4" if dark else "#202124"
        self.secondary = "#b5b5b8" if dark else "#64666a"
        self.border = "#454549" if dark else "#d7d8da"
        self.accent = "#3b91e8" if dark else "#1265a8"

        # Independently authored native shell; no discovery or history IO here.
        self._attach_activity(self.root)
        self._startup_activity = self._activity_begin("Opening your library…", priority=10)
        self.loading_shell = ttk.Frame(self.root, padding=48)
        self.loading_shell.pack(fill="both", expand=True)
        ttk.Label(
            self.loading_shell, text="Definitive SMR", font=("Helvetica Neue", 26, "bold")
        ).pack(anchor="w")
        ttk.Label(self.loading_shell, text="Opening your library…", padding=(0, 20)).pack(
            anchor="w"
        )
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.createcommand("::tk::mac::Quit", self._close)
        self.root.after(10, self._poll)
        self.root.after(0, self._begin_startup)

    def _attach_activity(self, parent):
        """Keep feedback visible in the main window and the active dialogs."""
        if not hasattr(self, "_activity_widgets"):
            self._activity_widgets = []
        if not hasattr(self, "_activities"):
            self._activities = Activities()
        frame = ttk.Frame(parent, padding=(22, 8))
        frame.pack(side="bottom", fill="x")
        frame.columnconfigure(0, weight=1)
        label = ttk.Label(frame, text="Ready", wraplength=700)
        label._dynamic_text = True
        label.grid(row=0, column=0, sticky="ew")
        frame.bind(
            "<Configure>", lambda event: label.configure(wraplength=max(160, event.width - 200))
        )
        bar = ttk.Progressbar(frame, length=130, mode="indeterminate")
        bar._activity_mode = None
        self._activity_widgets.append((frame, label, bar))
        self._activity_refresh()

    def _activity_begin(self, label, priority=0):
        if not hasattr(self, "_activities"):
            self._activities = Activities()
        token = self._activities.begin(label, priority)
        self._activity_refresh()
        return token

    def _settings_control(self, widget, ready="normal"):
        if not hasattr(self, "_settings_widgets"):
            self._settings_widgets = []
        self._settings_widgets.append((widget, ready))
        widget.configure(state="disabled" if self.busy else ready)
        return widget

    def _loading_panel(self, parent, title):
        """Fill otherwise empty content with feedback where the user is looking."""
        frame = ttk.Frame(parent, padding=28)
        frame.place(relx=0.5, rely=0.35, anchor="center")
        heading = ttk.Label(
            frame, text=tr(title), font=("Helvetica Neue", 18, "bold"), wraplength=420
        )
        heading.pack(pady=(0, 10))
        detail = ttk.Label(frame, text=tr("Please wait…"), wraplength=420)
        detail.pack(pady=(0, 16))
        bar = ttk.Progressbar(frame, length=260, mode="indeterminate")
        bar.pack()
        bar.start(20)
        return frame, heading, detail, bar

    def _loading_panel_update(self, panel, message, completed=None, total=None, *, error=False):
        if panel is None or not panel[0].winfo_exists():
            return
        frame, heading, detail, bar = panel
        heading.configure(text=tr(message))
        detail.configure(
            text=tr("{completed} of {total}", completed=completed, total=total)
            if total is not None
            else tr("Please wait…")
        )
        if error:
            bar.stop()
            bar.pack_forget()
            detail.configure(text=tr("See the status message for details. You can try again."))
        elif total:
            bar.stop()
            bar.configure(mode="determinate", maximum=total, value=completed)
            bar.pack()
        else:
            bar.configure(mode="indeterminate")
            bar.pack()
            bar.start(20)

    def _loading_panel_end(self, panel):
        if panel is not None and panel[0].winfo_exists():
            panel[3].stop()
            panel[0].destroy()

    def _activity_update(self, token, label, completed=None, total=None):
        if hasattr(self, "_activities"):
            self._activities.update(token, label, completed, total)
            self._activity_refresh()

    def _activity_end(self, token):
        if hasattr(self, "_activities"):
            self._activities.end(token)
            self._activity_refresh()

    def _activity_refresh(self):
        if self.closed:
            return
        display = self._activities.display()
        widgets = []
        for frame, label, bar in getattr(self, "_activity_widgets", ()):
            if not frame.winfo_exists():
                continue
            widgets.append((frame, label, bar))
            if display is None:
                label.configure(text=tr("Ready"))
                bar.stop()
                bar.grid_remove()
                bar._activity_mode = None
                continue
            text, completed, total, count = display
            if total is not None:
                text = tr(
                    "{operation} {completed} of {total}",
                    operation=tr(text),
                    completed=completed,
                    total=total,
                )
            if count > 1:
                text = tr("{operation} · {count} other operations", operation=text, count=count - 1)
            label.configure(text=tr(text))
            mode = "determinate" if total else "indeterminate"
            if bar._activity_mode != mode:
                bar.stop()
                bar.configure(mode=mode)
                bar.grid(row=0, column=1, padx=(12, 0), sticky="e")
                if mode == "indeterminate":
                    bar.start(20)
                bar._activity_mode = mode
            if mode == "determinate":
                bar.configure(maximum=total, value=completed)
        self._activity_widgets = widgets

    def _background(self, operation, finished, *, dispose=None, label="Loading…"):
        token = self._activity_begin(label) if label else None

        def delivered(ok, value):
            try:
                finished(ok, value)
            finally:
                self._activity_end(token)

        mailbox = self.background_results

        def worker():
            try:
                result = (True, operation(), delivered)
            except Exception as exc:
                result = (False, exc, delivered)
            mailbox.put(
                result, (lambda item: dispose(item[1]) if item[0] else None) if dispose else None
            )

        try:
            Thread(target=worker, daemon=True).start()
        except Exception as exc:
            # Submission is on Tk's thread: settle caller-owned state just as
            # a failed worker would, including regional errors and retry flags.
            delivered(False, exc)

    def _background_latest(self, lane, operation, finished):
        # At most one worker and one replacement request per maintenance lane.
        if not hasattr(self, "_maintenance_running"):
            self._maintenance_running = set()
            self._maintenance_pending = {}
        if lane in self._maintenance_running:
            self._maintenance_pending[lane] = (operation, finished)
            return
        self._maintenance_running.add(lane)

        def ready(ok, value):
            self._maintenance_running.discard(lane)
            try:
                finished(ok, value)
            finally:
                pending = self._maintenance_pending.pop(lane, None)
                if pending and not self.closed:
                    self._background_latest(lane, *pending)

        labels = {
            "collection-cache": "Saving the collection…",
            "collection-sources": "Checking imported map sources…",
            "collection-community": "Loading community information…",
            "activity-history": "Loading activity history…",
            "crash-reconcile": "Checking pending crash reports…",
        }
        self._background(operation, ready, label=labels.get(lane, "Loading…"))

    @staticmethod
    def _dispose_bootstrap(result):
        monitor = result.get("monitor")
        if monitor is not None:
            monitor.close()

    def _bootstrap(self, discover):
        # Only plain data/services cross to Tk. Preferences and history IO run here.
        data = dict(
            app=None,
            monitor=None,
            discovery_error="",
            monitor_error=False,
            pending=None,
            pending_manual=False,
            automatic=False,
            update_status="No update check yet.",
        )
        try:
            self.language_preferences.load()
        except Exception:
            pass
        data["language"] = self.language_preferences.language
        data["imported_names"] = set()
        data["community"] = read_cached_community_index(
            self.update_library / "community-index.json"
        )
        try:
            data["automatic"] = self.update_preferences.automatic()
            if trusted_team_from_bundle():
                data["pending"] = pending_install(
                    self.update_library, current_app_bundle(), APP_VERSION
                )
                if data["pending"]:
                    data["pending_manual"] = pending_install_manual(data["pending"])
                    data["update_status"] = (
                        "A staged update is ready; it will be rechecked when you quit to install it."
                    )
        except Exception as exc:
            data["update_status"] = "Update settings need inspection: " + str(exc)
        try:
            data["app"] = discover()
            data["enrolled"] = data["app"].profiles.state_file.exists()
            data["imported_names"] = {r.archive_filename for r in data["app"].catalogue()}
        except Exception as exc:
            data["discovery_error"] = str(exc)
        if data["app"] is not None:
            try:
                data["monitor"] = CrashMonitor(data["app"], APP_VERSION)
                data["monitor"].start()
            except Exception:
                self._dispose_bootstrap(data)
                data["monitor"] = None
                data["monitor_error"] = True
        return data

    def _begin_startup(self):
        if self.closed:
            return
        generation = self._startup_generation
        self._background(
            lambda: self._bootstrap(LauncherApplication.discover),
            lambda ok, value: self._startup_ready(generation, ok, value),
            dispose=self._dispose_bootstrap,
            label=None,
        )

    def _startup_ready(self, generation, successful, data):
        if self.closed or generation != self._startup_generation:
            if successful:
                self._dispose_bootstrap(data)
            return
        self.initializing = False
        self._activity_end(getattr(self, "_startup_activity", None))
        if successful:
            self.app = data["app"]
            self.crash_monitor = data["monitor"]
            self.community_index = data["community"]
            self.imported_names = data["imported_names"]
            set_language(data["language"])
            self.sort_order.set("Name A–Z")
            self.map_filter.set("All maps")
            self.auto_updates.set(data["automatic"])
            self.pending_update = data["pending"]
            self.pending_update_manual = data["pending_manual"]
            self.update_status.set(data["update_status"])
            if data["monitor_error"]:
                self.crash_status.set("Crash monitoring needs inspection; you can still play.")
            self.status.set(
                "Steam Mac game found. Select a map or play Original Game."
                if self.app
                else "Steam Mac game unavailable: " + data["discovery_error"]
            )
        else:
            self.status.set("Startup needs inspection: " + str(data))
        self.loading_shell.destroy()
        self._menu()
        self._layout()
        self._show(self._initial_view())
        if self.auto_updates.get() and self.pending_update is None:
            self.root.after(1500, lambda: None if self.closed else self.check_for_updates())

    def _menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label=tr("Import Map…"), command=self.import_map, accelerator="⌘O")
        file_menu.add_command(
            label=tr("Choose Steam Library Folder…"), command=self.choose_steam_library
        )
        file_menu.add_separator()
        file_menu.add_command(label=tr("Quit"), command=self._close, accelerator="⌘Q")
        menu.add_cascade(label=tr("File"), menu=file_menu)
        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(
            label="Installation & Help",
            command=lambda: webbrowser.open(
                "https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/blob/main/MACOS_README.md"
            ),
        )
        help_menu.add_command(
            label="Windows Feature Comparison",
            command=lambda: webbrowser.open(
                "https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/blob/main/docs/FEATURE_PARITY.md"
            ),
        )
        help_menu.add_command(label="Crash Reports…", command=self.crash_reports)
        menu.add_cascade(label=tr("Help"), menu=help_menu)
        self.root.configure(menu=menu)
        self.root.bind("<Command-o>", lambda _: self.import_map())
        self.root.bind("<Command-f>", lambda _: self.search_entry.focus_set())
        self.root.bind("<Command-q>", lambda _: self._close())

    def _close(self) -> None:
        self._cancel_search_refresh()
        self._cancel_gallery()
        if self.busy or self.bulk_active:
            self.close_when_idle = True
            if self.bulk_active:
                self.bulk_cancel.set()
            self.status.set("Finishing the current safe operation before closing…")
            self._controls()
            return
        if self.pending_update is not None and (
            self.pending_update_manual or self.auto_updates.get()
        ):

            def failed(exc):
                self.close_when_idle = False
                if not messagebox.askyesno(
                    "Update not installed",
                    f"The update could not start: {exc}\n\nQuit without installing it? "
                    "The staged update will remain available for inspection or retry.",
                    parent=self.root,
                ):
                    return
                self._finish_close()

            self._submit(
                "Verifying update before installation…",
                lambda: start_install_helper(self.pending_update, self.update_library),
                lambda _: self._finish_close(),
                failed=failed,
            )
            return
        self._finish_close()

    def _finish_close(self):
        if getattr(self, "crash_monitor", None) is not None:
            self.crash_monitor.close()
        self.closed = True
        self._startup_generation = getattr(self, "_startup_generation", 0) + 1
        self._collection_generation = getattr(self, "_collection_generation", 0) + 1
        if hasattr(self, "background_results"):
            self.background_results.close()
        if hasattr(self, "download_events") and hasattr(self.download_events, "close"):
            self.download_events.close()
        self.root.destroy()
        self._stop_speech()

    def _layout(self) -> None:
        header = ttk.Frame(self.root, padding=(22, 14, 22, 12))
        header.pack(fill="x")
        ttk.Label(header, text="Definitive SMR", font=("Helvetica Neue", 20, "bold")).pack(
            side="left"
        )
        ttk.Label(header, text="Apple Silicon", foreground=self.secondary).pack(
            side="left", padx=(10, 0)
        )
        self.search_entry = ttk.Entry(header, textvariable=self.search, width=26)
        self.search_entry.pack(side="right")
        self.search_label = ttk.Label(header, text="Search maps", foreground=self.secondary)
        self.search_label.pack(side="right", padx=(0, 8))
        self.search.trace_add("write", lambda *_: self._search_changed())

        ttk.Separator(self.root).pack(fill="x")
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.sidebar = ttk.Frame(body, width=218, padding=(14, 18, 12, 16))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.nav_buttons: dict[str, ttk.Button] = {}
        for key, label in (
            ("maps", "Map Library"),
            ("collection", "Collection"),
            ("original", "Original Game"),
            ("preferences", "Language & Voice"),
            ("activity", "Activity"),
            ("setup", "Setup"),
        ):
            button = ttk.Button(
                self.sidebar, text=label, command=lambda destination=key: self._show(destination)
            )
            button.pack(fill="x", pady=(0, 7))
            self.nav_buttons[key] = button
        ttk.Separator(self.sidebar).pack(fill="x", pady=(12, 14))
        ttk.Label(self.sidebar, text="Steam Mac edition", font=("Helvetica Neue", 12, "bold")).pack(
            anchor="w"
        )
        self.steam_label = ttk.Label(
            self.sidebar, text="Checking…", wraplength=185, foreground=self.secondary
        )
        self.steam_label.pack(anchor="w", pady=(5, 12))

        self.sidebar_separator = ttk.Separator(body, orient="vertical")
        self.sidebar_separator.pack(side="left", fill="y")
        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)
        ttk.Separator(self.root).pack(fill="x")
        ttk.Label(
            self.root,
            textvariable=self.status,
            padding=(22, 8),
            foreground=self.secondary,
            wraplength=900,
        ).pack(fill="x")

    def _clear_content(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self.columns = 0

    def _initial_view(self) -> str:
        if self.app is None or not self.app.profiles.state_file.exists():
            return "welcome"
        return "maps"

    def _welcome_view(self) -> None:
        frame = ttk.Frame(self.content, padding=(48, 36))
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="Welcome to Definitive SMR", font=("Helvetica Neue", 26, "bold")
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="Discover new maps for Sid Meier’s Railroads!",
            foreground=self.secondary,
            font=("Helvetica Neue", 14),
            wraplength=660,
        ).pack(anchor="w", pady=(10, 28))
        if self.app is None:
            ttk.Label(frame, text="Let’s find your game", font=("Helvetica Neue", 17, "bold")).pack(
                anchor="w"
            )
            ttk.Label(
                frame,
                text="Install Sid Meier’s Railroads! in Steam, open it once, then quit the game and check again.",
                wraplength=620,
            ).pack(anchor="w", pady=(10, 20))
            self.welcome_retry_button = ttk.Button(
                frame, text="Check Again", command=self._retry_discovery
            )
            self.welcome_retry_button.pack(anchor="w")
            ttk.Button(
                frame, text="Choose Steam Library Folder…", command=self.choose_steam_library
            ).pack(anchor="w", pady=(12, 0))
        else:
            ttk.Label(
                frame,
                text="Your Steam game is ready to connect",
                font=("Helvetica Neue", 17, "bold"),
            ).pack(anchor="w")
            ttk.Label(
                frame,
                text="Get Started checks your game and preserves its original profile and existing saves. Each custom map will have its own saves.",
                wraplength=620,
            ).pack(anchor="w", pady=(10, 12))
            ttk.Label(
                frame, text="Quit Railroads before continuing.", foreground=self.secondary
            ).pack(anchor="w", pady=(0, 20))
            self.setup_button = ttk.Button(frame, text="Get Started", command=self.setup)
            self.setup_button.pack(anchor="w")
        self._controls()

    def _retry_discovery(self) -> None:
        self._submit(
            "Looking for the Steam Mac game…", LauncherApplication.discover, self._library_chosen
        )

    def _show(self, view: str) -> None:
        if self.view == "maps" and hasattr(self, "canvas") and self.canvas.winfo_exists():
            position = self.canvas.yview()
            if position:
                self._maps_scroll = position[0]
        self._cancel_search_refresh()
        self._cancel_gallery()
        self.view = view
        self._clear_content()
        if view == "welcome":
            self.sidebar.pack_forget()
            self.sidebar_separator.pack_forget()
            self.search_entry.pack_forget()
            self.search_label.pack_forget()
        else:
            self.sidebar.pack(side="left", fill="y", before=self.content)
            self.sidebar_separator.pack(side="left", fill="y", before=self.content)
            self.search_entry.pack(side="right")
            self.search_label.pack(side="right", padx=(0, 8))
        for key, button in self.nav_buttons.items():
            button.state(["disabled"] if key == view else ["!disabled"])
        self.steam_label.configure(text="Installed" if self.app else "Game not found")
        if view == "welcome":
            self._welcome_view()
        elif view == "collection":
            self._collection_view()
        elif view == "activity":
            self._activity_view()
        elif view == "preferences":
            self._preferences_view()
        elif view == "setup":
            self._setup_view()
        elif self.app is None:
            self._missing_game()
        elif view == "maps":
            self._maps_view()
        elif view == "original":
            self._original_view()
        else:
            self._setup_view()

        self._localize_widgets(self.root)

    def _missing_game(self) -> None:
        frame = ttk.Frame(self.content, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Steam Mac game required", font=("Helvetica Neue", 21, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="Install Sid Meier’s Railroads! from Steam on its default branch and launch it once. If Steam uses another drive, choose that drive’s steamapps folder.",
            wraplength=550,
        ).pack(anchor="w", pady=(12, 18))
        ttk.Button(
            frame, text="Choose Steam Library Folder…", command=self.choose_steam_library
        ).pack(anchor="w")
        ttk.Label(
            frame, text="Choose a steamapps folder, not Steam.app.", foreground=self.secondary
        ).pack(anchor="w", pady=(8, 0))

    def _maps_view(self) -> None:
        self._gallery_snapshot = ()
        self._maps_dirty = True
        self.cards = {}
        self._gallery_restore_scroll = getattr(self, "_maps_scroll", 0.0)
        top = ttk.Frame(self.content, padding=(24, 20, 24, 12))
        top.pack(fill="x")
        ttk.Label(top, text="Map Library", font=("Helvetica Neue", 21, "bold")).pack(side="left")
        self.gallery_count = ttk.Label(top, text="Loading maps…", foreground=self.secondary)
        self.gallery_count.pack(side="left", padx=(12, 0))
        self.gallery_progress = ttk.Progressbar(top, length=90, mode="indeterminate")
        self.gallery_progress.pack(side="left")
        self.play_button = ttk.Button(top, text="Play Selected", command=self.play_selected)
        self.play_button.pack(side="right")
        self.import_button = ttk.Button(top, text="Import Map…", command=self.import_map)
        self.import_button.pack(side="right", padx=(0, 8))

        options = ttk.Frame(self.content, padding=(24, 0, 24, 10))
        options.pack(fill="x")
        ttk.Label(options, text="Show").pack(side="left")
        filters = ttk.Combobox(
            options,
            textvariable=self.map_filter,
            state="readonly",
            width=18,
            values=tuple(tr(key) for key in self.map_filter.choices),
        )
        filters.pack(side="left", padx=(6, 16))
        filters.bind("<<ComboboxSelected>>", lambda _: self._render_gallery())
        ttk.Label(options, text="Sort").pack(side="left")
        order = ttk.Combobox(
            options,
            textvariable=self.sort_order,
            state="readonly",
            width=19,
            values=tuple(tr(key) for key in self.sort_order.choices),
        )
        order.pack(side="left", padx=6)
        order.bind("<<ComboboxSelected>>", lambda _: self._render_gallery())
        self.details_button = ttk.Button(
            options, text="Map Details…", command=self._selected_details
        )
        self.details_button.pack(side="right")

        self.detail = tk.StringVar(value="Choose a map to see its scenario and source details.")
        ttk.Label(
            self.content,
            textvariable=self.detail,
            padding=(24, 0, 24, 12),
            foreground=self.secondary,
            wraplength=680,
        ).pack(fill="x")
        holder = ttk.Frame(self.content)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, background=self.background, highlightthickness=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=self._gallery_yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind(
            "<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind("<Configure>", self._resize_gallery)
        self.canvas.bind("<MouseWheel>", self._scroll_gallery)
        self.grid_frame.bind("<MouseWheel>", self._scroll_gallery)
        self._gallery_panel = self._loading_panel(self.canvas, "Loading your maps…")
        self._render_gallery()
        self._controls()

    def _records(self) -> tuple[MapRecord, ...]:
        if self.app is None:
            return ()
        try:
            return self.app.catalogue()
        except Exception as exc:
            self.status.set("Local map catalogue needs inspection: " + str(exc))
            return ()

    def _filtered(self) -> list[MapRecord]:
        query = self.search.get().strip().casefold()
        records = []
        for item in self._records():
            metadata = self._metadata(item)
            if (
                query
                not in " ".join(
                    (item.name, metadata.name or "", metadata.author or "", metadata.description)
                ).casefold()
            ):
                continue
            wanted = self.map_filter.get()
            if (
                wanted in ("Single player", "Multiplayer")
                and wanted.replace(" ", "").casefold()
                not in (metadata.map_type or "").replace(" ", "").casefold()
            ):
                continue
            if (
                wanted in ("Verified", "Not verified", "Known issue")
                and self._verification_text(item) != wanted
            ):
                continue
            records.append(item)
        order = self.sort_order.get()
        if order in ("Created newest", "Created oldest", "Updated newest"):
            field = "updated" if order == "Updated newest" else "created"
            known = [r for r in records if getattr(self._metadata(r), field)]
            unknown = [r for r in records if not getattr(self._metadata(r), field)]
            known.sort(
                key=lambda r: (getattr(self._metadata(r), field), r.name.casefold()),
                reverse=order != "Created oldest",
            )
            return known + sorted(unknown, key=lambda r: r.name.casefold())
        return sorted(
            records,
            key=lambda r: (
                (self._metadata(r).author or "~").casefold() if order == "Author" else "",
                r.name.casefold(),
            ),
        )

    def _metadata(self, record: MapRecord):
        # Details always read secure current bytes, including missing-to-present.
        return self.app.map_metadata(record)

    def _search_changed(self) -> None:
        if self.closed:
            return
        self._cancel_search_refresh()
        self._cancel_gallery()
        if self.view == "maps" and self.app is not None:
            self._gallery_activity = self._activity_begin("Filtering maps…", priority=5)
        generation = self._search_refresh_generation
        self._search_refresh_after_id = self.root.after(
            200, lambda: self._run_search_refresh(generation)
        )

    def _cancel_search_refresh(self) -> None:
        self._search_refresh_generation = getattr(self, "_search_refresh_generation", 0) + 1
        after_id = getattr(self, "_search_refresh_after_id", None)
        self._search_refresh_after_id = None
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                # The callback may already have fired or Tk may be closing.
                pass

    def _run_search_refresh(self, generation: int) -> None:
        if generation != self._search_refresh_generation:
            return
        self._search_refresh_after_id = None
        if self.closed:
            return
        if self.view == "maps" and self.app is not None and hasattr(self, "grid_frame"):
            self._render_gallery()
        elif self.view == "collection" and hasattr(self, "collection_list"):
            self._collection_rows()

    def _collection_view(self) -> None:
        top = ttk.Frame(self.content, padding=(24, 20, 24, 12))
        top.pack(fill="x")
        ttk.Label(top, text="Collection", font=("Helvetica Neue", 21, "bold")).pack(side="left")
        self.collection_count = ttk.Label(
            top, text=self._collection_caption(), foreground=self.secondary
        )
        self.collection_count.pack(side="left", padx=(12, 0))
        self.collection_progress = ttk.Progressbar(top, length=90, mode="indeterminate")
        actions = ttk.Frame(self.content, padding=(24, 0, 24, 12))
        actions.pack(fill="x")
        self.download_button = ttk.Button(
            actions, text="Download & Import Selected", command=self._download_selected
        )
        self.download_button.pack(side="left")
        self.bulk_button = ttk.Button(
            actions, text="Download & Import All…", command=self._download_all
        )
        self.bulk_button.pack(side="left", padx=8)
        ttk.Button(actions, text="Refresh", command=self._refresh_collection).pack(side="left")
        ttk.Button(actions, text="Check Map Updates", command=self._check_map_updates).pack(
            side="left", padx=8
        )
        ttk.Button(
            actions,
            text="Collection Source",
            command=lambda: webbrowser.open(
                "https://archive.org/details/sid-meiers-railroads-custom-maps-collection"
            ),
        ).pack(side="right")
        note = (
            "Download & Import All verifies archives in parallel, then prepares each map "
            "in your private library. Use Command-click or Shift-click to select several maps."
        )
        ttk.Label(
            self.content,
            text=note,
            padding=(24, 0, 24, 12),
            foreground=self.secondary,
            wraplength=680,
        ).pack(fill="x")
        self.collection_detail = tk.StringVar(value="Choose a map to see its archive identity.")
        ttk.Label(
            self.content,
            textvariable=self.collection_detail,
            padding=(24, 0, 24, 12),
            foreground=self.secondary,
            wraplength=680,
        ).pack(fill="x")
        frame = ttk.Frame(self.content, padding=(24, 0, 24, 20))
        frame.pack(fill="both", expand=True)
        self.collection_list = ttk.Treeview(
            frame, columns=("map", "progress", "size"), show="headings", selectmode="extended"
        )
        self.collection_list.heading("map", text=tr("Map"))
        self.collection_list.heading("progress", text=tr("Download progress"))
        self.collection_list.heading("size", text=tr("Archive size"))
        self.collection_list.column("map", width=440, stretch=True)
        self.collection_list.column("progress", width=145, stretch=False, anchor="w")
        self.collection_list.column("size", width=110, stretch=False, anchor="e")
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.collection_list.yview)
        self.collection_list.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.collection_list.pack(side="left", fill="both", expand=True)
        self.collection_list.bind("<<TreeviewSelect>>", self._collection_selected)
        self._collection_panel = None
        if not self.collection_loaded or self.collection_refreshing:
            self._collection_feedback(True)
        self._collection_rows()
        self._controls()
        if not self.collection_loaded and not getattr(self, "collection_refreshing", False):
            self._load_saved_collection()

    def _collection_rows(self) -> None:
        if not hasattr(self, "collection_list") or not self.collection_list.winfo_exists():
            return
        position = (
            self.collection_list.yview()[0]
            if hasattr(self.collection_list, "yview") and self.collection_list.yview()
            else 0
        )
        selected_names = {item.name for item in self.selected_remotes}
        for child in self.collection_list.get_children():
            self.collection_list.delete(child)
        self.remote_rows.clear()
        self.remote_row_ids = {}
        query = self.search.get().strip().casefold()
        for index, item in enumerate(self.remote_records):
            if query not in item.name.casefold():
                continue
            key = str(index)
            self.remote_rows[key] = item
            self.remote_row_ids[item.name] = key
            size = f"{item.size / (1024 * 1024):.1f} MB"
            self.collection_list.insert(
                "",
                "end",
                iid=key,
                values=(_display_name(item.name[:-3]), self._download_label(item.name), size),
            )
        keys = [key for key, item in self.remote_rows.items() if item.name in selected_names]
        self.collection_list.selection_set(keys)
        if hasattr(self.collection_list, "yview_moveto"):
            self.collection_list.yview_moveto(position)
        self._collection_selected(None)
        self._controls()

    def _collection_selected(self, _event: tk.Event) -> None:
        selection = self.collection_list.selection()
        self.selected_remotes = tuple(
            self.remote_rows[key] for key in selection if key in self.remote_rows
        )
        self.selected_remote = self.remote_rows.get(selection[0]) if selection else None
        if self.selected_remote is None:
            self.collection_detail.set("Choose a map to see its archive identity.")
        else:
            state = self.download_states.get(self.selected_remote.name)
            detail = f"  ·  {state[3]}" if state and state[3] else ""
            self.collection_detail.set(
                f"{len(selection)} selected · Internet Archive · "
                f"Archive modified: {self.selected_remote.archive_modified or 'Not provided'}\n"
                "Map creation date and author are available in Map Details after import. "
                f"{self._download_label(self.selected_remote.name)}{detail}"
            )
        self._controls()

    def _download_label(self, name: str) -> str:
        state, count, total, _ = self.download_states.get(
            name, ("Imported" if name in self.imported_names else "Ready", 0, 0, "")
        )
        if state == "Downloading" and total:
            return f"Downloading {min(100, count * 100 // total)}%"
        return state

    def _download_event(
        self, record: RemoteMap, state: str, count: int, total: int, detail: str
    ) -> None:
        self.download_events.put((record, state, count, total, detail))

    def _update_download_rows(self) -> None:
        changed = set()
        deadline = time.monotonic() + 0.004
        for _ in range(64):
            if time.monotonic() >= deadline:
                break
            try:
                record, state, count, total, detail = self.download_events.get_nowait()
            except Empty:
                break
            previous = self.download_states.get(record.name)
            if state in ("Imported", "Failed", "Canceled") and (
                not previous or previous[0] != state
            ):
                self._log(record.name, state, detail)
            self.download_states[record.name] = (state, count, total, detail)
            changed.add(record.name)
        visible = (
            self.view == "collection"
            and hasattr(self, "collection_list")
            and self.collection_list.winfo_exists()
        )
        if visible:
            for name in changed:
                key = getattr(self, "remote_row_ids", {}).get(name)
                if key is not None:
                    self.collection_list.set(key, "progress", self._download_label(name))
            if self.selected_remote is not None and self.selected_remote.name in changed:
                self._collection_selected(None)
        if not self.download_events.empty() and not getattr(self, "_progress_after_id", None):
            self._progress_after_id = self.root.after(1, self._continue_download_rows)

    def _continue_download_rows(self):
        self._progress_after_id = None
        if not self.closed:
            self._update_download_rows()

    def _collection_caption(self):
        if not self.collection_loaded:
            return "Internet Archive"
        label = "saved catalogue" if getattr(self, "collection_saved", False) else "maps online"
        return f"{len(self.remote_records)} {label}" + getattr(self, "collection_notice", "")

    def _load_saved_collection(self):
        self.collection_refreshing = True
        self._collection_feedback(True)
        generation = self._collection_generation

        def loaded(ok, saved):
            if self.closed or generation != self._collection_generation:
                return
            self.collection_refreshing = False
            if ok and saved is not None:
                self.collection_saved = True
                self.collection_notice = (
                    " · saved " + saved.fetched_at[:10] + "; Refresh to check online"
                )
                self._collection_loaded(saved.records)
            else:
                self._refresh_collection()

        path = self.update_library / "collection-catalogue.json"
        self._background(
            lambda: read_cached_catalogue(path), loaded, label="Loading saved collection…"
        )

    def _refresh_collection(self) -> None:
        if getattr(self, "collection_refreshing", False) or self.closed:
            return
        self._collection_generation += 1
        generation = self._collection_generation
        app = self.app
        self.collection_refreshing = True
        self._collection_feedback(True)
        self.collection_notice = " · refreshing…"
        self._publish_collection_caption()

        def loaded(ok, records):
            if self.closed or generation != self._collection_generation or self.app is not app:
                return
            self.collection_refreshing = False
            if not ok:
                self.collection_notice = (
                    " · refresh failed; showing saved list"
                    if self.collection_loaded
                    else " · refresh failed"
                )
                self.status.set("Collection refresh unavailable: " + str(records))
                self._publish_collection_caption()
                self._collection_feedback(False, error=True)
                return
            self.collection_saved = False
            self.collection_notice = ""
            self._collection_loaded(records)
            # Publication is independent of cache persistence, provenance and community IO.
            self._collection_maintenance(generation, app, records)

        self._background(fetch_catalogue, loaded, label="Loading collection from Internet Archive…")

    def _publish_collection_caption(self):
        if (
            self.view == "collection"
            and hasattr(self, "collection_count")
            and self.collection_count.winfo_exists()
        ):
            self.collection_count.configure(text=self._collection_caption())

    def _collection_feedback(self, active, *, error=False):
        if (
            self.view != "collection"
            or not hasattr(self, "collection_list")
            or not self.collection_list.winfo_exists()
        ):
            return
        bar = getattr(self, "collection_progress", None)
        if active:
            if bar is not None and bar.winfo_exists():
                bar.pack(side="left")
                bar.start(20)
            if not self.remote_records:
                panel = getattr(self, "_collection_panel", None)
                if panel is None or not panel[0].winfo_exists():
                    self._collection_panel = self._loading_panel(
                        self.collection_list, "Loading the map collection…"
                    )
                else:
                    self._loading_panel_update(panel, "Loading the map collection…")
        else:
            if bar is not None and bar.winfo_exists():
                bar.stop()
                bar.pack_forget()
            if error and not self.remote_records:
                self._loading_panel_update(
                    getattr(self, "_collection_panel", None),
                    "Could not load the collection",
                    error=True,
                )
            else:
                self._loading_panel_end(getattr(self, "_collection_panel", None))
                self._collection_panel = None

    def _collection_maintenance(self, generation, app, records):
        library = self.update_library

        def valid():
            return not self.closed and generation == self._collection_generation and self.app is app

        def cache_done(ok, value):
            if valid() and not ok:
                self.collection_notice += " · saved catalogue could not be updated"
                self.status.set("Collection cache needs inspection: " + str(value))
                self._publish_collection_caption()

        self._background_latest(
            "collection-cache",
            lambda: write_cached_catalogue(library / "collection-catalogue.json", records),
            cache_done,
        )
        if app:

            def matched(ok, value):
                if not valid():
                    return
                if not ok:
                    self.collection_notice += " · local source matching needs inspection"
                    self.status.set("Collection source matching needs inspection: " + str(value))
                else:
                    self.imported_names = value
                    if self.view == "collection":
                        self._collection_rows()
                self._publish_collection_caption()

            def match():
                app.match_collection_sources(records)
                return {r.archive_filename for r in app.catalogue()}

            self._background_latest("collection-sources", match, matched)

        def community_done(ok, value):
            if not valid():
                return
            if ok:
                self.community_index = value
                self.community_notice = ""
            else:
                self.community_notice = (
                    " Community refresh unavailable; using saved community information."
                )
                self.collection_notice += " · community refresh unavailable"
                self.status.set("Community refresh unavailable: " + str(value))
            self._publish_collection_caption()

        self._background_latest(
            "collection-community",
            lambda: fetch_community_index(library / "community-index.json"),
            community_done,
        )

    def _collection_loaded(self, result: object) -> None:
        assert isinstance(result, tuple)
        self.remote_records = result
        self.collection_loaded = True
        if self.view == "collection":
            self.status.set(self._collection_caption() + getattr(self, "community_notice", ""))
            # Reuse existing native controls, selection and scroll position.
            self._collection_rows()
            self._publish_collection_caption()
            self._collection_feedback(False)

    def _download_selected(self) -> None:
        if self.app is None or self.selected_remote is None:
            return
        if len(self.selected_remotes) > 1:
            self._download_all(self.selected_remotes)
            return
        if self.busy or self.closed:
            return
        record = self.selected_remote
        app = self.app
        self.download_events.begin((record,))
        self.download_states[record.name] = ("Queued", 0, record.size, "")

        def operation() -> MapRecord:
            try:
                path = app.download_archive(
                    record,
                    progress=lambda count, total: self._download_event(
                        record, "Downloading", count, total, ""
                    ),
                )
                self._download_event(record, "Archive verified", record.size, record.size, "")
                result = app.import_archive(path, original_filename=record.name, remote=record)
                self._download_event(record, "Imported", record.size, record.size, "")
                return result
            except Exception as exc:
                self._download_event(record, "Failed", 0, record.size, str(exc))
                raise

        self._submit("Downloading and checking " + record.name + "…", operation, self._imported)

    def _download_all(self, selected: Optional[tuple[RemoteMap, ...]] = None) -> None:
        if self.bulk_active:
            self.bulk_cancel.set()
            self.status.set("Stopping downloads after the current network reads…")
            self._controls()
            return
        if self.app is None or self.busy or not self.collection_loaded or not self.remote_records:
            return
        records = selected if selected is not None else self.remote_records
        total = sum(record.size for record in records)
        if not messagebox.askyesno(
            "Download and import all maps",
            f"Download {len(records)} {'selected' if selected else 'collection'} map archives (up to {total / (1024**3):.2f} GiB) "
            "and import them into your private library? Four downloads can run at once; imports are checked "
            "one at a time. This also needs space for extracted map files. Maps will not be activated.",
            parent=self.root,
        ):
            return
        self.download_events.begin(records)
        app = self.app
        self.bulk_cancel.clear()
        self.bulk_active = True
        for record in records:
            self.download_states[record.name] = ("Queued", 0, record.size, "")
        self._collection_rows()

        def operation():
            # Fresh game/build checks precede every network read, off Tk.
            app._stopped()
            return download_all_maps(
                records,
                app.library / "downloads",
                self._download_event,
                self.bulk_cancel,
                downloader=app.download_archive,
                importer=lambda record, path: app.import_archive(
                    path, original_filename=record.name, remote=record
                ),
            )

        def failed(exc):
            for record in records:
                state = self.download_states.get(record.name, ("Queued", 0, record.size, ""))
                if state[0] not in ("Imported", "Failed", "Canceled"):
                    self.download_states[record.name] = ("Failed", state[1], record.size, str(exc))
            self._collection_rows()
            messagebox.showerror("Collection import stopped", str(exc), parent=self.root)

        self._submit(
            f"Checking, downloading and importing {len(records)} maps…",
            operation,
            self._download_all_finished,
            failed=failed,
        )

    def _download_all_finished(self, result: object) -> None:
        completed, failed, canceled = result
        self.bulk_active = False
        self.status.set(
            f"Bulk import finished: {completed} imported, {failed} failed, {canceled} canceled. "
            "Choose a map in Map Library to test it."
        )
        if self.view == "maps":
            self._show("maps")
        self._controls()

    def _resize_gallery(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)
        columns = gallery_columns(event.width)
        if columns != self.columns:
            self.columns = columns
            self._render_gallery()

    def _gallery_yview(self, *args):
        self.canvas.yview(*args)
        self._remember_gallery_scroll()

    def _remember_gallery_scroll(self):
        position = self.canvas.yview()
        if position:
            self._maps_scroll = position[0]
            self._gallery_scroll = position[0]

    def _scroll_gallery(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self._remember_gallery_scroll()

    def _art(self, item) -> Optional[tk.PhotoImage]:
        if not item.art:
            return None
        if item.art_key in self.images:
            return self.images[item.art_key]
        try:
            original = tk.PhotoImage(data=item.art, format="png")
            factor = max(1, math.ceil(max(original.width(), original.height()) / 144))
            photo = original.subsample(factor)
            self.images[item.art_key] = photo
            return photo
        except tk.TclError:
            return None

    def _cancel_gallery(self):
        self._loading_panel_end(getattr(self, "_gallery_panel", None))
        self._gallery_panel = None
        if hasattr(self, "gallery_progress") and self.gallery_progress.winfo_exists():
            self.gallery_progress.stop()
            self.gallery_progress.pack_forget()
        self._activity_end(getattr(self, "_gallery_activity", None))
        self._gallery_activity = None
        self._gallery_generation = getattr(self, "_gallery_generation", 0) + 1
        cancel = getattr(self, "_gallery_cancel", None)
        if cancel is not None:
            cancel.set()
        self._gallery_pending = None
        after_id = getattr(self, "_gallery_after_id", None)
        self._gallery_after_id = None
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass

    def _render_gallery(self) -> None:
        if (
            self.closed
            or self.busy
            or self.view != "maps"
            or self.app is None
            or not hasattr(self, "grid_frame")
            or not self.grid_frame.winfo_exists()
        ):
            return
        self._cancel_gallery()
        generation = self._gallery_generation
        self._gallery_pending = (generation, self.app)
        self._gallery_activity = self._activity_begin("Loading maps…", priority=5)
        if hasattr(self, "gallery_count"):
            self.gallery_count.configure(text=tr("Loading maps…"))
        if hasattr(self, "gallery_progress") and self.gallery_progress.winfo_exists():
            self.gallery_progress.configure(mode="indeterminate")
            self.gallery_progress.pack(side="left")
            self.gallery_progress.start(20)
        if (
            not getattr(self, "cards", {})
            and hasattr(self, "canvas")
            and getattr(self, "_gallery_panel", None) is None
        ):
            self._gallery_panel = self._loading_panel(self.canvas, "Loading your maps…")
        self._loading_panel_update(getattr(self, "_gallery_panel", None), "Loading your maps…")
        self._maps_dirty = False
        self._start_gallery_snapshot()

    def _start_gallery_snapshot(self):
        # One producer per window. A replacement request cancels, then waits for
        # the previous plain-data worker; search cannot spawn an unbounded fleet.
        if getattr(self, "_gallery_loading", False) or not self._gallery_pending:
            return
        generation, app = self._gallery_pending
        self._gallery_pending = None
        cancel = Event()
        self._gallery_cancel = cancel
        self._gallery_loading = True
        if not hasattr(self, "_gallery_progress"):
            self._gallery_progress = LatestValue()
        progress = self._gallery_progress

        def advanced(completed, total):
            progress.put((generation, completed, total))

        def loaded(ok, snapshot):
            self._gallery_loading = False
            if self.closed:
                return
            if (
                generation == self._gallery_generation
                and app is self.app
                and self.view == "maps"
                and not self.busy
            ):
                if ok:
                    self._gallery_snapshot = snapshot
                    self.imported_names = {item.record.archive_filename for item in snapshot}
                    self._gallery_app = app
                    self.gallery_count.configure(text=f"{len(snapshot)} imported")
                    for item in snapshot:
                        if item.notice:
                            self.status.set(item.notice)
                            break
                    self._begin_gallery_batches(generation)
                else:
                    self._activity_end(self._gallery_activity)
                    self._gallery_activity = None
                    self.gallery_count.configure(text=tr("Could not load maps"))
                    self._loading_panel_update(
                        getattr(self, "_gallery_panel", None), "Could not load maps", error=True
                    )
                    if hasattr(self, "gallery_progress") and self.gallery_progress.winfo_exists():
                        self.gallery_progress.stop()
                        self.gallery_progress.pack_forget()
                    self.status.set("Local map catalogue needs inspection: " + str(snapshot))
            self._start_gallery_snapshot()

        self._background(
            lambda: gallery_snapshot(
                app,
                cancel,
                advanced,
                history_error=getattr(getattr(self, "crash_monitor", None), "history_error", ""),
            ),
            loaded,
            label=None,
        )

    def _begin_gallery_batches(self, generation):
        if generation != self._gallery_generation or self.closed or self.view != "maps":
            return
        restore = getattr(self, "_gallery_restore_scroll", None)
        self._gallery_scroll = (
            restore
            if restore is not None
            else (self.canvas.yview()[0] if self.canvas.yview() else 0)
        )
        self._gallery_restore_scroll = None
        self._gallery_old_cards = list(self.grid_frame.winfo_children())
        self.cards = {}
        self._gallery_row_cards = {}
        self._gallery_items = filter_snapshot(
            self._gallery_snapshot, self.search.get(), self.map_filter.get(), self.sort_order.get()
        )
        self._gallery_index = 0
        self._activity_update(
            getattr(self, "_gallery_activity", None),
            "Displaying maps…",
            0,
            len(self._gallery_items),
        )
        self._gallery_batch(generation)

    def _gallery_batch(self, generation):
        try:
            self._render_gallery_batch(generation)
        except Exception as exc:
            self._cancel_gallery()
            self.gallery_count.configure(text=tr("Could not display maps"))
            if hasattr(self, "canvas") and self.canvas.winfo_exists():
                self._gallery_panel = self._loading_panel(self.canvas, "Could not display maps")
            self._loading_panel_update(
                getattr(self, "_gallery_panel", None), "Could not display maps", error=True
            )
            self.status.set("Map display needs inspection: " + str(exc))

    def _render_gallery_batch(self, generation):
        self._gallery_after_id = None
        if self.closed or self.view != "maps" or generation != self._gallery_generation:
            return
        deadline = time.monotonic() + 0.008
        count = 0
        # Destruction is batched too; a previous large gallery must not block Tk.
        while self._gallery_old_cards and count < 8 and time.monotonic() < deadline:
            self._gallery_old_cards.pop().destroy()
            count += 1
        while (
            not self._gallery_old_cards
            and self._gallery_index < len(self._gallery_items)
            and count < 8
            and time.monotonic() < deadline
        ):
            index = self._gallery_index
            self._create_gallery_card(index, self._gallery_items[index])
            self._loading_panel_end(getattr(self, "_gallery_panel", None))
            self._gallery_panel = None
            self._gallery_index += 1
            count += 1

        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._activity_update(
            getattr(self, "_gallery_activity", None),
            "Displaying maps…",
            self._gallery_index,
            len(self._gallery_items),
        )
        if hasattr(self, "gallery_count"):
            self.gallery_count.configure(
                text=tr(
                    "Displaying maps… {completed} of {total}",
                    completed=self._gallery_index,
                    total=len(self._gallery_items),
                )
            )
        if hasattr(self, "gallery_progress") and self.gallery_progress.winfo_exists():
            self.gallery_progress.stop()
            self.gallery_progress.configure(
                mode="determinate",
                maximum=max(1, len(self._gallery_items)),
                value=self._gallery_index,
            )
        if self._gallery_old_cards or self._gallery_index < len(self._gallery_items):
            self._gallery_after_id = self.root.after(1, lambda: self._gallery_batch(generation))
        else:
            if not self._gallery_items:
                message = (
                    "Import a map archive to start your library."
                    if not self.search.get().strip()
                    else "No matching maps."
                )
                ttk.Label(
                    self.grid_frame, text=message, padding=24, foreground=self.secondary
                ).grid(row=0, column=0, sticky="w")
            used = {item.art_key for item in self._gallery_items if item.art_key}
            self.images = {key: value for key, value in self.images.items() if key in used}
            self.canvas.yview_moveto(self._gallery_scroll)
            self._selection_details()
            self._loading_panel_end(getattr(self, "_gallery_panel", None))
            self._gallery_panel = None
            if hasattr(self, "gallery_progress") and self.gallery_progress.winfo_exists():
                self.gallery_progress.stop()
                self.gallery_progress.pack_forget()
            self._activity_end(getattr(self, "_gallery_activity", None))
            self._gallery_activity = None
            if hasattr(self, "gallery_count"):
                self.gallery_count.configure(
                    text=tr(
                        "{count} shown · {total} imported",
                        count=len(self._gallery_items),
                        total=len(self._gallery_snapshot),
                    )
                )

    def _create_gallery_card(self, index, item):
        record = item.record
        selected = record.profile_id == self.selected_profile
        error = item.history_error or (
            "History snapshot unavailable" if item.history is None else ""
        )
        card = history_card(
            self.grid_frame,
            title=_display_name(record.name),
            summary=item.history or summarize((), {}),
            verification=item.verification,
            metadata=item.metadata,
            palette=(self.surface, self.text, self.secondary, self.border, self.accent),
            photo=self._art(item),
            selected=selected,
            error=error,
        )
        columns = max(1, self.columns)
        row_index = index // columns
        card.grid(row=row_index, column=index % columns, padx=8, pady=8, sticky="n")
        self.cards[record.profile_id] = card
        if not hasattr(self, "_gallery_row_cards"):
            self._gallery_row_cards = {}
        row = self._gallery_row_cards.setdefault(row_index, [])
        row.append(card)
        height = max(c.winfo_reqheight() for c in row)
        for member in row:
            member.configure(height=height)

        def descendants(widget):
            yield widget
            for child in widget.winfo_children():
                yield from descendants(child)

        for widget in descendants(card):
            widget.bind("<Button-1>", lambda _, item=record: self._select(item))
            widget.bind("<Double-Button-1>", lambda _, item=record: self._map_details(item))
            widget.bind("<MouseWheel>", self._scroll_gallery)
        card.bind("<Return>", lambda _, item=record: self._select(item))
        card.bind("<space>", lambda _, item=record: self._select(item))
        self._localize_widgets(card)

    def _select(self, record: MapRecord) -> None:
        self.selected_profile = record.profile_id
        for profile_id, card in self.cards.items():
            selected = profile_id == self.selected_profile
            card.configure(
                highlightbackground=self.accent if selected else self.border, highlightthickness=2
            )
        self._selection_details()

    def _selection_details(self) -> None:
        item = next(
            (
                item
                for item in self._gallery_snapshot
                if item.record.profile_id == self.selected_profile
            ),
            None,
        )
        if item is None:
            self.detail.set("Choose a map to see its scenario and source details.")
        else:
            record, info = item.record, item.metadata
            self.detail.set(
                f"{_display_name(record.name)} · {record.source_label}\nCreated: {info.created or 'Not provided'} · Updated: {info.updated or 'Not provided'} · {item.verification}"
            )
        self._controls()

    def _poll_gallery_progress(self):
        progress = getattr(self, "_gallery_progress", None)
        update = progress.take() if progress is not None else None
        if update is not None:
            generation, completed, total = update
            if (
                generation == self._gallery_generation
                and self.view == "maps"
                and getattr(self, "_gallery_loading", False)
            ):
                self._activity_update(
                    getattr(self, "_gallery_activity", None), "Checking maps…", completed, total
                )
                self._loading_panel_update(
                    getattr(self, "_gallery_panel", None), "Loading your maps…", completed, total
                )
                if (
                    hasattr(self, "gallery_progress")
                    and self.gallery_progress.winfo_exists()
                    and total
                ):
                    self.gallery_progress.stop()
                    self.gallery_progress.configure(
                        mode="determinate", maximum=total, value=completed
                    )
                if hasattr(self, "gallery_count"):
                    self.gallery_count.configure(
                        text=tr(
                            "Checking maps… {completed} of {total}",
                            completed=completed,
                            total=total,
                        )
                    )

    def _selected_details(self) -> None:
        record = next(
            (
                item.record
                for item in self._gallery_snapshot
                if item.record.profile_id == self.selected_profile
            ),
            None,
        )
        if record:
            self._map_details(record)

    def _stop_speech(self) -> None:
        self._activity_end(getattr(self, "_speech_activity", None))
        if self.speech_process is not None and self.speech_process.poll() is None:
            self.speech_process.terminate()
        self.speech_process = None

    def _read_briefing(self, text: str) -> None:
        self._stop_speech()
        try:
            self.speech_process = speak(
                text, self.language_preferences.voice, available_voices=self.voices or None
            )
            self._speech_activity = self._activity_begin("Reading briefing aloud…")
        except Exception as exc:
            self._operation_error(tr("Read Briefing Aloud"), exc)

    def _map_details(self, record: MapRecord) -> None:
        app = self.app
        if app is None or getattr(self, "busy", False):
            return
        popup = tk.Toplevel(self.root)
        popup.title("Map Details — " + _display_name(record.name))
        popup.geometry("860x770")
        popup.minsize(760, 720)
        self._attach_activity(popup)
        panel = self._loading_panel(popup, "Loading map details…")
        community = self.community_index.get(record.archive_filename or record.name + ".7z")

        def prepare():
            metadata = app.map_metadata(record)
            verification = None
            verification_error = ""
            try:
                verification = app.verification_for(record)
            except Exception as exc:
                verification_error = "Gameplay record needs inspection: " + str(exc)
            try:
                choices = app.compatibility_recipes(record)
            except Exception as exc:
                choices = exc
            cache = None
            rating = None
            if community and community.discussion_url:
                cache = (
                    self.update_library
                    / "ratings"
                    / (community.discussion_url.rsplit("/", 1)[-1] + ".json")
                )
                rating = read_cached_rating(community.discussion_url, cache)
            return (
                metadata,
                verification,
                verification_error,
                app.import_checks(record),
                choices,
                community,
                cache,
                rating,
            )

        def loaded(data):
            if popup.winfo_exists():
                self._loading_panel_end(panel)
                self._display_map_details(record, data, popup)

        def failed(exc):
            if popup.winfo_exists():
                self._loading_panel_update(panel, "Could not load map details", error=True)
                panel[2].configure(text=str(exc))

        self._submit("Loading map details…", prepare, loaded, failed=failed)

    def _display_map_details(self, record, data, popup):
        metadata, verification, verification_error, checks, choices, community, cache, rating = data
        verification_status = (
            "Needs inspection"
            if verification_error
            else (verification.status if verification else "Not verified")
        )
        if verification_error:
            self.status.set(verification_error)
        frame = ttk.Frame(popup, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=_display_name(record.name)
            if " — Experimental " in record.name
            else (metadata.name or _display_name(record.name)),
            font=("Helvetica Neue", 20, "bold"),
            wraplength=780,
        ).pack(anchor="w")
        rows = [
            ("Author", metadata.author),
            ("Modified by", metadata.modified_by),
            ("Version", metadata.version),
            ("Created", metadata.created),
            ("Updated", metadata.updated),
            ("Map type", metadata.map_type),
            ("Archive source", record.source_label),
            ("Archive file modified", record.archive_modified),
            ("Mac testing", verification_status),
        ]
        facts = ttk.Frame(frame, padding=(0, 14))
        facts.pack(fill="x")
        for row, (label, value) in enumerate(rows):
            ttk.Label(facts, text=label, foreground=self.secondary).grid(
                row=row, column=0, sticky="nw", padx=(0, 18), pady=2
            )
            ttk.Label(facts, text=value or "Not provided", wraplength=510).grid(
                row=row, column=1, sticky="w", pady=2
            )
        ttk.Label(
            frame,
            text="Created and updated dates are declared by the map package; archive dates describe the download file.",
            foreground=self.secondary,
            wraplength=680,
        ).pack(anchor="w")
        links = ttk.Frame(frame, padding=(0, 10))
        links.pack(fill="x")
        urls = ([record.source_url] if record.source_url else []) + list(metadata.source_urls)
        for i, url in enumerate(dict.fromkeys(urls)):
            if safe_source_url(url):
                ttk.Button(
                    links,
                    text="Open Archive Source"
                    if i == 0 and record.source_url
                    else "Author’s Source",
                    command=lambda target=url: webbrowser.open(target),
                ).pack(side="left", padx=(0, 8))
        if community and community.discussion_url:
            ttk.Button(
                links,
                text="Community Reviews & Rating",
                command=lambda: webbrowser.open(community.discussion_url),
            ).pack(side="left")
        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        HistoryPanel(
            notebook,
            record,
            PlayHistoryStore(self.app.library),
            self.app.installation.executable_sha256,
            changed=self._render_gallery,
            submit=self._submit,
        )
        identity = (
            f"Archive: {record.archive_filename or record.name}\nSHA-256: {record.archive_sha256}\nVariant: {record.variant_id}\n\n"
            + self._verification_details(verification)
        )
        if verification_error:
            identity = f"Archive: {record.archive_filename or record.name}\nSHA-256: {record.archive_sha256}\nVariant: {record.variant_id}\n\n{verification_error}"
        if (
            verification
            and not isinstance(verification, VerificationSummary)
            and not verification.resources_sha256
        ):
            identity += "\n\nHistorical limited report: resource timestamps were not recorded. Scenario and feature coverage remains unknown. Any reported issue is retained as a historical warning."
        community_text = (
            f"Upstream community report: {community.stability}\nMultiplayer: "
            + (
                {True: "Reported supported", False: "Reported unsupported", None: "Not reported"}[
                    community.multiplayer
                ]
            )
            + "\n\nThese are community reports for the original map, not verification on the Mac edition. "
            "Open Community Reviews & Rating to see current votes and discussion."
            if community
            else "No community information cached for this archive. Refresh Collection to check the upstream index."
        )
        text_widgets = {}
        for title, content in (
            ("Briefing", metadata.description or "No briefing provided."),
            ("Original mapInfo", metadata.raw_text or "No mapInfo.txt provided."),
            ("Community", community_text),
            ("Mac Test & Identity", identity),
            ("Import Checks", _checks_text(checks)),
        ):
            tab = ttk.Frame(notebook)
            notebook.add(tab, text=tr(title))
            text = tk.Text(
                tab, wrap="word", font=("Helvetica Neue", 12), padx=12, pady=12, borderwidth=0
            )
            bar = ttk.Scrollbar(tab, command=text.yview)
            text.configure(yscrollcommand=bar.set)
            bar.pack(side="right", fill="y")
            text.pack(fill="both", expand=True)
            render_linked_text(text, content)
            text_widgets[title] = text

        def replace_text(title, content):
            widget = text_widgets[title]
            if widget.winfo_exists():
                render_linked_text(widget, content)

        self._compatibility_tab(notebook, record, popup, choices)
        if community and community.discussion_url:

            def display_rating(rating):
                if not popup.winfo_exists():
                    return
                text = community_text + "\n\n"
                if rating is None:
                    text += "No rating cached. Use Refresh Rating to fetch public votes."
                else:
                    text += "Checked: " + rating.fetched_at + "\n"
                    if rating.approximate_stars is not None:
                        text += f"Approximate star average: {rating.approximate_stars:.1f} / 5\n"
                    if rating.total_votes is not None:
                        text += f"Total community votes: {rating.total_votes}\n"
                    text += "\n".join(
                        f"{option.label}: {option.percentage:g}%" for option in rating.options
                    )
                    text += "\n\n" + rating.detail
                replace_text("Community", text)

            display_rating(rating)
            ttk.Button(
                links,
                text="Refresh Rating",
                command=lambda: self._submit(
                    "Refreshing public community votes…",
                    lambda: fetch_rating(community.discussion_url, cache),
                    display_rating,
                ),
            ).pack(side="left", padx=8)
        actions = ttk.Frame(frame, padding=(0, 6))
        actions.pack(side="bottom", fill="x", before=notebook)
        ttk.Button(
            actions, text="Record Mac Test…", command=lambda: self._record_mac_test(record, popup)
        ).pack(side="left")
        ttk.Button(
            actions,
            text="Run Import Checks",
            command=lambda: self._submit(
                "Checking map XML and file references…",
                lambda: self.app.import_checks(record, refresh=True),
                lambda report: replace_text("Import Checks", _checks_text(report)),
            ),
        ).pack(side="left", padx=8)
        ttk.Button(
            actions,
            text="Switch to Original Game",
            command=lambda: self._submit(
                "Switching to Original Game…",
                lambda: self.app.activate(ORIGINAL),
                lambda _: self.status.set("Original Game is active. The map can now be removed."),
            ),
        ).pack(side="left")
        ttk.Button(
            actions, text="Remove Map…", command=lambda: self._remove_map(record, popup)
        ).pack(side="right")
        translation = ttk.Frame(frame, padding=(0, 8))
        translation.pack(side="bottom", fill="x", before=notebook)
        source = tk.StringVar(value=LANGUAGES["en"])
        target = tk.StringVar(value=LANGUAGES[self.language_preferences.language])
        ttk.Label(translation, text="From").pack(side="left")
        ttk.Combobox(
            translation,
            textvariable=source,
            values=tuple(LANGUAGES.values()),
            state="readonly",
            width=12,
        ).pack(side="left", padx=4)
        ttk.Label(translation, text="To").pack(side="left")
        ttk.Combobox(
            translation,
            textvariable=target,
            values=tuple(LANGUAGES.values()),
            state="readonly",
            width=12,
        ).pack(side="left", padx=4)

        def translate():
            source_code = next(k for k, v in LANGUAGES.items() if v == source.get())
            target_code = next(k for k, v in LANGUAGES.items() if v == target.get())
            self._submit(
                "Translating briefing with Apple’s installed languages…",
                lambda: translate_briefing(metadata.description, target_code, source_code),
                lambda result: replace_text("Briefing", result),
            )

        ttk.Button(translation, text="Translate Briefing", command=translate).pack(
            side="left", padx=4
        )
        ttk.Button(
            translation,
            text="Show Original",
            command=lambda: replace_text(
                "Briefing", metadata.description or "No briefing provided."
            ),
        ).pack(side="left", padx=4)
        controls = ttk.Frame(frame, padding=(0, 12, 0, 0))
        controls.pack(side="bottom", fill="x", before=notebook)
        ttk.Button(
            controls,
            text="Read Briefing Aloud",
            command=lambda: self._read_briefing(text_widgets["Briefing"].get("1.0", "end-1c")),
        ).pack(side="left")
        ttk.Button(controls, text="Stop Reading", command=self._stop_speech).pack(
            side="left", padx=8
        )
        ttk.Button(
            controls, text="Experimental Editions…", command=lambda: self._edition_options(record)
        ).pack(side="left", padx=4)

        def close():
            self._stop_speech()
            popup.destroy()

        ttk.Button(controls, text="Close", command=close).pack(side="right")
        popup.protocol("WM_DELETE_WINDOW", close)
        self._localize_widgets(popup)

    def _compatibility_tab(self, notebook, record, details, choices=None):
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text=tr("Compatibility"))
        try:
            if choices is None:
                choices = self.app.compatibility_recipes(record)
            if isinstance(choices, Exception):
                raise choices
        except Exception as exc:
            ttk.Label(
                tab, text="Compatibility options need inspection: " + str(exc), wraplength=650
            ).pack(anchor="w")
            return
        if not choices:
            text = (
                "This is a compiled compatibility edition. Use Mac Test & Identity "
                "to see observations for this exact edition."
                if getattr(record, "recipe_receipt_sha256", "")
                else "No reviewed repair recipe is available for this edition. "
                "A map can still work without a repair. Import checks and Mac "
                "test results describe what has been checked so far."
            )
            ttk.Label(tab, text=text, wraplength=650).pack(anchor="w")
            return
        chooser = ttk.Combobox(
            tab, state="readonly", values=tuple(choice.title for choice in choices)
        )
        chooser.current(0)
        chooser.pack(fill="x", pady=(0, 8))

        def choice():
            return choices[chooser.current() if chooser.current() >= 0 else 0]

        ttk.Button(
            tab,
            text="Create Compatibility Edition",
            command=lambda: self._create_recipe(record, choice().recipe_id, details),
        ).pack(side="bottom", anchor="w", pady=(8, 0))
        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True)
        text = tk.Text(
            body, wrap="word", font=("Helvetica Neue", 12), borderwidth=0, height=5, padx=6, pady=6
        )
        bar = ttk.Scrollbar(body, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)

        def describe(_event=None):
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert(
                "1.0",
                choice().description + "\n\nCreates a separate edition with its own saves. "
                "Your original map and saves are kept. If this exact edition already exists, "
                "it is selected with its existing saves. Files are checked before creation; "
                "an unsupported installation will be refused.",
            )
            text.configure(state="disabled")

        chooser.bind("<<ComboboxSelected>>", describe)
        describe()

    def _create_recipe(self, record, recipe_id, details):
        if self.app is None or self.busy:
            return

        def completed(edition):
            if details.winfo_exists():
                details.destroy()
            self.selected_profile = edition.profile_id
            # A previous search/filter can hide the new or reused edition.
            self.search.set("")
            self.map_filter.set("All maps")
            self._show("maps")
            self.status.set(
                "Compatibility edition ready. Select Play to test it. "
                "Your original map and saves are preserved; gameplay remains unverified."
            )

        self._submit(
            "Checking files and preparing a compatibility edition…",
            lambda: self.app.create_recipe_edition(record, recipe_id),
            completed,
        )

    def _record_mac_test(self, record: MapRecord, details) -> None:
        if self.busy or self.app is None:
            return
        popup = tk.Toplevel(details)
        self._attach_activity(popup)
        popup.title("Record Mac Test")
        popup.geometry("640x680")
        popup.minsize(610, 650)
        frame = ttk.Frame(popup, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=_display_name(record.name),
            font=("Helvetica Neue", 17, "bold"),
            wraplength=570,
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="Play this map, quit Railroads, then record your result before switching maps. Check only what you personally tested.",
            wraplength=570,
        ).pack(anchor="w", pady=12)
        checks = {key: tk.BooleanVar(value=False) for key in CHECKS}
        for key, label in MAC_TEST_LABELS.items():
            ttk.Checkbutton(frame, text=label, variable=checks[key]).pack(anchor="w", pady=3)
        ttk.Label(frame, text="Test scope / duration (required)").pack(anchor="w", pady=(16, 4))
        scope = tk.Text(
            frame,
            height=3,
            wrap="word",
            borderwidth=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground="#666666",
            padx=6,
            pady=6,
        )
        scope.pack(fill="x")
        ttk.Label(
            frame,
            text="Example: 20 minutes; built track, stations and a train; tested a bridge.",
            wraplength=570,
        ).pack(anchor="w")
        ttk.Label(frame, text="Crash or other issue (leave blank if none observed)").pack(
            anchor="w", pady=(14, 4)
        )
        issue = tk.Text(
            frame,
            height=3,
            wrap="word",
            borderwidth=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground="#666666",
            padx=6,
            pady=6,
        )
        issue.pack(fill="x")
        ttk.Label(
            frame,
            text="These checks save a limited report for this edition. They do not establish which scenarios or features were tested and cannot earn Verified. A reported issue remains visible as a historical warning.",
            wraplength=570,
        ).pack(anchor="w", pady=14)
        controls = ttk.Frame(frame)
        controls.pack(side="bottom", fill="x")

        def finished(result):
            if popup.winfo_exists():
                popup.destroy()
            if details.winfo_exists():
                details.destroy()
            self.status.set("Mac test recorded: " + result.status)
            self._show("maps")

        def save():
            observation = scope.get("1.0", "end-1c").strip()
            problem = issue.get("1.0", "end-1c").strip()
            if not observation or len(observation) > 500 or len(problem) > 500:
                messagebox.showerror(
                    "Test details",
                    "Enter a test scope. Scope and issue must each be 500 characters or fewer.",
                    parent=popup,
                )
                return
            values = {key: variable.get() for key, variable in checks.items()}
            self._submit(
                "Recording your Mac play test…",
                lambda: self.app.record_gameplay(
                    record,
                    datetime.now().astimezone().isoformat(),
                    getpass.getuser(),
                    values,
                    observation,
                    problem,
                ),
                finished,
            )

        ttk.Button(controls, text="Cancel", command=popup.destroy).pack(side="right")
        ttk.Button(controls, text="Save Test Result", command=save).pack(side="right", padx=8)

    def _remove_map(self, record: MapRecord, details) -> None:
        if self.busy or self.app is None:
            return
        if self.app.profiles._state()["active"] == record.profile_id:
            messagebox.showinfo(
                "Map is active",
                "Quit Railroads, then use Switch to Original Game in Map Details. You can remove this map afterward.",
                parent=details,
            )
            return
        if not messagebox.askyesno(
            "Remove map?",
            "Remove “"
            + _display_name(record.name)
            + "” and its launcher-owned downloaded archive?\n\nSaved games will be kept and restored when you re-import this exact map version. Archives used by another edition will stay. Your source files outside the launcher library will stay.",
            parent=details,
            default="no",
        ):
            return

        def finished(saved_games):
            if details.winfo_exists():
                details.destroy()
            if self.selected_profile == record.profile_id:
                self.selected_profile = None
            self.download_states.clear()
            self.status.set(
                "Map and unshared archives removed. Saved games kept for exact-version re-import."
            )
            self._show("maps")

        self._submit(
            "Removing map and keeping saved games…", lambda: self.app.remove_map(record), finished
        )

    @staticmethod
    def _verification_details(result) -> str:
        if result is None:
            return "No local gameplay result recorded. Scenario coverage remains unknown."
        if isinstance(result, VerificationSummary):
            lines = [result.status, result.scope]
            for issue in result.local_issues:
                lines.extend(["", "Local failure · " + issue["occurred_at"], issue["detail"]])
            for scenario in result.scenarios:
                lines.extend(["", scenario.scenario_path + " · " + scenario.status])
                lines.extend(
                    action.replace("_", " ").title() + ": " + outcome.replace("_", " ")
                    for action, outcome in scenario.actions
                )
                lines.extend(scenario.limitations)
            for old in result.historical:
                lines.extend(
                    [
                        "",
                        "Historical limited report · " + old.observed_at,
                        "Reported by: " + old.reporter,
                        old.scope,
                    ]
                )
                lines.extend(
                    ("✓ " if old.checks[key] else "○ ") + label
                    for key, label in MAC_TEST_LABELS.items()
                )
                if old.issue:
                    lines.append("Historical unscoped issue: " + old.issue)
            for old in result.limited:
                raw = old.record.payload
                reviewed = (
                    old.interpretation.payload["outcome"]
                    if old.interpretation is not None
                    else "awaiting review"
                )
                lines.extend(
                    [
                        "",
                        "Earlier scenario observation · " + raw["scenario"]["scenario_path"],
                        raw["action"].replace("_", " ").title()
                        + ": recorded "
                        + raw["outcome"].replace("_", " ")
                        + "; review "
                        + reviewed.replace("_", " "),
                        "The original result is retained. Complete input coverage was not confirmed.",
                    ]
                )
                if old.issue:
                    lines.append(old.issue)
            return "\n".join(lines)
        return (
            f"{result.status} · {result.observed_at}\nReported by: {result.reporter}\n"
            + "Historical limited report; scenario and feature coverage remains unknown.\n"
            + result.scope
            + "\n\n"
            + "\n".join(
                ("✓ " if result.checks[key] else "○ ") + label
                for key, label in MAC_TEST_LABELS.items()
            )
            + "\n\n"
            + result.issue
        )

    def _verification_text(self, record: MapRecord, *, detail: bool = False) -> str:
        if self.app is None:
            return "Not verified"
        try:
            result = self.app.verification_for(record)
        except Exception as exc:
            self.status.set("Gameplay record needs inspection: " + str(exc))
            return "Needs inspection"
        if result is None:
            return "Not verified"
        return result.status + (" · " + result.scope if detail else "")

    def _original_view(self) -> None:
        frame = ttk.Frame(self.content, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Original Game", font=("Helvetica Neue", 21, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Play the stock Steam profile with its own saves. Your custom maps and their saves stay in separate profiles.",
            wraplength=560,
        ).pack(anchor="w", pady=(12, 20))
        self.original_button = ttk.Button(
            frame, text="Play Original Game", command=lambda: self._play(ORIGINAL)
        )
        self.original_button.pack(anchor="w")
        self._controls()

    def _setup_view(self) -> None:
        frame = ttk.Frame(self.content, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Setup", font=("Helvetica Neue", 21, "bold")).pack(anchor="w")
        if self.app is not None and self.app.profiles.state_file.exists():
            ttk.Label(
                frame,
                text="Your game is connected. Original Game and its saves are preserved.",
                wraplength=590,
            ).pack(anchor="w", pady=(14, 0))
        else:
            ttk.Label(
                frame,
                text="Connect your Steam game to start adding maps. Your original game profile and existing saves will be preserved.",
                wraplength=590,
            ).pack(anchor="w", pady=(14, 14))
            self.setup_button = ttk.Button(frame, text="Get Started", command=self.setup)
            self.setup_button.pack(anchor="w")
        ttk.Label(
            frame,
            text="The launcher preserves each source archive and keeps future saves with the selected map.",
            wraplength=590,
            foreground=self.secondary,
        ).pack(anchor="w", pady=(20, 0))
        ttk.Separator(frame).pack(fill="x", pady=(24, 18))
        ttk.Label(frame, text="App Updates", font=("Helvetica Neue", 16, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text=f"Installed launcher version {APP_VERSION}. Releases come only from the Craig-Franklin personal fork.",
            foreground=self.secondary,
            wraplength=590,
        ).pack(anchor="w", pady=(6, 10))
        automatic = ttk.Checkbutton(
            frame,
            text="Automatically install verified updates when I quit the launcher",
            variable=self.auto_updates,
            command=self._save_update_setting,
        )
        self._settings_control(automatic)
        automatic.pack(anchor="w")
        ttk.Label(
            frame, textvariable=self.update_status, foreground=self.secondary, wraplength=590
        ).pack(anchor="w", pady=(8, 10))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="w")
        self.check_update_button = ttk.Button(
            buttons, text="Check for Updates", command=self.check_for_updates
        )
        self.check_update_button.pack(side="left")
        self.install_update_button = ttk.Button(
            buttons, text="Download & Install Update…", command=self._download_update
        )
        self.install_update_button.pack(side="left", padx=(8, 0))
        self._controls()

    def _localize_widgets(self, widget) -> None:
        for child in widget.winfo_children():
            try:
                if (
                    not getattr(child, "_dynamic_text", False)
                    and "text" in child.keys()
                    and ("textvariable" not in child.keys() or not child.cget("textvariable"))
                ):
                    original = getattr(child, "_english_text", str(child.cget("text")))
                    child._english_text = original
                    child.configure(text=tr(original))
            except tk.TclError:
                pass
            self._localize_widgets(child)

    def _log(self, action, state, detail="") -> None:
        try:
            self.activity.append(action, state, detail)
        except Exception:
            pass  # Logging must never interrupt a completed game transaction.

    def _activity_view(self) -> None:
        frame = ttk.Frame(self.content, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Activity", font=("Helvetica Neue", 21, "bold")).pack(anchor="w")
        ttk.Label(
            frame, text="Recent launcher operations, retained locally between sessions."
        ).pack(anchor="w", pady=8)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(0, 8))
        query = tk.StringVar()
        ttk.Entry(actions, textvariable=query).pack(side="left", fill="x", expand=True)
        output = tk.Text(frame, wrap="word", padx=10, pady=10)
        bar = ttk.Scrollbar(frame, command=output.yview)
        output.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        output.pack(fill="both", expand=True)
        panel = self._loading_panel(output, "Loading activity history…")
        records = []

        def render(*_):
            needle = query.get().casefold()
            lines = [
                f"{r['time']}  ·  {r['state']}  ·  {r['action']}\n{r['detail']}"
                for r in reversed(records)
            ]
            content = (
                "\n\n".join(line for line in lines if needle in line.casefold())
                or "No matching activity yet."
            )
            output.configure(state="normal")
            output.delete("1.0", "end")
            output.insert("1.0", content)
            output.configure(state="disabled")

        def loaded(ok, value):
            if not output.winfo_exists():
                return
            self._loading_panel_end(panel)
            if ok:
                records[:] = value
                render()
            else:
                output.configure(state="normal")
                output.delete("1.0", "end")
                output.insert("1.0", "Activity history needs inspection: " + str(value))
                output.configure(state="disabled")

        def refresh():
            self._background_latest("activity-history", self.activity.records, loaded)

        output.insert("1.0", tr("Loading activity history…"))
        output.configure(state="disabled")
        ttk.Button(actions, text="Refresh", command=refresh).pack(side="left", padx=8)
        query.trace_add("write", render)
        refresh()

    def _preferences_view(self) -> None:
        frame = ttk.Frame(self.content, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Language & Voice", font=("Helvetica Neue", 21, "bold")).pack(
            anchor="w"
        )
        ttk.Label(frame, text="Interface language").pack(anchor="w", pady=(20, 5))
        language = tk.StringVar(value=LANGUAGES[self.language_preferences.language])
        choice = ttk.Combobox(
            frame,
            textvariable=language,
            values=tuple(LANGUAGES.values()),
            state="readonly",
            width=32,
        )
        self._settings_control(choice, "readonly")
        choice.pack(anchor="w")

        def changed(_=None):
            code = next(k for k, value in LANGUAGES.items() if value == language.get())
            voice = self.language_preferences.voice
            selected_sort, selected_filter = self.sort_order.get(), self.map_filter.get()

            def saved(_):
                set_language(code)
                self.sort_order.set(selected_sort)
                self.map_filter.set(selected_filter)
                self._menu()
                self._show(self.view)

            self._submit(
                "Saving language settings…",
                lambda: self.language_preferences.save(code, voice),
                saved,
            )

        choice.bind("<<ComboboxSelected>>", changed)
        ttk.Label(frame, text="Voice").pack(anchor="w", pady=(20, 5))
        labels = {f"{voice.name} · {voice.language}": voice.identifier for voice in self.voices}
        default = tr("System default")
        selected = next(
            (
                label
                for label, identifier in labels.items()
                if identifier == self.language_preferences.voice
            ),
            default,
        )
        voice_choice = ttk.Combobox(frame, values=(default, *labels), state="readonly", width=48)
        self._settings_control(voice_choice, "readonly")
        voice_choice.set(selected)
        voice_choice.pack(anchor="w")

        def voice_changed(_=None):
            code, voice = self.language_preferences.language, labels.get(voice_choice.get(), "")
            self._submit(
                "Saving voice settings…", lambda: self.language_preferences.save(code, voice)
            )

        voice_choice.bind("<<ComboboxSelected>>", voice_changed)
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="w", pady=12)

        def loaded(result):
            self.voices = result
            self.status.set(f"{len(self.voices)} macOS voices available.")
            if self.view == "preferences":
                self._show("preferences")

        ttk.Button(
            buttons,
            text="Refresh Voices",
            command=lambda: self._submit("Loading macOS voices…", list_voices, loaded),
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Manage Voices…",
            command=lambda: self._submit("Opening macOS voice settings…", open_voice_settings),
        ).pack(side="left", padx=8)
        ttk.Separator(frame).pack(fill="x", pady=20)
        ttk.Label(
            frame,
            text="Briefing translation uses Apple’s installed language models on macOS 26 or later. Original map text is preserved.",
            wraplength=610,
        ).pack(anchor="w")
        ttk.Button(
            frame,
            text="Translation Languages…",
            command=lambda: self._submit(
                "Opening translation settings…", open_translation_settings
            ),
        ).pack(anchor="w", pady=12)
        if not self.voices and not self.busy:
            self._submit("Loading macOS voices…", list_voices, loaded)

    def _check_map_updates(self) -> None:
        app = self.app

        def operation():
            remotes = fetch_catalogue()
            if app:
                app.match_collection_sources(remotes)
            return remotes, find_map_updates(app.catalogue() if app else (), remotes)

        def finished(result):
            self.remote_records, candidates = result
            self.collection_loaded = True
            self._show_map_updates(candidates)

        self._submit("Checking for map updates…", operation, finished)

    def _show_map_updates(self, candidates) -> None:
        popup = tk.Toplevel(self.root)
        popup.title(tr("Map Updates"))
        popup.geometry("790x440")
        self._attach_activity(popup)
        frame = ttk.Frame(popup, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Updates are imported as separate editions. Existing maps and saves remain available.",
            wraplength=730,
        ).pack(anchor="w", pady=(0, 12))
        rows = ttk.Treeview(
            frame, columns=("old", "new", "kind"), show="headings", selectmode="extended"
        )
        for key, label in (
            ("old", "Installed archive"),
            ("new", "Available archive"),
            ("kind", "Change"),
        ):
            rows.heading(key, text=tr(label))
            rows.column(key, width=300 if key != "kind" else 120)
        rows.pack(fill="both", expand=True)
        for i, item in enumerate(candidates):
            rows.insert(
                "",
                "end",
                iid=str(i),
                values=(
                    item.installed_filename,
                    item.remote.name,
                    "New version" if item.kind == "new_version" else "Revised archive",
                ),
            )
        note = (
            "No newer matching versions found."
            if not candidates
            else "Select the updates to import."
        )
        ttk.Label(frame, text=note).pack(anchor="w", pady=8)

        def import_selected():
            chosen = tuple(
                {
                    candidates[int(i)].remote.name: candidates[int(i)].remote
                    for i in rows.selection()
                }.values()
            )
            if chosen and not self.busy:
                popup.destroy()
                self._download_all(chosen)

        ttk.Button(frame, text="Import Selected Updates", command=import_selected).pack(anchor="e")
        self._localize_widgets(popup)

    def _edition_options(self, record):
        popup = tk.Toplevel(self.root)
        popup.title(tr("Experimental Editions"))
        self._attach_activity(popup)
        frame = ttk.Frame(popup, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="Create a separate edition of " + _display_name(record.name), wraplength=560
        ).pack(anchor="w")
        editor = tk.BooleanVar()
        difficulty = tk.BooleanVar()
        ttk.Checkbutton(
            frame,
            text="Enable terrain editor — export workflow pending",
            variable=editor,
            state="disabled",
        ).pack(anchor="w", pady=(16, 5))
        ttk.Checkbutton(
            frame, text="Use Windows launcher custom difficulty levels", variable=difficulty
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="Editor saving currently conflicts with protected map assets; that option stays unavailable until edited maps can be captured safely.\n\nCustom difficulty is experimental on the Mac edition. The new edition starts with empty saves and does not replace your original. Test a fresh scenario first. Custom difficulty is refused if the map supplies its own definitions.",
            wraplength=560,
        ).pack(anchor="w", pady=16)

        def create():
            if self.busy or not (editor.get() or difficulty.get()):
                return
            options = dict(editor=editor.get(), difficulty=difficulty.get())
            popup.destroy()
            self._submit(
                "Preparing a separate experimental edition…",
                lambda: self.app.create_edition(record, **options),
                self._imported,
            )

        ttk.Button(frame, text="Create Experimental Edition", command=create).pack(anchor="e")
        self._localize_widgets(popup)

    def _save_update_setting(self) -> None:
        enabled = bool(self.auto_updates.get())

        def failed(exc):
            self.auto_updates.set(False)
            self.update_status.set("Could not save update setting: " + str(exc))

        def saved(_):
            self.auto_updates.set(enabled)
            if enabled:
                self.update_status.set(
                    "Automatic updates enabled. A verified release installs when you quit."
                )
                self.check_for_updates()
            else:
                if self.pending_update_manual:
                    self.update_status.set(
                        "Automatic updates disabled. Your manually requested update will install when you quit."
                    )
                elif self.pending_update is not None:
                    self.update_status.set(
                        "Automatic updates disabled. The staged update will wait for you to re-enable it."
                    )
                else:
                    self.update_status.set(
                        "Automatic updates disabled. You can still check manually."
                    )

        self._submit(
            "Saving update settings…",
            lambda: self.update_preferences.set_automatic(enabled),
            saved,
            failed=failed,
        )

    def check_for_updates(self) -> None:
        if self.busy or self.pending_update is not None:
            return
        self._submit(
            "Checking the personal fork for an app update…",
            lambda: fetch_latest_release(APP_VERSION),
            self._update_checked,
        )

    def _update_checked(self, result: object) -> None:
        self.available_update = result if isinstance(result, Release) else None
        if self.available_update is None:
            self.update_status.set("No newer stable release is available.")
            self.status.set("Launcher is current; no newer stable release is available.")
        else:
            self.update_status.set(
                f"Version {self.available_update.version} is available from the personal fork."
            )
            self.status.set(f"Launcher update {self.available_update.version} is available.")
            if self.auto_updates.get():
                self._download_update(manual=False)
        self._controls()

    def _download_update(self, manual: bool = True) -> None:
        if self.available_update is None or self.busy or self.pending_update is not None:
            return
        self.update_request_manual = manual
        release = self.available_update
        team = trusted_team_from_bundle()
        if not team:
            self.update_status.set(
                "Automatic installation requires a Developer ID signed launcher release."
            )
            return

        def operation() -> Path:
            installed = current_app_bundle()
            archive = download_release(release, self.update_library / "updates")
            staged = stage_release(archive, release, self.update_library, team)
            return prepare_install(
                staged,
                installed,
                release,
                self.update_library,
                team,
                APP_VERSION,
                manual_install=manual,
            )

        self._submit(
            f"Downloading and verifying launcher {release.version}…", operation, self._update_staged
        )

    def _update_staged(self, result: object) -> None:
        self.pending_update = Path(result)
        self.pending_update_manual = self.update_request_manual
        version = self.available_update.version if self.available_update else "new"
        if self.pending_update_manual or self.auto_updates.get():
            self.update_status.set(
                f"Version {version} is verified and ready. It will install when you quit this launcher."
            )
            self.status.set(f"Launcher update {version} ready; quit the launcher to install it.")
        else:
            self.update_status.set(
                f"Version {version} is verified and staged. Automatic installation is off."
            )
            self.status.set(
                f"Launcher update {version} is staged; enable automatic installation to apply it."
            )
        self._controls()

    def _controls(self) -> None:
        settings = []
        for widget, ready in getattr(self, "_settings_widgets", ()):
            if widget.winfo_exists():
                widget.configure(state="disabled" if self.busy else ready)
                settings.append((widget, ready))
        self._settings_widgets = settings
        enrolled = self.app is not None and self.app.profiles.state_file.exists()
        if hasattr(self, "import_button") and self.import_button.winfo_exists():
            self.import_button.configure(
                state="normal" if enrolled and not self.busy else "disabled"
            )
        if hasattr(self, "play_button") and self.play_button.winfo_exists():
            ready = enrolled and self.selected_profile is not None and not self.busy
            self.play_button.configure(state="normal" if ready else "disabled")
        if hasattr(self, "details_button") and self.details_button.winfo_exists():
            self.details_button.configure(
                state="normal"
                if self.selected_profile is not None and not self.busy
                else "disabled"
            )
        if hasattr(self, "original_button") and self.original_button.winfo_exists():
            self.original_button.configure(
                state="normal" if enrolled and not self.busy else "disabled"
            )
        if hasattr(self, "setup_button") and self.setup_button.winfo_exists():
            self.setup_button.configure(
                state="normal" if self.app is not None and not self.busy else "disabled"
            )
        if hasattr(self, "welcome_retry_button") and self.welcome_retry_button.winfo_exists():
            self.welcome_retry_button.configure(state="normal" if not self.busy else "disabled")
        if hasattr(self, "download_button") and self.download_button.winfo_exists():
            ready = enrolled and self.selected_remote is not None and not self.busy
            self.download_button.configure(
                state="normal" if ready else "disabled", text=tr("Download & Import Selected")
            )
        if hasattr(self, "bulk_button") and self.bulk_button.winfo_exists():
            ready = (self.bulk_active and not self.bulk_cancel.is_set()) or (
                enrolled and self.collection_loaded and not self.busy
            )
            self.bulk_button.configure(
                text=tr("Cancel Imports" if self.bulk_active else "Download & Import All…"),
                state="normal" if ready else "disabled",
            )
        if hasattr(self, "check_update_button") and self.check_update_button.winfo_exists():
            self.check_update_button.configure(state="normal" if not self.busy else "disabled")
        if hasattr(self, "install_update_button") and self.install_update_button.winfo_exists():
            ready = (
                self.available_update is not None and self.pending_update is None and not self.busy
            )
            self.install_update_button.configure(state="normal" if ready else "disabled")

    def _submit(
        self,
        label: str,
        operation: Callable[[], object],
        finished: Optional[Callable[[object], None]] = None,
        *,
        failed=None,
        maps_changed=True,
    ) -> None:
        if self.busy or getattr(self, "initializing", False) or self.closed:
            return
        self.busy = True
        if maps_changed:
            self._maps_dirty = True
        self._cancel_gallery()
        self._operation_panel = None
        if (
            self.view == "maps"
            and not getattr(self, "cards", {})
            and hasattr(self, "canvas")
            and self.canvas.winfo_exists()
        ):
            self._operation_panel = self._loading_panel(self.canvas, label)
        self._operation_activity = self._activity_begin(label, priority=10)
        self._operation_failed = failed
        self.status.set(label)
        self._controls()

        def worker() -> None:
            self._log(label, "Started")
            try:
                value = operation()
                self._log(label, "Completed")
                self.results.put((True, value, finished))
            except Exception as exc:
                self._log(label, "Failed", str(exc))
                self.results.put((False, exc, None))

        try:
            Thread(target=worker, daemon=True).start()
        except Exception as exc:
            # Use normal UI delivery so operation-specific failure panels and
            # close/update handling also run when no worker could be started.
            self.results.put((False, exc, None))

    def _operation_warning(self, title, message):
        messagebox.showwarning(title, message, parent=self.root)

    def _operation_error(self, title, error):
        messagebox.showerror(title, str(error), parent=self.root)

    def _poll(self) -> None:
        if self.closed:
            return
        self._poll_gallery_progress()
        if hasattr(self, "background_results"):
            for _ in range(16):
                try:
                    ok, value, finished = self.background_results.get_nowait()
                except Empty:
                    break
                try:
                    finished(ok, value)
                except Exception as exc:
                    self._log("Display background result", "Failed", str(exc))
                    self.status.set("Background result needs inspection: " + str(exc))
                if self.closed:
                    return
        if getattr(self, "crash_monitor", None) is not None:
            self.crash_status.set(self.crash_monitor.status)
            revision = self.crash_monitor.issue_revision
            if revision != getattr(self, "_crash_issue_revision", 0) and not self.busy:
                self._crash_issue_revision = revision
                if self.view == "maps":
                    self._render_gallery()
            for event in self.crash_monitor.take_events():
                self.status.set(event)
                self._log("Game resource protection", "Stopped", event)
                self._operation_warning("Railroads memory protection", event)
            for event in self.crash_monitor.take_history_exits():
                self.root.after_idle(lambda item=event: self._history_exit_prompt(item))
        speech = self.speech_process
        if speech is not None and speech.done:
            self._activity_end(getattr(self, "_speech_activity", None))
            self.speech_process = None
            if getattr(speech, "error", None):
                self.status.set("Speech stopped: " + speech.error)
                self._log("Read briefing", "Failed", speech.error)
        self._update_download_rows()
        # A completion cannot overtake terminal row/activity delivery.
        if hasattr(self, "download_events") and not self.download_events.empty():
            self.root.after(10, self._poll)
            return
        try:
            while True:
                successful, value, finished = self.results.get_nowait()
                self.busy = False
                self._loading_panel_end(getattr(self, "_operation_panel", None))
                self._operation_panel = None
                self._activity_end(getattr(self, "_operation_activity", None))
                failed = getattr(self, "_operation_failed", None)
                self._operation_failed = None
                self._gallery_app = None
                if successful:
                    if finished:
                        try:
                            finished(value)
                        except Exception as exc:
                            self._log("Display operation result", "Failed", str(exc))
                            self.status.set(
                                "Operation completed; displaying its result failed: " + str(exc)
                            )
                else:
                    self.bulk_active = False
                    self.status.set("Action stopped: " + str(value))
                    if failed:
                        try:
                            failed(value)
                        except Exception as exc:
                            self._log("Display operation error", "Failed", str(exc))
                            self.status.set(
                                "Action stopped; displaying its error failed: " + str(exc)
                            )
                    else:
                        self._operation_error("Railroads launcher", value)
                if self.closed:
                    return
                if self.view == "maps" and not self.busy and getattr(self, "_maps_dirty", False):
                    self._render_gallery()
                self._controls()
        except Empty:
            pass
        if self.close_when_idle and not self.busy and not self.bulk_active:
            self.close_when_idle = False
            self._close()
            if not self.root.winfo_exists():
                return
        self.root.after(10 if getattr(self, "initializing", False) else 100, self._poll)

    def choose_steam_library(self) -> None:
        if self.busy or getattr(self, "initializing", False):
            return
        location = filedialog.askdirectory(
            parent=self.root, title="Choose the steamapps folder, not Steam.app"
        )
        if location:
            self._submit(
                "Checking the selected Steam library…",
                lambda: LauncherApplication.choose_steam_library(Path(location)),
                self._library_chosen,
            )

    def _history_exit_prompt(self, event):
        if self.closed or not self.app:
            return
        record = next(
            (
                r
                for r in self._records()
                if history_context(r, self.app.installation.executable_sha256)["variant_id"]
                == event["variant_id"]
                and r.archive_sha256 == event["archive_sha256"]
                and self.app.installation.executable_sha256 == event["game_executable_sha256"]
            ),
            None,
        )
        if record is None:
            return
        store = PlayHistoryStore(self.app.library)
        prompt_id = digest(event["attempt_id"], "prompt")
        try:
            if any(e["event_id"] == prompt_id for e in store.records()):
                return
            store.once(
                {k: event[k] for k in (*IDENTITIES, "scenario")},
                "prompt_offered",
                event_id=prompt_id,
                attempt_id=event["attempt_id"],
                source="observed",
            )
        except (OSError, ValueError) as exc:
            self.status.set("History prompt needs inspection: " + str(exc))
            return
        popup = tk.Toplevel(self.root)
        popup.title("Record this game's checkpoints?")
        popup.transient(self.root)
        body = ttk.Frame(popup, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=_display_name(record.name),
            font=("Helvetica Neue", 14, "bold"),
            wraplength=440,
        ).pack(anchor="w")
        ttk.Label(
            body,
            text="The game process exited. Which checkpoints did you reach? Its outcome stays unknown until confirmed. You can also record this later in Map Details → Play History.",
            wraplength=440,
        ).pack(anchor="w", pady=12)

        def record_now():
            popup.destroy()
            details = tk.Toplevel(self.root)
            details.title("Play History — " + _display_name(record.name))
            details.geometry("800x600")
            tabs = ttk.Notebook(details)
            tabs.pack(fill="both", expand=True)
            panel = HistoryPanel(
                tabs,
                record,
                store,
                self.app.installation.executable_sha256,
                changed=self._render_gallery,
                submit=self._submit,
            )
            panel.checkpoint_dialog(event["attempt_id"])

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Later", command=popup.destroy).pack(side="right")
        ttk.Button(buttons, text="Record checkpoints…", command=record_now).pack(
            side="right", padx=8
        )

    def _library_chosen(self, result: object) -> None:
        assert isinstance(result, LauncherApplication)
        self._cancel_gallery()
        self._collection_generation = getattr(self, "_collection_generation", 0) + 1
        self.collection_refreshing = False
        self._gallery_snapshot = ()
        self.images = {}
        self.metadata_cache = {}
        self.selected_profile = None
        self._maps_scroll = 0.0
        self.imported_names = set()
        self.download_states.clear()
        if getattr(self, "crash_monitor", None) is not None:
            self.crash_monitor.close()
            self.crash_monitor = None
        self.app = result
        self._start_crash_monitor_async()
        self.status.set("Steam Mac game found in the selected library.")
        self._show(self._initial_view() if self.view == "welcome" else self.view)

    def setup(self) -> None:
        if self.app is None or self.busy:
            return
        self._submit(
            "Checking your game and preserving its existing saves…",
            self.app.setup,
            lambda _: (
                self.status.set("You’re ready. Choose a map to download, or play Original Game."),
                self._show("collection"),
            ),
        )

    def import_map(self) -> None:
        if self.app is None or not self.app.profiles.state_file.exists():
            return
        location = filedialog.askopenfilename(
            parent=self.root,
            title="Choose a custom map archive",
            filetypes=[("Map archives", "*.7z *.zip *.tar"), ("All files", "*")],
        )
        if location:
            self._submit(
                "Inspecting and preparing " + Path(location).name + "…",
                lambda: self.app.import_archive(Path(location)),
                self._imported,
            )

    def _imported(self, result: object) -> None:
        assert isinstance(result, MapRecord)
        self.selected_profile = result.profile_id
        if self.close_when_idle or self.closed:
            return
        app = self.app

        def checked(report):
            findings = len(report["warnings"]) if report else 0
            self.status.set(
                result.name
                + (
                    f" imported with {findings} static findings — see Map Details → Import Checks."
                    if findings
                    else " imported. Gameplay is not verified; see Map Details for import checks."
                )
            )
            self._show("maps")

        def failed(exc):
            self.status.set(result.name + " imported; import checks need inspection: " + str(exc))
            self._show("maps")

        self._submit(
            "Reading import checks…", lambda: app.import_checks(result), checked, failed=failed
        )

    def play_selected(self) -> None:
        if self.selected_profile is not None:
            self._play(self.selected_profile)

    def _play(self, profile_id: str) -> None:
        if self.app is None:
            return
        app = self.app

        def inspect():
            record = next((r for r in app.catalogue() if r.profile_id == profile_id), None)
            return app.verification_for(record) if record else None

        def checked(verification):
            if self.closed or self.close_when_idle or self.app is not app:
                return
            if (
                verification
                and verification.issue
                and not messagebox.askyesno(
                    "Known map issue",
                    verification.issue + "\n\nLaunch this map anyway?",
                    parent=self.root,
                )
            ):
                self.status.set("Launch cancelled.")
                return
            # A modal consent dialog can service a Quit event before returning.
            if self.closed or self.close_when_idle or self.app is not app:
                return
            self._submit(
                "Switching profile and starting Railroads…",
                lambda: self._play_with_crash_monitor(profile_id),
                lambda _: self.status.set("Railroads launched. Quit it before changing maps."),
            )

        def refused(exc):
            self.status.set("Map verification needs inspection; launch cancelled.")
            messagebox.showerror("Map verification needs inspection", str(exc), parent=self.root)

        self._submit("Checking map before launch…", inspect, checked, failed=refused)

    def _start_crash_monitor_async(self):
        self.busy = True
        self._controls()
        app = self.app

        def create():
            monitor = CrashMonitor(app, APP_VERSION)
            try:
                monitor.start()
            except Exception:
                monitor.close()
                raise
            return monitor

        def ready(ok, monitor):
            if self.closed or self.app is not app:
                if ok:
                    monitor.close()
                return
            self.busy = False
            self.crash_monitor = monitor if ok else None
            self._controls()
            if self.view == "maps":
                self._render_gallery()
            if not ok:
                self.crash_status.set("Crash monitoring needs inspection; you can still play.")

        self._background(
            create,
            ready,
            dispose=lambda monitor: monitor.close(),
            label="Starting game protection…",
        )

    def _start_crash_monitor(self) -> None:
        try:
            self.crash_monitor = CrashMonitor(self.app, APP_VERSION)
            self.crash_monitor.start()
        except Exception:
            self.crash_monitor = None
            self.crash_status.set("Crash monitoring needs inspection; you can still play.")

    def _require_launch_owner(self, app):
        if self.closed or self.close_when_idle or self.app is not app:
            raise LaunchCancelledBeforeDispatch(
                "The launcher owner changed or is closing; launch cancelled"
            )

    def _play_with_crash_monitor(self, profile_id: str, *, mutation_guard=None):
        app = self.app

        def launch():
            # Existing application/profile guards run before this callback.
            self._require_launch_owner(app)
            if self.crash_monitor is not None:
                try:
                    self.crash_monitor.arm(profile_id)
                except Exception:
                    self.crash_monitor.status = (
                        "Crash monitoring needs inspection; you can still play."
                    )
            # Monitor arming and the profile transaction can block. Recheck the
            # captured owner at the actual dispatch boundary, not only on entry.
            with mutation_guard() if mutation_guard is not None else nullcontext():
                self._require_launch_owner(app)
                child = subprocess.Popen(
                    ["/usr/bin/open", "-a", str(app.installation.executable.parents[2])]
                )
            if self.crash_monitor is not None:
                try:
                    self.crash_monitor.bind_after_launch(profile_id)
                except Exception:
                    self.crash_monitor.status = (
                        "Launch observation unavailable; unmatched reports stay private."
                    )
            return child

        if mutation_guard is not None:
            return app.play(profile_id, launch=launch, mutation_guard=mutation_guard)
        return app.play(profile_id, launch=launch)

    def crash_reports(self) -> None:
        popup = tk.Toplevel(self.root)
        self._attach_activity(popup)
        popup.title("Crash Reports")
        popup.transient(self.root)
        panel = ttk.Frame(popup, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(
            panel,
            text="Play normally; keep this launcher open.",
            font=("Helvetica Neue", 14, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            panel,
            wraplength=540,
            justify="left",
            text=(
                "Complete macOS crash reports stay in a private folder on this Mac. "
                "Reports arriving after you close the launcher are checked when it next opens. "
                "Confirmed crashes are recorded against the active map edition as known issues. "
                "Memory protection stops a runaway game above 8 GiB for five seconds, or 16 GiB immediately. "
                "This includes compressed and swapped memory. A normal game exit is never reported as a crash."
            ),
        ).pack(anchor="w", pady=(10, 14))
        monitor = self.crash_monitor
        enabled = tk.BooleanVar(value=monitor.enabled if monitor else False)
        automatic = tk.BooleanVar(value=monitor.automatic_reporting if monitor else False)
        protection = tk.BooleanVar(value=monitor.protect_resources if monitor else True)

        def configure():
            values = dict(
                enabled=enabled.get(),
                automatic_reporting=automatic.get(),
                protect_resources=protection.get(),
            )

            def failed(exc):
                self.crash_status.set(
                    "Crash reporting preferences could not be saved; you can still play."
                )

            self._submit(
                "Saving crash reporting settings…",
                lambda: monitor.configure(**values),
                failed=failed,
            )

        for label, variable in (
            ("Monitor game crashes", enabled),
            ("Stop Railroads if its memory use runs away", protection),
            ("Automatically report sanitized crashes to GitHub", automatic),
        ):
            checkbox = ttk.Checkbutton(panel, text=label, variable=variable, command=configure)
            self._settings_control(checkbox, "normal" if monitor else "disabled")
            checkbox.pack(anchor="w", pady=4)
        ttk.Label(
            panel,
            wraplength=540,
            justify="left",
            text=(
                "Automatic reporting uses your existing GitHub CLI sign-in. Public issues contain only "
                "validated versions, exception details, bounded thread frames and register values, "
                "and binary image identities. Each report states its coverage and omissions. "
                "Passwords, tokens, personal paths, save names, raw logs and attachments are excluded. "
                "If GitHub is unavailable, confirmed reports wait locally."
            ),
        ).pack(anchor="w", pady=14)
        ttk.Label(panel, wraplength=540, textvariable=self.crash_status).pack(anchor="w", pady=8)
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x", pady=(8, 0))

        def reconcile():
            def ready(ok, value):
                if not ok:
                    monitor.status = (
                        "Crash report reconciliation needs inspection; reports remain local."
                    )

            self._background_latest(
                "crash-reconcile", lambda: monitor.poll_once(reconcile=True), ready
            )

        ttk.Button(
            buttons,
            text="Check pending reports",
            command=reconcile,
            state="normal" if monitor else "disabled",
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Open private reports folder",
            state="normal" if monitor else "disabled",
            command=lambda: subprocess.Popen(["/usr/bin/open", str(monitor.root)]),
        ).pack(side="left", padx=8)
        ttk.Button(buttons, text="Close", command=popup.destroy).pack(side="right")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    LauncherWindow().run()


if __name__ == "__main__":
    main()
