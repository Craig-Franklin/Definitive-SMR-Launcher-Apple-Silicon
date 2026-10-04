"""Mac map gallery built from native Tk controls and local, user-owned map art."""
from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Callable, Optional
import math
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .activation import ORIGINAL
from .application import LauncherApplication, MapRecord
from .collection import RemoteMap, download_map, fetch_catalogue


def _display_name(name: str) -> str:
    return re.sub(r"\bv(\d+) (\d{2})\b", r"v\1.\2", name.replace("_", " "))


class LauncherWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Definitive SMR Launcher — Apple Silicon")
        self.root.geometry("1120x720")
        self.root.minsize(900, 590)
        self.app: Optional[LauncherApplication] = None
        self.busy = False
        self.results: Queue[tuple[bool, object, Optional[Callable[[object], None]]]] = Queue()
        self.view = "maps"
        self.selected_profile: Optional[str] = None
        self.remote_records: tuple[RemoteMap, ...] = ()
        self.remote_rows: dict[str, RemoteMap] = {}
        self.selected_remote: Optional[RemoteMap] = None
        self.collection_loaded = False
        self.search = tk.StringVar()
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
                self.status.set("Original Game is ready. Choose an imported map or play the stock game.")
            else:
                self.status.set("Steam Mac game found. Play and reload a stock save before first setup.")
        except Exception as exc:
            self.status.set("Steam Mac game unavailable: " + str(exc))
        self._show("maps")
        self.root.after(100, self._poll)

    def _menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Import Map…", command=self.import_map, accelerator="⌘O")
        file_menu.add_command(label="Choose Steam Library Folder…", command=self.choose_steam_library)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.root.destroy, accelerator="⌘Q")
        menu.add_cascade(label="File", menu=file_menu)
        self.root.configure(menu=menu)
        self.root.bind("<Command-o>", lambda _: self.import_map())
        self.root.bind("<Command-f>", lambda _: self.search_entry.focus_set())
        self.root.bind("<Command-q>", lambda _: self.root.destroy())

    def _layout(self) -> None:
        header = ttk.Frame(self.root, padding=(22, 14, 22, 12))
        header.pack(fill="x")
        ttk.Label(header, text="Definitive SMR", font=("Helvetica Neue", 20, "bold")).pack(side="left")
        ttk.Label(header, text="Apple Silicon", foreground=self.secondary).pack(side="left", padx=(10, 0))
        self.search_entry = ttk.Entry(header, textvariable=self.search, width=26)
        self.search_entry.pack(side="right")
        ttk.Label(header, text="Search maps", foreground=self.secondary).pack(side="right", padx=(0, 8))
        self.search.trace_add("write", lambda *_: self._search_changed())

        ttk.Separator(self.root).pack(fill="x")
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.sidebar = ttk.Frame(body, width=218, padding=(14, 18, 12, 16))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.nav_buttons: dict[str, ttk.Button] = {}
        for key, label in (("maps", "Map Library"), ("collection", "Collection"),
                           ("original", "Original Game"), ("setup", "Setup")):
            button = ttk.Button(self.sidebar, text=label, command=lambda destination=key: self._show(destination))
            button.pack(fill="x", pady=(0, 7))
            self.nav_buttons[key] = button
        ttk.Separator(self.sidebar).pack(fill="x", pady=(12, 14))
        ttk.Label(self.sidebar, text="Steam Mac edition", font=("Helvetica Neue", 12, "bold")).pack(anchor="w")
        self.steam_label = ttk.Label(self.sidebar, text="Checking…", wraplength=185, foreground=self.secondary)
        self.steam_label.pack(anchor="w", pady=(5, 12))

        ttk.Separator(body, orient="vertical").pack(side="left", fill="y")
        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)
        ttk.Separator(self.root).pack(fill="x")
        ttk.Label(self.root, textvariable=self.status, padding=(22, 8), foreground=self.secondary,
                  wraplength=900).pack(fill="x")

    def _clear_content(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self.columns = 0

    def _show(self, view: str) -> None:
        self.view = view
        self._clear_content()
        for key, button in self.nav_buttons.items():
            button.state(["disabled"] if key == view else ["!disabled"])
        self.steam_label.configure(text="Installed" if self.app else "Game not found")
        if view == "collection":
            self._collection_view()
        elif self.app is None:
            self._missing_game()
        elif view == "maps":
            self._maps_view()
        elif view == "original":
            self._original_view()
        else:
            self._setup_view()

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
        return sorted((item for item in self._records() if query in item.name.casefold()),
                      key=lambda item: item.name.casefold())

    def _search_changed(self) -> None:
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
        self.download_button = ttk.Button(top, text="Download & Import", command=self._download_selected)
        self.download_button.pack(side="right")
        ttk.Button(top, text="Refresh", command=self._refresh_collection).pack(side="right", padx=(0, 8))
        note = ("Browse the community collection. Downloaded archives are verified and kept "
                "in your private library. Gameplay remains unverified until tested.")
        ttk.Label(self.content, text=note, padding=(24, 0, 24, 12),
                  foreground=self.secondary, wraplength=680).pack(fill="x")
        self.collection_detail = tk.StringVar(value="Choose a map to see its archive identity.")
        ttk.Label(self.content, textvariable=self.collection_detail, padding=(24, 0, 24, 12),
                  foreground=self.secondary, wraplength=680).pack(fill="x")
        frame = ttk.Frame(self.content, padding=(24, 0, 24, 20))
        frame.pack(fill="both", expand=True)
        self.collection_list = ttk.Treeview(frame, columns=("map", "size"), show="headings", selectmode="browse")
        self.collection_list.heading("map", text="Map")
        self.collection_list.heading("size", text="Archive size")
        self.collection_list.column("map", width=520, stretch=True)
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
        for child in self.collection_list.get_children():
            self.collection_list.delete(child)
        self.remote_rows.clear()
        query = self.search.get().strip().casefold()
        for index, item in enumerate(self.remote_records):
            if query not in item.name.casefold():
                continue
            key = str(index)
            self.remote_rows[key] = item
            size = f"{item.size / (1024 * 1024):.1f} MB"
            self.collection_list.insert("", "end", iid=key,
                                        values=(_display_name(item.name[:-3]), size))
        if self.selected_remote:
            key = next((key for key, item in self.remote_rows.items()
                        if item == self.selected_remote), None)
            if key is not None:
                self.collection_list.selection_set(key)
            else:
                self.selected_remote = None
                self.collection_detail.set("Choose a map to see its archive identity.")
        self._controls()

    def _collection_selected(self, _event: tk.Event) -> None:
        selection = self.collection_list.selection()
        self.selected_remote = self.remote_rows.get(selection[0]) if selection else None
        if self.selected_remote is None:
            self.collection_detail.set("Choose a map to see its archive identity.")
        else:
            self.collection_detail.set(
                f"{_display_name(self.selected_remote.name[:-3])}  ·  "
                f"archive SHA-1 {self.selected_remote.sha1[:12]}  ·  Not verified"
            )
        self._controls()

    def _refresh_collection(self) -> None:
        self._submit("Loading the Internet Archive map collection…",
                     fetch_catalogue, self._collection_loaded)

    def _collection_loaded(self, result: object) -> None:
        assert isinstance(result, tuple)
        self.remote_records = result
        self.collection_loaded = True
        if self.view == "collection":
            self.status.set(f"{len(self.remote_records)} maps available in the collection.")
            self._show("collection")

    def _download_selected(self) -> None:
        if self.app is None or self.selected_remote is None:
            return
        record = self.selected_remote
        self._submit("Downloading and checking " + record.name + "…",
                     lambda: self.app.import_archive(
                         download_map(record, self.app.library / "downloads"),
                         original_filename=record.name),
                     self._imported)

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
        records = self._filtered()
        if not records:
            message = "Import a map archive to start your library." if not self.search.get().strip() else "No matching maps."
            ttk.Label(self.grid_frame, text=message, padding=24, foreground=self.secondary).grid(row=0, column=0, sticky="w")
        for index, record in enumerate(records):
            selected = record.profile_id == self.selected_profile
            border = self.accent if selected else self.border
            card = tk.Frame(self.grid_frame, width=190, height=268, background=self.surface,
                            highlightbackground=border, highlightthickness=2 if selected else 1,
                            takefocus=True)
            card.grid(row=index // max(1, self.columns), column=index % max(1, self.columns),
                      padx=8, pady=8, sticky="n")
            card.pack_propagate(False)
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
            state = "Not verified"
            badge = tk.Label(card, text=state, background=self.surface, foreground=self.secondary,
                             font=("Helvetica Neue", 10), anchor="w")
            badge.pack(fill="x", padx=10, pady=(2, 0))
            for widget in (card, artwork, label, badge):
                widget.bind("<Button-1>", lambda _, item=record: self._select(item))
                widget.bind("<MouseWheel>", self._scroll_gallery)
            card.bind("<Return>", lambda _, item=record: self._select(item))
            card.bind("<space>", lambda _, item=record: self._select(item))
        self.grid_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(previous)
        self._selection_details()

    def _select(self, record: MapRecord) -> None:
        self.selected_profile = record.profile_id
        self._render_gallery()

    def _selection_details(self) -> None:
        record = next((item for item in self._records() if item.profile_id == self.selected_profile), None)
        if record is None:
            self.detail.set("Choose a map to see its scenario and source details.")
        else:
            self.detail.set(f"{_display_name(record.name)}  ·  {len(record.scenarios)} scenario file(s)  ·  archive {record.archive_sha256[:12]}  ·  Not verified")
        self._controls()

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
        instructions = ("1. Install the Steam Mac edition on the default branch.\n"
                        "2. Play a stock scenario, save, quit, reopen, and load the save.\n"
                        "3. Quit Railroads, then set up the clean profile below.")
        ttk.Label(frame, text=instructions, wraplength=590, justify="left").pack(anchor="w", pady=(14, 20))
        if self.app is not None and self.app.profiles.state_file.exists():
            ttk.Label(frame, text="Clean game profile enrolled.").pack(anchor="w")
        else:
            self.setup_button = ttk.Button(frame, text="Set Up Clean Game", command=self.setup)
            self.setup_button.pack(anchor="w")
        ttk.Label(frame, text="The launcher preserves each source archive and keeps future saves with the selected map.",
                  wraplength=590, foreground=self.secondary).pack(anchor="w", pady=(20, 0))
        self._controls()

    def _controls(self) -> None:
        enrolled = self.app is not None and self.app.profiles.state_file.exists()
        if hasattr(self, "import_button") and self.import_button.winfo_exists():
            self.import_button.configure(state="normal" if enrolled and not self.busy else "disabled")
        if hasattr(self, "play_button") and self.play_button.winfo_exists():
            ready = enrolled and self.selected_profile is not None and not self.busy
            self.play_button.configure(state="normal" if ready else "disabled")
        if hasattr(self, "original_button") and self.original_button.winfo_exists():
            self.original_button.configure(state="normal" if enrolled and not self.busy else "disabled")
        if hasattr(self, "setup_button") and self.setup_button.winfo_exists():
            self.setup_button.configure(state="normal" if not self.busy else "disabled")
        if hasattr(self, "download_button") and self.download_button.winfo_exists():
            ready = enrolled and self.selected_remote is not None and not self.busy
            self.download_button.configure(state="normal" if ready else "disabled")

    def _submit(self, label: str, operation: Callable[[], object], finished: Optional[Callable[[object], None]] = None) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.set(label)
        self._controls()

        def worker() -> None:
            try:
                self.results.put((True, operation(), finished))
            except Exception as exc:
                self.results.put((False, exc, None))

        Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                successful, value, finished = self.results.get_nowait()
                self.busy = False
                if successful:
                    if finished:
                        finished(value)
                else:
                    self.status.set("Action stopped: " + str(value))
                    messagebox.showerror("Railroads launcher", str(value), parent=self.root)
                self._controls()
        except Empty:
            pass
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
        self._show(self.view)

    def setup(self) -> None:
        if self.app is None:
            return
        if not messagebox.askyesno(
            "Set up clean game",
            "Have you played the stock Steam Mac game, saved, quit, reopened, and loaded the save?\n\n"
            "Set Up preserves and enrolls the clean profile. Quit Railroads first.", parent=self.root,
        ):
            return
        self._submit("Preserving the clean game profile…", self.app.setup,
                     lambda _: (self.status.set("Original Game is ready."), self._show(self.view)))

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
        self.status.set(result.name + " imported. Static checks passed; gameplay is not verified.")
        self._show("maps")

    def play_selected(self) -> None:
        if self.selected_profile is not None:
            self._play(self.selected_profile)

    def _play(self, profile_id: str) -> None:
        if self.app is None:
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
