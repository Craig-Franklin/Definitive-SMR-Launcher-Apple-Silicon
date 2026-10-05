"""Mac map gallery built from native Tk controls and local, user-owned map art."""
from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from typing import Callable, Optional
from datetime import datetime
import getpass
import math
import re
import subprocess
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .activity import ActivityLog
from .map_updates import find_map_updates
from .ratings import fetch_rating, read_cached_rating
from .language import LANGUAGES, LanguagePreferences, set_language, tr, translate_briefing, open_translation_settings
from .voices import list_voices, speak, open_voice_settings
from .verification import CHECKS
from .activation import ORIGINAL
from .application import LauncherApplication, MapRecord
from .collection import RemoteMap, download_all_maps, download_map, fetch_catalogue
from .map_metadata import safe_source_url
from .community import CommunityError, fetch_community_index, read_cached_community_index
from . import APP_VERSION
from .updates import (Release, UpdatePreferences, current_app_bundle, download_release,
                      fetch_latest_release, pending_install, pending_install_manual,
                      prepare_install, stage_release,
                      start_install_helper, trusted_team_from_bundle)


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
    return (f"Checked: {report['checked_at']}\nXML files inspected: {report['xml_files_checked']}\n\n"
            + ("Potential problems to inspect:\n\n" + "\n\n".join(findings) if findings else "No problems found by these static checks.")
            + "\n\n" + report["limitations"])


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
        self.busy = False
        self.closed = False
        self._search_refresh_after_id = None
        self._search_refresh_generation = 0
        self.results: Queue[tuple[bool, object, Optional[Callable[[object], None]]]] = Queue()
        self.download_events: Queue[tuple[RemoteMap, str, int, int, str]] = Queue()
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
        self.update_library = Path.home() / "Library/Application Support/Definitive SMR Launcher Apple Silicon"
        self.activity = ActivityLog(self.update_library)
        self.language_preferences = LanguagePreferences(self.update_library)
        try:
            self.language_preferences.load()
        except Exception:
            pass
        set_language(self.language_preferences.language)
        self.voices = ()
        self.community_index = read_cached_community_index(self.update_library / "community-index.json")
        self.community_notice = ""
        self.imported_names = set()
        self.update_preferences = UpdatePreferences(self.update_library)
        try:
            automatic = self.update_preferences.automatic()
            initial_update_status = "No update check yet."
        except Exception as exc:
            automatic = False
            initial_update_status = "Update settings need inspection: " + str(exc)
        self.auto_updates = tk.BooleanVar(value=automatic)
        self.update_status = tk.StringVar(value=initial_update_status)
        self.available_update: Optional[Release] = None
        self.pending_update: Optional[Path] = None
        self.pending_update_manual = False
        self.update_request_manual = False
        self.search = tk.StringVar()
        self.sort_order = TranslatedChoice(value="Name A–Z", choices=("Name A–Z", "Created newest", "Created oldest", "Updated newest", "Author"))
        self.map_filter = TranslatedChoice(value="All maps", choices=("All maps", "Single player", "Multiplayer", "Verified", "Not verified", "Known issue"))
        self.metadata_cache = {}
        self.speech_process = None
        self.status = tk.StringVar(value="Checking the Steam Mac game…")
        self.images: dict[str, tk.PhotoImage] = {}
        self.columns = 0

        self.background = self.root.cget("background")
        dark = sum(self.root.winfo_rgb(self.background)) < 3 * 32768
        self.surface = "#252527" if dark else "#ffffff"
        self.text = "#f3f3f4" if dark else "#202124"
        self.secondary = "#b5b5b8" if dark else "#64666a"
        self.border = "#454549" if dark else "#d7d8da"
        self.accent = "#3b91e8" if dark else "#1265a8"

        self._menu()
        self._layout()
        try:
            self.app = LauncherApplication.discover()
            if self.app.profiles.state_file.exists():
                self.status.set("Your library is ready. Select a map, view its details, or play Original Game.")
            else:
                self.status.set("Steam Mac game found. Get started to prepare your map library.")
        except Exception as exc:
            self.status.set("Steam Mac game unavailable: " + str(exc))
        try:
            if trusted_team_from_bundle():
                self.pending_update = pending_install(self.update_library, current_app_bundle(), APP_VERSION)
                if self.pending_update:
                    self.pending_update_manual = pending_install_manual(self.pending_update)
                    if self.pending_update_manual or automatic:
                        self.update_status.set("A staged update is ready; it will be rechecked when you quit to install it.")
                    else:
                        self.update_status.set("A staged update is ready. Automatic installation is off.")
        except Exception as exc:
            self.update_status.set("Pending update needs inspection: " + str(exc))
        self._show(self._initial_view())
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.createcommand("::tk::mac::Quit", self._close)
        if automatic and self.pending_update is None:
            self.root.after(1500, self.check_for_updates)

    def _menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label=tr("Import Map…"), command=self.import_map, accelerator="⌘O")
        file_menu.add_command(label=tr("Choose Steam Library Folder…"), command=self.choose_steam_library)
        file_menu.add_separator()
        file_menu.add_command(label=tr("Quit"), command=self._close, accelerator="⌘Q")
        menu.add_cascade(label=tr("File"), menu=file_menu)
        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="Installation & Help", command=lambda: webbrowser.open(
            "https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/blob/main/MACOS_README.md"))
        help_menu.add_command(label="Windows Feature Comparison", command=lambda: webbrowser.open(
            "https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/blob/main/docs/FEATURE_PARITY.md"))
        menu.add_cascade(label=tr("Help"), menu=help_menu)
        self.root.configure(menu=menu)
        self.root.bind("<Command-o>", lambda _: self.import_map())
        self.root.bind("<Command-f>", lambda _: self.search_entry.focus_set())
        self.root.bind("<Command-q>", lambda _: self._close())

    def _close(self) -> None:
        self._cancel_search_refresh()
        if self.busy or self.bulk_active:
            self.close_when_idle = True
            if self.bulk_active:
                self.bulk_cancel.set()
            self.status.set("Finishing the current safe operation before closing…")
            self._controls()
            return
        if self.pending_update is not None and (self.pending_update_manual or self.auto_updates.get()):
            try:
                start_install_helper(self.pending_update, self.update_library)
            except Exception as exc:
                self.close_when_idle = False
                if not messagebox.askyesno(
                    "Update not installed",
                    f"The update could not start: {exc}\n\nQuit without installing it? "
                    "The staged update will remain available for inspection or retry.",
                    parent=self.root,
                ):
                    return
        self.root.destroy()
        self.closed = True
        self._stop_speech()

    def _layout(self) -> None:
        header = ttk.Frame(self.root, padding=(22, 14, 22, 12))
        header.pack(fill="x")
        ttk.Label(header, text="Definitive SMR", font=("Helvetica Neue", 20, "bold")).pack(side="left")
        ttk.Label(header, text="Apple Silicon", foreground=self.secondary).pack(side="left", padx=(10, 0))
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
        for key, label in (("maps", "Map Library"), ("collection", "Collection"),
                           ("original", "Original Game"), ("preferences", "Language & Voice"),
                           ("activity", "Activity"), ("setup", "Setup")):
            button = ttk.Button(self.sidebar, text=label, command=lambda destination=key: self._show(destination))
            button.pack(fill="x", pady=(0, 7))
            self.nav_buttons[key] = button
        ttk.Separator(self.sidebar).pack(fill="x", pady=(12, 14))
        ttk.Label(self.sidebar, text="Steam Mac edition", font=("Helvetica Neue", 12, "bold")).pack(anchor="w")
        self.steam_label = ttk.Label(self.sidebar, text="Checking…", wraplength=185, foreground=self.secondary)
        self.steam_label.pack(anchor="w", pady=(5, 12))

        self.sidebar_separator = ttk.Separator(body, orient="vertical")
        self.sidebar_separator.pack(side="left", fill="y")
        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)
        ttk.Separator(self.root).pack(fill="x")
        ttk.Label(self.root, textvariable=self.status, padding=(22, 8), foreground=self.secondary,
                  wraplength=900).pack(fill="x")

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
        ttk.Label(frame, text="Welcome to Definitive SMR", font=("Helvetica Neue", 26, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Discover new maps for Sid Meier’s Railroads!", foreground=self.secondary,
                  font=("Helvetica Neue", 14), wraplength=660).pack(anchor="w", pady=(10, 28))
        if self.app is None:
            ttk.Label(frame, text="Let’s find your game", font=("Helvetica Neue", 17, "bold")).pack(anchor="w")
            ttk.Label(frame, text="Install Sid Meier’s Railroads! in Steam, open it once, then quit the game and check again.",
                      wraplength=620).pack(anchor="w", pady=(10, 20))
            self.welcome_retry_button = ttk.Button(frame, text="Check Again", command=self._retry_discovery)
            self.welcome_retry_button.pack(anchor="w")
            ttk.Button(frame, text="Choose Steam Library Folder…", command=self.choose_steam_library).pack(anchor="w", pady=(12, 0))
        else:
            ttk.Label(frame, text="Your Steam game is ready to connect", font=("Helvetica Neue", 17, "bold")).pack(anchor="w")
            ttk.Label(frame, text="Get Started checks your game and preserves its original profile and existing saves. Each custom map will have its own saves.",
                      wraplength=620).pack(anchor="w", pady=(10, 12))
            ttk.Label(frame, text="Quit Railroads before continuing.", foreground=self.secondary).pack(anchor="w", pady=(0, 20))
            self.setup_button = ttk.Button(frame, text="Get Started", command=self.setup)
            self.setup_button.pack(anchor="w")
        self._controls()

    def _retry_discovery(self) -> None:
        self._submit("Looking for the Steam Mac game…", LauncherApplication.discover, self._library_chosen)

    def _show(self, view: str) -> None:
        self._cancel_search_refresh()
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
        ttk.Label(frame, text="Steam Mac game required", font=("Helvetica Neue", 21, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Install Sid Meier’s Railroads! from Steam on its default branch and launch it once. If Steam uses another drive, choose that drive’s steamapps folder.",
                  wraplength=550).pack(anchor="w", pady=(12, 18))
        ttk.Button(frame, text="Choose Steam Library Folder…",
                   command=self.choose_steam_library).pack(anchor="w")
        ttk.Label(frame, text="Choose a steamapps folder, not Steam.app.",
                  foreground=self.secondary).pack(anchor="w", pady=(8, 0))

    def _maps_view(self) -> None:
        records = self._records()
        top = ttk.Frame(self.content, padding=(24, 20, 24, 12))
        top.pack(fill="x")
        ttk.Label(top, text="Map Library", font=("Helvetica Neue", 21, "bold")).pack(side="left")
        ttk.Label(top, text=f"{len(records)} imported", foreground=self.secondary).pack(side="left", padx=(12, 0))
        self.play_button = ttk.Button(top, text="Play Selected", command=self.play_selected)
        self.play_button.pack(side="right")
        self.import_button = ttk.Button(top, text="Import Map…", command=self.import_map)
        self.import_button.pack(side="right", padx=(0, 8))

        options = ttk.Frame(self.content, padding=(24, 0, 24, 10))
        options.pack(fill="x")
        ttk.Label(options, text="Show").pack(side="left")
        filters = ttk.Combobox(options, textvariable=self.map_filter, state="readonly", width=18,
                              values=tuple(tr(key) for key in self.map_filter.choices))
        filters.pack(side="left", padx=(6, 16))
        filters.bind("<<ComboboxSelected>>", lambda _: self._render_gallery())
        ttk.Label(options, text="Sort").pack(side="left")
        order = ttk.Combobox(options, textvariable=self.sort_order, state="readonly", width=19,
                            values=tuple(tr(key) for key in self.sort_order.choices))
        order.pack(side="left", padx=6)
        order.bind("<<ComboboxSelected>>", lambda _: self._render_gallery())
        self.details_button = ttk.Button(options, text="Map Details…", command=self._selected_details)
        self.details_button.pack(side="right")

        self.detail = tk.StringVar(value="Choose a map to see its scenario and source details.")
        ttk.Label(self.content, textvariable=self.detail, padding=(24, 0, 24, 12),
                  foreground=self.secondary, wraplength=680).pack(fill="x")
        holder = ttk.Frame(self.content)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, background=self.background, highlightthickness=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize_gallery)
        self.canvas.bind("<MouseWheel>", self._scroll_gallery)
        self.grid_frame.bind("<MouseWheel>", self._scroll_gallery)
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
            if query not in " ".join((item.name, metadata.name or "", metadata.author or "", metadata.description)).casefold():
                continue
            wanted = self.map_filter.get()
            if wanted in ("Single player", "Multiplayer") and wanted.replace(" ", "").casefold() not in (metadata.map_type or "").replace(" ", "").casefold():
                continue
            if wanted in ("Verified", "Not verified", "Known issue") and self._verification_text(item) != wanted:
                continue
            records.append(item)
        order = self.sort_order.get()
        if order in ("Created newest", "Created oldest", "Updated newest"):
            field = "updated" if order == "Updated newest" else "created"
            known = [r for r in records if getattr(self._metadata(r), field)]
            unknown = [r for r in records if not getattr(self._metadata(r), field)]
            known.sort(key=lambda r: (getattr(self._metadata(r), field), r.name.casefold()), reverse=order != "Created oldest")
            return known + sorted(unknown, key=lambda r: r.name.casefold())
        return sorted(records, key=lambda r: ((self._metadata(r).author or "~").casefold() if order == "Author" else "", r.name.casefold()))

    def _metadata(self, record: MapRecord):
        if record.variant_id not in self.metadata_cache:
            self.metadata_cache[record.variant_id] = self.app.map_metadata(record)
        return self.metadata_cache[record.variant_id]

    def _search_changed(self) -> None:
        if self.closed:
            return
        self._cancel_search_refresh()
        generation = self._search_refresh_generation
        self._search_refresh_after_id = self.root.after(
            200, lambda: self._run_search_refresh(generation))

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
        count = f"{len(self.remote_records)} maps online" if self.collection_loaded else "Internet Archive"
        ttk.Label(top, text=count, foreground=self.secondary).pack(side="left", padx=(12, 0))
        actions = ttk.Frame(self.content, padding=(24, 0, 24, 12))
        actions.pack(fill="x")
        self.download_button = ttk.Button(actions, text="Download & Import Selected", command=self._download_selected)
        self.download_button.pack(side="left")
        self.bulk_button = ttk.Button(actions, text="Download & Import All…", command=self._download_all)
        self.bulk_button.pack(side="left", padx=8)
        ttk.Button(actions, text="Refresh", command=self._refresh_collection).pack(side="left")
        ttk.Button(actions, text="Check Map Updates", command=self._check_map_updates).pack(side="left", padx=8)
        ttk.Button(actions, text="Collection Source", command=lambda: webbrowser.open(
            "https://archive.org/details/sid-meiers-railroads-custom-maps-collection")).pack(side="right")
        note = ("Download & Import All verifies archives in parallel, then prepares each map "
                "in your private library. Use Command-click or Shift-click to select several maps.")
        ttk.Label(self.content, text=note, padding=(24, 0, 24, 12),
                  foreground=self.secondary, wraplength=680).pack(fill="x")
        self.collection_detail = tk.StringVar(value="Choose a map to see its archive identity.")
        ttk.Label(self.content, textvariable=self.collection_detail, padding=(24, 0, 24, 12),
                  foreground=self.secondary, wraplength=680).pack(fill="x")
        frame = ttk.Frame(self.content, padding=(24, 0, 24, 20))
        frame.pack(fill="both", expand=True)
        self.collection_list = ttk.Treeview(frame, columns=("map", "progress", "size"), show="headings", selectmode="extended")
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
        self._collection_rows()
        self._controls()
        if not self.collection_loaded and not self.busy:
            self._refresh_collection()

    def _collection_rows(self) -> None:
        if not hasattr(self, "collection_list") or not self.collection_list.winfo_exists():
            return
        selected_names = {item.name for item in self.selected_remotes}
        for child in self.collection_list.get_children():
            self.collection_list.delete(child)
        self.remote_rows.clear()
        self.imported_names = {r.archive_filename for r in self._records()}
        query = self.search.get().strip().casefold()
        for index, item in enumerate(self.remote_records):
            if query not in item.name.casefold():
                continue
            key = str(index)
            self.remote_rows[key] = item
            size = f"{item.size / (1024 * 1024):.1f} MB"
            self.collection_list.insert("", "end", iid=key,
                                        values=(_display_name(item.name[:-3]),
                                                self._download_label(item.name), size))
        keys = [key for key, item in self.remote_rows.items() if item.name in selected_names]
        self.collection_list.selection_set(keys)
        self._collection_selected(None)
        self._controls()

    def _collection_selected(self, _event: tk.Event) -> None:
        selection = self.collection_list.selection()
        self.selected_remotes = tuple(self.remote_rows[key] for key in selection if key in self.remote_rows)
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
                f"{self._download_label(self.selected_remote.name)}{detail}")
        self._controls()

    def _download_label(self, name: str) -> str:
        state, count, total, _ = self.download_states.get(name, ("Imported" if name in self.imported_names else "Ready", 0, 0, ""))
        if state == "Downloading" and total:
            return f"Downloading {min(100, count * 100 // total)}%"
        return state

    def _download_event(self, record: RemoteMap, state: str, count: int,
                        total: int, detail: str) -> None:
        self.download_events.put((record, state, count, total, detail))

    def _update_download_rows(self) -> None:
        try:
            while True:
                record, state, count, total, detail = self.download_events.get_nowait()
                previous = self.download_states.get(record.name)
                if state in ("Imported", "Failed", "Canceled") and (not previous or previous[0] != state):
                    self._log(record.name, state, detail)
                self.download_states[record.name] = (state, count, total, detail)
                if self.view == "collection" and hasattr(self, "collection_list") and self.collection_list.winfo_exists():
                    key = next((key for key, item in self.remote_rows.items() if item.name == record.name), None)
                    if key is not None:
                        self.collection_list.set(key, "progress", self._download_label(record.name))
                    if self.selected_remote == record:
                        self._collection_selected(None)
        except Empty:
            pass

    def _refresh_collection(self) -> None:
        def operation():
            records = fetch_catalogue()
            if self.app:
                self.app.match_collection_sources(records)
            try:
                self.community_index = fetch_community_index(self.update_library / "community-index.json")
                self.community_notice = ""
            except CommunityError:
                self.community_notice = " Community refresh unavailable; using any saved community information."
            return records
        self._submit("Loading the Internet Archive map collection…",
                     operation, self._collection_loaded)

    def _collection_loaded(self, result: object) -> None:
        assert isinstance(result, tuple)
        self.remote_records = result
        self.collection_loaded = True
        if self.view == "collection":
            self.status.set(f"{len(self.remote_records)} maps available in the collection." + getattr(self, "community_notice", ""))
            self._show("collection")

    def _download_selected(self) -> None:
        if self.app is None or self.selected_remote is None:
            return
        if len(self.selected_remotes) > 1:
            self._download_all(self.selected_remotes)
            return
        record = self.selected_remote
        def operation() -> MapRecord:
            try:
                path = download_map(record, self.app.library / "downloads",
                                    progress=lambda count, total: self._download_event(
                                        record, "Downloading", count, total, ""))
                self._download_event(record, "Archive verified", record.size, record.size, "")
                result = self.app.import_archive(path, original_filename=record.name, remote=record)
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
        try:
            self.app._stopped()
        except Exception as exc:
            self.status.set("Quit Railroads before importing the collection: " + str(exc))
            return
        records = selected if selected is not None else self.remote_records
        total = sum(record.size for record in records)
        if not messagebox.askyesno(
            "Download and import all maps",
            f"Download {len(records)} {'selected' if selected else 'collection'} map archives (up to {total / (1024 ** 3):.2f} GiB) "
            "and import them into your private library? Four downloads can run at once; imports are checked "
            "one at a time. This also needs space for extracted map files. Maps will not be activated.",
            parent=self.root,
        ):
            return
        self.bulk_cancel.clear()
        self.bulk_active = True
        for record in records:
            self.download_states[record.name] = ("Queued", 0, record.size, "")
        self._collection_rows()
        self._submit(f"Downloading and importing {len(records)} maps with four parallel downloads…",
                     lambda: download_all_maps(records, self.app.library / "downloads",
                                               self._download_event, self.bulk_cancel,
                                               importer=lambda record, path: self.app.import_archive(
                                                   path, original_filename=record.name, remote=record)),
                     self._download_all_finished)

    def _download_all_finished(self, result: object) -> None:
        completed, failed, canceled = result
        self.bulk_active = False
        self.status.set(f"Bulk import finished: {completed} imported, {failed} failed, {canceled} canceled. "
                        "Choose a map in Map Library to test it.")
        if self.view == "maps":
            self._show("maps")
        self._controls()

    def _resize_gallery(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)
        columns = max(1, (event.width - 34) // 206)
        if columns != self.columns:
            self.columns = columns
            self._render_gallery()

    def _scroll_gallery(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _art(self, record: MapRecord) -> Optional[tk.PhotoImage]:
        if record.variant_id in self.images:
            return self.images[record.variant_id]
        if self.app is None:
            return None
        source = self.app.map_icon(record)
        if source is None:
            return None
        try:
            original = tk.PhotoImage(file=str(source))
            factor = max(1, math.ceil(max(original.width(), original.height()) / 184))
            photo = original.subsample(factor)
            self.images[record.variant_id] = photo
            return photo
        except tk.TclError:
            return None

    def _render_gallery(self) -> None:
        if not hasattr(self, "grid_frame") or not self.grid_frame.winfo_exists():
            return
        previous = self.canvas.yview()[0] if self.canvas.yview() else 0
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.cards = {}
        records = self._filtered()
        if not records:
            message = "Import a map archive to start your library." if not self.search.get().strip() else "No matching maps."
            ttk.Label(self.grid_frame, text=message, padding=24, foreground=self.secondary).grid(row=0, column=0, sticky="w")
        for index, record in enumerate(records):
            selected = record.profile_id == self.selected_profile
            border = self.accent if selected else self.border
            card = tk.Frame(self.grid_frame, width=190, height=305, background=self.surface,
                            highlightbackground=border, highlightthickness=2 if selected else 1,
                            takefocus=True)
            card.grid(row=index // max(1, self.columns), column=index % max(1, self.columns),
                      padx=8, pady=8, sticky="n")
            card.pack_propagate(False)
            self.cards[record.profile_id] = card
            artwork = tk.Canvas(card, width=184, height=183, background=self.surface, highlightthickness=0)
            artwork.pack(fill="x")
            photo = self._art(record)
            if photo is None:
                artwork.create_text(92, 91, text="▦", fill=self.secondary, font=("Helvetica Neue", 34))
            else:
                artwork.create_image(92, 91, image=photo)
            label = tk.Label(card, text=_display_name(record.name), background=self.surface,
                             foreground=self.text, font=("Helvetica Neue", 12, "bold"),
                             anchor="nw", justify="left", height=2, wraplength=174)
            label.pack(fill="x", padx=10)
            state = self._verification_text(record)
            badge = tk.Label(card, text=state, background=self.surface, foreground=self.secondary,
                             font=("Helvetica Neue", 10), anchor="w")
            badge.pack(fill="x", padx=10, pady=(2, 0))
            metadata = self._metadata(record)
            byline = tk.Label(card, text=f"{metadata.author or 'Author not provided'}\nCreated {metadata.created or 'date not provided'}",
                              background=self.surface, foreground=self.secondary, anchor="w", justify="left",
                              font=("Helvetica Neue", 10), wraplength=174)
            byline.pack(fill="x", padx=10, pady=(3, 0))
            for widget in (card, artwork, label, badge, byline):
                widget.bind("<Button-1>", lambda _, item=record: self._select(item))
                widget.bind("<Double-Button-1>", lambda _, item=record: self._map_details(item))
                widget.bind("<MouseWheel>", self._scroll_gallery)
            card.bind("<Return>", lambda _, item=record: self._select(item))
            card.bind("<space>", lambda _, item=record: self._select(item))
        self.grid_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(previous)
        self._selection_details()

    def _select(self, record: MapRecord) -> None:
        self.selected_profile = record.profile_id
        for profile_id, card in self.cards.items():
            selected = profile_id == self.selected_profile
            card.configure(highlightbackground=self.accent if selected else self.border,
                           highlightthickness=2 if selected else 1)
        self._selection_details()

    def _selection_details(self) -> None:
        record = next((item for item in self._records() if item.profile_id == self.selected_profile), None)
        if record is None:
            self.detail.set("Choose a map to see its scenario and source details.")
        else:
            info = self._metadata(record)
            self.detail.set(f"{_display_name(record.name)} · {record.source_label}\nCreated: {info.created or 'Not provided'} · Updated: {info.updated or 'Not provided'} · {self._verification_text(record)}")
        self._controls()

    def _selected_details(self) -> None:
        record = next((r for r in self._records() if r.profile_id == self.selected_profile), None)
        if record:
            self._map_details(record)

    def _stop_speech(self) -> None:
        if self.speech_process is not None and self.speech_process.poll() is None:
            self.speech_process.terminate()
        self.speech_process = None

    def _read_briefing(self, text: str) -> None:
        self._stop_speech()
        try:
            self.speech_process = speak(text, self.language_preferences.voice, available_voices=self.voices or None)
        except Exception as exc:
            messagebox.showerror(tr("Read Briefing Aloud"), str(exc), parent=self.root)

    def _map_details(self, record: MapRecord) -> None:
        metadata = self._metadata(record)
        verification = None
        verification_error = ""
        try:
            verification = self.app.verification_for(record)
            verification_status = verification.status if verification else "Not verified"
        except Exception as exc:
            verification_error = "Gameplay record needs inspection: " + str(exc)
            self.status.set(verification_error)
            verification_status = "Needs inspection"
        popup = tk.Toplevel(self.root)
        popup.title("Map Details — " + _display_name(record.name))
        popup.geometry("860x770")
        popup.minsize(760, 720)
        frame = ttk.Frame(popup, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=_display_name(record.name) if " — Experimental " in record.name else (metadata.name or _display_name(record.name)), font=("Helvetica Neue", 20, "bold"), wraplength=780).pack(anchor="w")
        rows = [("Author", metadata.author), ("Modified by", metadata.modified_by),
                ("Version", metadata.version), ("Created", metadata.created), ("Updated", metadata.updated),
                ("Map type", metadata.map_type), ("Archive source", record.source_label),
                ("Archive file modified", record.archive_modified), ("Mac testing", verification_status)]
        community = self.community_index.get(record.archive_filename or record.name + ".7z")
        facts = ttk.Frame(frame, padding=(0, 14))
        facts.pack(fill="x")
        for row, (label, value) in enumerate(rows):
            ttk.Label(facts, text=label, foreground=self.secondary).grid(row=row, column=0, sticky="nw", padx=(0, 18), pady=2)
            ttk.Label(facts, text=value or "Not provided", wraplength=510).grid(row=row, column=1, sticky="w", pady=2)
        ttk.Label(frame, text="Created and updated dates are declared by the map package; archive dates describe the download file.", foreground=self.secondary, wraplength=680).pack(anchor="w")
        links = ttk.Frame(frame, padding=(0, 10))
        links.pack(fill="x")
        urls = ([record.source_url] if record.source_url else []) + list(metadata.source_urls)
        for i, url in enumerate(dict.fromkeys(urls)):
            if safe_source_url(url):
                ttk.Button(links, text="Open Archive Source" if i == 0 and record.source_url else "Author’s Source", command=lambda target=url: webbrowser.open(target)).pack(side="left", padx=(0, 8))
        if community and community.discussion_url:
            ttk.Button(links, text="Community Reviews & Rating", command=lambda: webbrowser.open(community.discussion_url)).pack(side="left")
        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        identity = f"Archive: {record.archive_filename or record.name}\nSHA-256: {record.archive_sha256}\nVariant: {record.variant_id}\n\n" + (f"{verification.status} · {verification.observed_at}\nReported by: {verification.reporter}\n{verification.scope}\n\n" + "\n".join(("✓ " if verification.checks[key] else "○ ") + label for key, label in MAC_TEST_LABELS.items()) + "\n\n" + verification.issue if verification else "No local gameplay result recorded.")
        if verification_error:
            identity = f"Archive: {record.archive_filename or record.name}\nSHA-256: {record.archive_sha256}\nVariant: {record.variant_id}\n\n{verification_error}"
        if verification and not verification.resources_sha256:
            identity += "\n\nHistorical result: resource timestamps were not recorded. Repeat the play test before marking this map Verified. Any reported issue is retained as a historical warning."
        community_text = (f"Upstream community report: {community.stability}\nMultiplayer: "
                          + ({True: "Reported supported", False: "Reported unsupported", None: "Not reported"}[community.multiplayer])
                          + "\n\nThese are community reports for the original map, not verification on the Mac edition. "
                          "Open Community Reviews & Rating to see current votes and discussion."
                          if community else "No community information cached for this archive. Refresh Collection to check the upstream index.")
        text_widgets = {}
        for title, content in (("Briefing", metadata.description or "No briefing provided."),
                               ("Original mapInfo", metadata.raw_text or "No mapInfo.txt provided."),
                               ("Community", community_text),
                               ("Mac Test & Identity", identity),
                               ("Import Checks", _checks_text(self.app.import_checks(record)))):
            tab = ttk.Frame(notebook)
            notebook.add(tab, text=tr(title))
            text = tk.Text(tab, wrap="word", font=("Helvetica Neue", 12), padx=12, pady=12, borderwidth=0)
            bar = ttk.Scrollbar(tab, command=text.yview)
            text.configure(yscrollcommand=bar.set)
            bar.pack(side="right", fill="y"); text.pack(fill="both", expand=True)
            text.insert("1.0", content); text.configure(state="disabled")
            text_widgets[title] = text
        def replace_text(title, content):
            widget = text_widgets[title]
            if widget.winfo_exists():
                widget.configure(state="normal"); widget.delete("1.0", "end")
                widget.insert("1.0", content); widget.configure(state="disabled")
        self._compatibility_tab(notebook, record, popup)
        if community and community.discussion_url:
            cache = self.update_library / "ratings" / (community.discussion_url.rsplit("/", 1)[-1] + ".json")
            def display_rating(rating):
                if not popup.winfo_exists(): return
                text = community_text + "\n\n"
                if rating is None:
                    text += "No rating cached. Use Refresh Rating to fetch public votes."
                else:
                    text += "Checked: " + rating.fetched_at + "\n"
                    if rating.approximate_stars is not None:
                        text += f"Approximate star average: {rating.approximate_stars:.1f} / 5\n"
                    if rating.total_votes is not None:
                        text += f"Total community votes: {rating.total_votes}\n"
                    text += "\n".join(f"{option.label}: {option.percentage:g}%" for option in rating.options)
                    text += "\n\n" + rating.detail
                replace_text("Community", text)
            display_rating(read_cached_rating(community.discussion_url, cache))
            ttk.Button(links, text="Refresh Rating", command=lambda: self._submit("Refreshing public community votes…", lambda: fetch_rating(community.discussion_url, cache), display_rating)).pack(side="left", padx=8)
        actions = ttk.Frame(frame, padding=(0, 6))
        actions.pack(side="bottom", fill="x", before=notebook)
        ttk.Button(actions, text="Record Mac Test…", command=lambda: self._record_mac_test(record, popup)).pack(side="left")
        ttk.Button(actions, text="Run Import Checks", command=lambda: self._submit(
            "Checking map XML and file references…", lambda: self.app.import_checks(record, refresh=True),
            lambda report: replace_text("Import Checks", _checks_text(report)))).pack(side="left", padx=8)
        ttk.Button(actions, text="Switch to Original Game", command=lambda: self._submit(
            "Switching to Original Game…", lambda: self.app.activate(ORIGINAL),
            lambda _: self.status.set("Original Game is active. The map can now be removed."))).pack(side="left")
        ttk.Button(actions, text="Remove Map…", command=lambda: self._remove_map(record, popup)).pack(side="right")
        translation = ttk.Frame(frame, padding=(0, 8))
        translation.pack(side="bottom", fill="x", before=notebook)
        source = tk.StringVar(value=LANGUAGES['en'])
        target = tk.StringVar(value=LANGUAGES[self.language_preferences.language])
        ttk.Label(translation, text="From").pack(side="left")
        ttk.Combobox(translation, textvariable=source, values=tuple(LANGUAGES.values()), state="readonly", width=12).pack(side="left", padx=4)
        ttk.Label(translation, text="To").pack(side="left")
        ttk.Combobox(translation, textvariable=target, values=tuple(LANGUAGES.values()), state="readonly", width=12).pack(side="left", padx=4)
        def translate():
            source_code = next(k for k, v in LANGUAGES.items() if v == source.get())
            target_code = next(k for k, v in LANGUAGES.items() if v == target.get())
            self._submit("Translating briefing with Apple’s installed languages…", lambda: translate_briefing(metadata.description, target_code, source_code), lambda result: replace_text("Briefing", result))
        ttk.Button(translation, text="Translate Briefing", command=translate).pack(side="left", padx=4)
        ttk.Button(translation, text="Show Original", command=lambda: replace_text("Briefing", metadata.description or "No briefing provided.")).pack(side="left", padx=4)
        controls = ttk.Frame(frame, padding=(0, 12, 0, 0))
        controls.pack(side="bottom", fill="x", before=notebook)
        ttk.Button(controls, text="Read Briefing Aloud", command=lambda: self._read_briefing(text_widgets["Briefing"].get("1.0", "end-1c"))).pack(side="left")
        ttk.Button(controls, text="Stop Reading", command=self._stop_speech).pack(side="left", padx=8)
        ttk.Button(controls, text="Experimental Editions…", command=lambda: self._edition_options(record)).pack(side="left", padx=4)
        def close():
            self._stop_speech(); popup.destroy()
        ttk.Button(controls, text="Close", command=close).pack(side="right")
        popup.protocol("WM_DELETE_WINDOW", close)
        self._localize_widgets(popup)

    def _compatibility_tab(self, notebook, record, details):
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text=tr("Compatibility"))
        try:
            choices = self.app.compatibility_recipes(record)
        except Exception as exc:
            ttk.Label(tab, text="Compatibility options need inspection: " + str(exc),
                      wraplength=650).pack(anchor="w")
            return
        if not choices:
            text = ("This is a compiled compatibility edition. Use Mac Test & Identity "
                    "to see observations for this exact edition."
                    if getattr(record, "recipe_receipt_sha256", "") else
                    "No reviewed repair recipe is available for this edition. "
                    "A map can still work without a repair. Import checks and Mac "
                    "test results describe what has been checked so far.")
            ttk.Label(tab, text=text, wraplength=650).pack(anchor="w")
            return
        chooser = ttk.Combobox(tab, state="readonly",
                               values=tuple(choice.title for choice in choices))
        chooser.current(0)
        chooser.pack(fill="x", pady=(0, 8))
        def choice():
            return choices[chooser.current() if chooser.current() >= 0 else 0]
        ttk.Button(tab, text="Create Compatibility Edition", command=lambda:
                   self._create_recipe(record, choice().recipe_id, details)).pack(side="bottom", anchor="w", pady=(8, 0))
        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True)
        text = tk.Text(body, wrap="word", font=("Helvetica Neue", 12), borderwidth=0,
                       height=5, padx=6, pady=6)
        bar = ttk.Scrollbar(body, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        def describe(_event=None):
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", choice().description + "\n\nCreates a separate edition with its own saves. "
                        "Your original map and saves are kept. If this exact edition already exists, "
                        "it is selected with its existing saves. Files are checked before creation; "
                        "an unsupported installation will be refused.")
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
            self.status.set("Compatibility edition ready. Select Play to test it. "
                            "Your original map and saves are preserved; gameplay remains unverified.")
        self._submit("Checking files and preparing a compatibility edition…",
                     lambda: self.app.create_recipe_edition(record, recipe_id), completed)

    def _record_mac_test(self, record: MapRecord, details) -> None:
        if self.busy or self.app is None:
            return
        popup = tk.Toplevel(details)
        popup.title("Record Mac Test")
        popup.geometry("640x680")
        popup.minsize(610, 650)
        frame = ttk.Frame(popup, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=_display_name(record.name), font=("Helvetica Neue", 17, "bold"), wraplength=570).pack(anchor="w")
        ttk.Label(frame, text="Play this map, quit Railroads, then record your result before switching maps. Check only what you personally tested.", wraplength=570).pack(anchor="w", pady=12)
        checks = {key: tk.BooleanVar(value=False) for key in CHECKS}
        for key, label in MAC_TEST_LABELS.items():
            ttk.Checkbutton(frame, text=label, variable=checks[key]).pack(anchor="w", pady=3)
        ttk.Label(frame, text="Test scope / duration (required)").pack(anchor="w", pady=(16, 4))
        scope = tk.Text(frame, height=3, wrap="word", borderwidth=1, relief="solid", highlightthickness=1, highlightbackground="#666666", padx=6, pady=6)
        scope.pack(fill="x")
        ttk.Label(frame, text="Example: 20 minutes; built track, stations and a train; tested a bridge.", wraplength=570).pack(anchor="w")
        ttk.Label(frame, text="Crash or other issue (leave blank if none observed)").pack(anchor="w", pady=(14, 4))
        issue = tk.Text(frame, height=3, wrap="word", borderwidth=1, relief="solid", highlightthickness=1, highlightbackground="#666666", padx=6, pady=6)
        issue.pack(fill="x")
        ttk.Label(frame, text="All six checks and no reported issue earn Verified for these exact map files and game build. Partial tests stay Not verified; an issue becomes Known issue. Short tests do not prove a full campaign.", wraplength=570).pack(anchor="w", pady=14)
        controls = ttk.Frame(frame)
        controls.pack(side="bottom", fill="x")
        def finished(result):
            if popup.winfo_exists(): popup.destroy()
            if details.winfo_exists(): details.destroy()
            self.status.set("Mac test recorded: " + result.status)
            self._show("maps")
        def save():
            observation = scope.get("1.0", "end-1c").strip()
            problem = issue.get("1.0", "end-1c").strip()
            if not observation or len(observation) > 500 or len(problem) > 500:
                messagebox.showerror("Test details", "Enter a test scope. Scope and issue must each be 500 characters or fewer.", parent=popup)
                return
            values = {key: variable.get() for key, variable in checks.items()}
            self._submit("Recording your Mac play test…", lambda: self.app.record_gameplay(
                record, datetime.now().astimezone().isoformat(), getpass.getuser(), values, observation, problem), finished)
        ttk.Button(controls, text="Cancel", command=popup.destroy).pack(side="right")
        ttk.Button(controls, text="Save Test Result", command=save).pack(side="right", padx=8)

    def _remove_map(self, record: MapRecord, details) -> None:
        if self.busy or self.app is None:
            return
        if self.app.profiles._state()["active"] == record.profile_id:
            messagebox.showinfo("Map is active", "Quit Railroads, then use Switch to Original Game in Map Details. You can remove this map afterward.", parent=details)
            return
        if not messagebox.askyesno("Remove map?", "Remove “" + _display_name(record.name) + "” and its launcher-owned downloaded archive?\n\nSaved games will be kept and restored when you re-import this exact map version. Archives used by another edition will stay. Your source files outside the launcher library will stay.", parent=details, default="no"):
            return
        def finished(saved_games):
            if details.winfo_exists(): details.destroy()
            if self.selected_profile == record.profile_id: self.selected_profile = None
            self.download_states.clear()
            self.status.set("Map and unshared archives removed. Saved games kept for exact-version re-import.")
            self._show("maps")
        self._submit("Removing map and keeping saved games…", lambda: self.app.remove_map(record), finished)

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
        ttk.Label(frame, text="Play the stock Steam profile with its own saves. Your custom maps and their saves stay in separate profiles.",
                  wraplength=560).pack(anchor="w", pady=(12, 20))
        self.original_button = ttk.Button(frame, text="Play Original Game", command=lambda: self._play(ORIGINAL))
        self.original_button.pack(anchor="w")
        self._controls()

    def _setup_view(self) -> None:
        frame = ttk.Frame(self.content, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Setup", font=("Helvetica Neue", 21, "bold")).pack(anchor="w")
        if self.app is not None and self.app.profiles.state_file.exists():
            ttk.Label(frame, text="Your game is connected. Original Game and its saves are preserved.", wraplength=590).pack(anchor="w", pady=(14, 0))
        else:
            ttk.Label(frame, text="Connect your Steam game to start adding maps. Your original game profile and existing saves will be preserved.", wraplength=590).pack(anchor="w", pady=(14, 14))
            self.setup_button = ttk.Button(frame, text="Get Started", command=self.setup)
            self.setup_button.pack(anchor="w")
        ttk.Label(frame, text="The launcher preserves each source archive and keeps future saves with the selected map.",
                  wraplength=590, foreground=self.secondary).pack(anchor="w", pady=(20, 0))
        ttk.Separator(frame).pack(fill="x", pady=(24, 18))
        ttk.Label(frame, text="App Updates", font=("Helvetica Neue", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text=f"Installed launcher version {APP_VERSION}. Releases come only from the Craig-Franklin personal fork.",
                  foreground=self.secondary, wraplength=590).pack(anchor="w", pady=(6, 10))
        ttk.Checkbutton(frame, text="Automatically install verified updates when I quit the launcher",
                        variable=self.auto_updates, command=self._save_update_setting).pack(anchor="w")
        ttk.Label(frame, textvariable=self.update_status, foreground=self.secondary,
                  wraplength=590).pack(anchor="w", pady=(8, 10))
        buttons = ttk.Frame(frame)
        buttons.pack(anchor="w")
        self.check_update_button = ttk.Button(buttons, text="Check for Updates", command=self.check_for_updates)
        self.check_update_button.pack(side="left")
        self.install_update_button = ttk.Button(buttons, text="Download & Install Update…",
                                                command=self._download_update)
        self.install_update_button.pack(side="left", padx=(8, 0))
        self._controls()

    def _localize_widgets(self, widget) -> None:
        for child in widget.winfo_children():
            try:
                if "text" in child.keys() and ("textvariable" not in child.keys() or not child.cget("textvariable")):
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
        ttk.Label(frame, text="Recent launcher operations, retained locally between sessions.").pack(anchor="w", pady=8)
        actions = ttk.Frame(frame); actions.pack(fill="x", pady=(0, 8))
        query = tk.StringVar()
        ttk.Entry(actions, textvariable=query).pack(side="left", fill="x", expand=True)
        output = tk.Text(frame, wrap="word", padx=10, pady=10)
        bar = ttk.Scrollbar(frame, command=output.yview)
        output.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y"); output.pack(fill="both", expand=True)
        def refresh(*_):
            try:
                records = self.activity.records()
                needle = query.get().casefold()
                lines = [f"{r['time']}  ·  {r['state']}  ·  {r['action']}\n{r['detail']}" for r in reversed(records)]
                content = "\n\n".join(line for line in lines if needle in line.casefold()) or "No matching activity yet."
            except Exception as exc:
                content = "Activity history needs inspection: " + str(exc)
            output.configure(state="normal"); output.delete("1.0", "end")
            output.insert("1.0", content); output.configure(state="disabled")
        ttk.Button(actions, text="Refresh", command=refresh).pack(side="left", padx=8)
        query.trace_add("write", refresh); refresh()

    def _preferences_view(self) -> None:
        frame = ttk.Frame(self.content, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Language & Voice", font=("Helvetica Neue", 21, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Interface language").pack(anchor="w", pady=(20, 5))
        language = tk.StringVar(value=LANGUAGES[self.language_preferences.language])
        choice = ttk.Combobox(frame, textvariable=language, values=tuple(LANGUAGES.values()), state="readonly", width=32)
        choice.pack(anchor="w")
        def changed(_=None):
            code = next(k for k, value in LANGUAGES.items() if value == language.get())
            try:
                self.language_preferences.save(code, self.language_preferences.voice)
                selected_sort, selected_filter = self.sort_order.get(), self.map_filter.get()
                set_language(code)
                self.sort_order.set(selected_sort); self.map_filter.set(selected_filter)
                self._menu(); self._show("preferences")
            except Exception as exc:
                messagebox.showerror("Language", str(exc), parent=self.root)
        choice.bind("<<ComboboxSelected>>", changed)
        ttk.Label(frame, text="Voice").pack(anchor="w", pady=(20, 5))
        labels = {f"{voice.name} · {voice.language}": voice.identifier for voice in self.voices}
        default = tr("System default")
        selected = next((label for label, identifier in labels.items() if identifier == self.language_preferences.voice), default)
        voice_choice = ttk.Combobox(frame, values=(default, *labels), state="readonly", width=48)
        voice_choice.set(selected); voice_choice.pack(anchor="w")
        def voice_changed(_=None):
            try:
                self.language_preferences.save(self.language_preferences.language, labels.get(voice_choice.get(), ""))
            except Exception as exc:
                messagebox.showerror("Voice", str(exc), parent=self.root)
        voice_choice.bind("<<ComboboxSelected>>", voice_changed)
        buttons = ttk.Frame(frame); buttons.pack(anchor="w", pady=12)
        def loaded(result):
            self.voices = result
            self.status.set(f"{len(self.voices)} macOS voices available.")
            if self.view == "preferences": self._show("preferences")
        ttk.Button(buttons, text="Refresh Voices", command=lambda: self._submit("Loading macOS voices…", list_voices, loaded)).pack(side="left")
        ttk.Button(buttons, text="Manage Voices…", command=lambda: self._submit("Opening macOS voice settings…", open_voice_settings)).pack(side="left", padx=8)
        ttk.Separator(frame).pack(fill="x", pady=20)
        ttk.Label(frame, text="Briefing translation uses Apple’s installed language models on macOS 26 or later. Original map text is preserved.", wraplength=610).pack(anchor="w")
        ttk.Button(frame, text="Translation Languages…", command=lambda: self._submit("Opening translation settings…", open_translation_settings)).pack(anchor="w", pady=12)
        if not self.voices and not self.busy:
            self._submit("Loading macOS voices…", list_voices, loaded)

    def _check_map_updates(self) -> None:
        def operation():
            remotes = fetch_catalogue()
            if self.app: self.app.match_collection_sources(remotes)
            return remotes, find_map_updates(self._records(), remotes)
        def finished(result):
            self.remote_records, candidates = result
            self.collection_loaded = True
            self._show_map_updates(candidates)
        self._submit("Checking for map updates…", operation, finished)

    def _show_map_updates(self, candidates) -> None:
        popup = tk.Toplevel(self.root); popup.title(tr("Map Updates")); popup.geometry("790x440")
        frame = ttk.Frame(popup, padding=18); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Updates are imported as separate editions. Existing maps and saves remain available.", wraplength=730).pack(anchor="w", pady=(0, 12))
        rows = ttk.Treeview(frame, columns=("old", "new", "kind"), show="headings", selectmode="extended")
        for key, label in (("old", "Installed archive"), ("new", "Available archive"), ("kind", "Change")):
            rows.heading(key, text=tr(label)); rows.column(key, width=300 if key != "kind" else 120)
        rows.pack(fill="both", expand=True)
        for i, item in enumerate(candidates):
            rows.insert("", "end", iid=str(i), values=(item.installed_filename, item.remote.name, "New version" if item.kind == "new_version" else "Revised archive"))
        note = "No newer matching versions found." if not candidates else "Select the updates to import."
        ttk.Label(frame, text=note).pack(anchor="w", pady=8)
        def import_selected():
            chosen = tuple({candidates[int(i)].remote.name: candidates[int(i)].remote for i in rows.selection()}.values())
            if chosen and not self.busy:
                popup.destroy(); self._download_all(chosen)
        ttk.Button(frame, text="Import Selected Updates", command=import_selected).pack(anchor="e")
        self._localize_widgets(popup)

    def _edition_options(self, record):
        popup = tk.Toplevel(self.root); popup.title(tr("Experimental Editions"))
        frame = ttk.Frame(popup, padding=24); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Create a separate edition of " + _display_name(record.name), wraplength=560).pack(anchor="w")
        editor = tk.BooleanVar(); difficulty = tk.BooleanVar()
        ttk.Checkbutton(frame, text="Enable terrain editor — export workflow pending", variable=editor, state="disabled").pack(anchor="w", pady=(16, 5))
        ttk.Checkbutton(frame, text="Use Windows launcher custom difficulty levels", variable=difficulty).pack(anchor="w")
        ttk.Label(frame, text="Editor saving currently conflicts with protected map assets; that option stays unavailable until edited maps can be captured safely.\n\nCustom difficulty is experimental on the Mac edition. The new edition starts with empty saves and does not replace your original. Test a fresh scenario first. Custom difficulty is refused if the map supplies its own definitions.", wraplength=560).pack(anchor="w", pady=16)
        def create():
            if self.busy or not (editor.get() or difficulty.get()): return
            options = dict(editor=editor.get(), difficulty=difficulty.get())
            popup.destroy()
            self._submit("Preparing a separate experimental edition…", lambda: self.app.create_edition(record, **options), self._imported)
        ttk.Button(frame, text="Create Experimental Edition", command=create).pack(anchor="e")
        self._localize_widgets(popup)

    def _save_update_setting(self) -> None:
        try:
            self.update_preferences.set_automatic(bool(self.auto_updates.get()))
        except Exception as exc:
            self.auto_updates.set(False)
            self.update_status.set("Could not save update setting: " + str(exc))
            return
        if self.auto_updates.get():
            self.update_status.set("Automatic updates enabled. A verified release installs when you quit.")
            self.check_for_updates()
        else:
            if self.pending_update_manual:
                self.update_status.set("Automatic updates disabled. Your manually requested update will install when you quit.")
            elif self.pending_update is not None:
                self.update_status.set("Automatic updates disabled. The staged update will wait for you to re-enable it.")
            else:
                self.update_status.set("Automatic updates disabled. You can still check manually.")

    def check_for_updates(self) -> None:
        if self.busy or self.pending_update is not None:
            return
        self._submit("Checking the personal fork for an app update…",
                     lambda: fetch_latest_release(APP_VERSION), self._update_checked)

    def _update_checked(self, result: object) -> None:
        self.available_update = result if isinstance(result, Release) else None
        if self.available_update is None:
            self.update_status.set("No newer stable release is available.")
            self.status.set("Launcher is current; no newer stable release is available.")
        else:
            self.update_status.set(f"Version {self.available_update.version} is available from the personal fork.")
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
            self.update_status.set("Automatic installation requires a Developer ID signed launcher release.")
            return

        def operation() -> Path:
            installed = current_app_bundle()
            archive = download_release(release, self.update_library / "updates")
            staged = stage_release(archive, release, self.update_library, team)
            return prepare_install(staged, installed, release, self.update_library, team,
                                   APP_VERSION, manual_install=manual)

        self._submit(f"Downloading and verifying launcher {release.version}…",
                     operation, self._update_staged)

    def _update_staged(self, result: object) -> None:
        self.pending_update = Path(result)
        self.pending_update_manual = self.update_request_manual
        version = self.available_update.version if self.available_update else "new"
        if self.pending_update_manual or self.auto_updates.get():
            self.update_status.set(f"Version {version} is verified and ready. It will install when you quit this launcher.")
            self.status.set(f"Launcher update {version} ready; quit the launcher to install it.")
        else:
            self.update_status.set(f"Version {version} is verified and staged. Automatic installation is off.")
            self.status.set(f"Launcher update {version} is staged; enable automatic installation to apply it.")
        self._controls()

    def _controls(self) -> None:
        enrolled = self.app is not None and self.app.profiles.state_file.exists()
        if hasattr(self, "import_button") and self.import_button.winfo_exists():
            self.import_button.configure(state="normal" if enrolled and not self.busy else "disabled")
        if hasattr(self, "play_button") and self.play_button.winfo_exists():
            ready = enrolled and self.selected_profile is not None and not self.busy
            self.play_button.configure(state="normal" if ready else "disabled")
        if hasattr(self, "details_button") and self.details_button.winfo_exists():
            self.details_button.configure(state="normal" if self.selected_profile is not None else "disabled")
        if hasattr(self, "original_button") and self.original_button.winfo_exists():
            self.original_button.configure(state="normal" if enrolled and not self.busy else "disabled")
        if hasattr(self, "setup_button") and self.setup_button.winfo_exists():
            self.setup_button.configure(state="normal" if self.app is not None and not self.busy else "disabled")
        if hasattr(self, "welcome_retry_button") and self.welcome_retry_button.winfo_exists():
            self.welcome_retry_button.configure(state="normal" if not self.busy else "disabled")
        if hasattr(self, "download_button") and self.download_button.winfo_exists():
            ready = enrolled and self.selected_remote is not None and not self.busy
            self.download_button.configure(state="normal" if ready else "disabled", text=tr("Download & Import Selected"))
        if hasattr(self, "bulk_button") and self.bulk_button.winfo_exists():
            ready = ((self.bulk_active and not self.bulk_cancel.is_set())
                     or (enrolled and self.collection_loaded and not self.busy))
            self.bulk_button.configure(text=tr("Cancel Imports" if self.bulk_active else "Download & Import All…"),
                                       state="normal" if ready else "disabled")
        if hasattr(self, "check_update_button") and self.check_update_button.winfo_exists():
            self.check_update_button.configure(state="normal" if not self.busy else "disabled")
        if hasattr(self, "install_update_button") and self.install_update_button.winfo_exists():
            ready = self.available_update is not None and self.pending_update is None and not self.busy
            self.install_update_button.configure(state="normal" if ready else "disabled")

    def _submit(self, label: str, operation: Callable[[], object], finished: Optional[Callable[[object], None]] = None) -> None:
        if self.busy:
            return
        self.busy = True
        self._log(label, "Started")
        self.status.set(label)
        self._controls()

        def worker() -> None:
            try:
                value = operation()
                self._log(label, "Completed")
                self.results.put((True, value, finished))
            except Exception as exc:
                self._log(label, "Failed", str(exc))
                self.results.put((False, exc, None))

        Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        speech = self.speech_process
        if speech is not None and speech.done:
            self.speech_process = None
            if getattr(speech, "error", None):
                self.status.set("Speech stopped: " + speech.error)
                self._log("Read briefing", "Failed", speech.error)
        self._update_download_rows()
        try:
            while True:
                successful, value, finished = self.results.get_nowait()
                self.busy = False
                if successful:
                    if finished:
                        try:
                            finished(value)
                        except Exception as exc:
                            self._log("Display operation result", "Failed", str(exc))
                            self.status.set("Operation completed; displaying its result failed: " + str(exc))
                else:
                    self.bulk_active = False
                    self.status.set("Action stopped: " + str(value))
                    messagebox.showerror("Railroads launcher", str(value), parent=self.root)
                if self.closed:
                    return
                self._controls()
        except Empty:
            pass
        if self.close_when_idle and not self.busy and not self.bulk_active:
            self.close_when_idle = False
            self._close()
            if not self.root.winfo_exists():
                return
        self.root.after(100, self._poll)

    def choose_steam_library(self) -> None:
        location = filedialog.askdirectory(parent=self.root, title="Choose the steamapps folder, not Steam.app")
        if location:
            self._submit("Checking the selected Steam library…",
                         lambda: LauncherApplication.choose_steam_library(Path(location)), self._library_chosen)

    def _library_chosen(self, result: object) -> None:
        assert isinstance(result, LauncherApplication)
        self.app = result
        self.status.set("Steam Mac game found in the selected library.")
        self._show(self._initial_view() if self.view == "welcome" else self.view)

    def setup(self) -> None:
        if self.app is None or self.busy:
            return
        self._submit("Checking your game and preserving its existing saves…", self.app.setup,
                     lambda _: (self.status.set("You’re ready. Choose a map to download, or play Original Game."), self._show("collection")))

    def import_map(self) -> None:
        if self.app is None or not self.app.profiles.state_file.exists():
            return
        location = filedialog.askopenfilename(
            parent=self.root, title="Choose a custom map archive",
            filetypes=[("Map archives", "*.7z *.zip *.tar"), ("All files", "*")],
        )
        if location:
            self._submit("Inspecting and preparing " + Path(location).name + "…",
                         lambda: self.app.import_archive(Path(location)), self._imported)

    def _imported(self, result: object) -> None:
        assert isinstance(result, MapRecord)
        self.selected_profile = result.profile_id
        report = self.app.import_checks(result)
        findings = len(report["warnings"]) if report else 0
        self.status.set(result.name + (f" imported with {findings} static findings — see Map Details → Import Checks." if findings else " imported. Gameplay is not verified; see Map Details for import checks."))
        self._show("maps")

    def play_selected(self) -> None:
        if self.selected_profile is not None:
            self._play(self.selected_profile)

    def _play(self, profile_id: str) -> None:
        if self.app is None:
            return
        record = next((r for r in self._records() if r.profile_id == profile_id), None)
        if record:
            try:
                verification = self.app.verification_for(record)
            except Exception as exc:
                self.status.set("Map verification needs inspection; launch cancelled.")
                messagebox.showerror("Map verification needs inspection", str(exc), parent=self.root)
                return
            if verification and verification.issue and not messagebox.askyesno(
                    "Known map issue", verification.issue + "\n\nLaunch this map anyway?", parent=self.root):
                return
        self._submit("Switching profile and starting Railroads…",
                     lambda: self.app.play(profile_id),
                     lambda _: self.status.set("Railroads launched. Quit it before changing maps."))

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    LauncherWindow().run()


if __name__ == "__main__":
    main()
