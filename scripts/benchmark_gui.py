#!/usr/bin/env python3
"""Opt-in native Tk performance fixtures; never opens a game or real library.

Run with an arm64 Python/Tk on macOS. Each sample gets a temporary HOME and
fresh process. --source-root can select an extracted baseline source tree.
These source/native timings exclude Python imports and packaged bootloader IO.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time


def child(source: Path, mode: str, count: int) -> dict:
    # Isolate before importing any launcher code. No installed app is opened.
    with tempfile.TemporaryDirectory(prefix="smr-native-benchmark-") as temporary:
        os.environ["HOME"] = temporary
        os.environ["TMPDIR"] = temporary
        os.environ["PYTHONNOUSERSITE"] = "1"
        sys.path.insert(0, str(source / "src"))
        from unittest.mock import patch
        from types import SimpleNamespace
        import tkinter as tk
        from smr_launcher.gui import LauncherWindow
        from smr_launcher.application import LauncherApplication, MapRecord

        def deny_external(event, args):
            if event in {"subprocess.Popen", "os.system", "os.posix_spawn",
                         "socket.connect", "socket.getaddrinfo"}:
                raise RuntimeError("Benchmark refused external operation: " + event)
        sys.addaudithook(deny_external)
        original_tk = tk.Tk
        clock = {"start": time.perf_counter(), "last_tick": None,
                 "max_gap_ms": 0.0, "max_gap_after_mapped_ms": 0.0,
                 "mapped_ms": None, "ready_ms": None}

        def elapsed():
            return (time.perf_counter() - clock["start"]) * 1000

        def create_root(*args, **kwargs):
            root = original_tk(*args, **kwargs)
            root.bind("<Map>", lambda event: clock.update(mapped_ms=elapsed())
                      if str(event.widget) == str(root) and clock["mapped_ms"] is None else None)
            def tick():
                now = time.perf_counter()
                if clock["last_tick"] is not None:
                    clock["max_gap_ms"] = max(clock["max_gap_ms"],
                                                (now - clock["last_tick"]) * 1000)
                    if clock["mapped_ms"] is not None:
                        clock["max_gap_after_mapped_ms"] = max(
                            clock["max_gap_after_mapped_ms"],
                            (now - max(clock["last_tick"],
                             clock["start"] + clock["mapped_ms"] / 1000)) * 1000)
                if clock["mapped_ms"] is None and root.winfo_viewable():
                    clock["mapped_ms"] = elapsed()
                clock["last_tick"] = now
                root.after(10, tick)
            # Include pre-mainloop synchronous work in the initial gap.
            clock["last_tick"] = clock["start"]
            root.after(10, tick)
            return root

        show = LauncherWindow._show
        def observed_show(window, view):
            result = show(window, view)
            if view == "welcome":
                clock["ready_ms"] = elapsed()
            return result

        def discovery():
            if mode == "startup":
                time.sleep(0.5)
            raise RuntimeError("Synthetic benchmark: no game installed")

        with patch("smr_launcher.gui.tk.Tk", side_effect=create_root), \
                patch.object(LauncherApplication, "discover", side_effect=discovery), \
                patch.object(LauncherWindow, "_show", observed_show):
            window = LauncherWindow()
            window.root.title("SMR synthetic native performance benchmark")
            deadline = time.perf_counter() + 15
            def wait_startup():
                if clock["ready_ms"] is not None:
                    window.root.quit()
                elif time.perf_counter() >= deadline:
                    clock["timed_out"] = True
                    window.root.quit()
                else:
                    window.root.after(5, wait_startup)
            window.root.after(5, wait_startup)
            window.root.mainloop()
            if clock.get("timed_out"):
                window._close()
                raise RuntimeError("Synthetic startup timed out")
            if mode == "startup":
                result = {"mode": mode, "injected_discovery_ms": 500,
                          **{k: clock[k] for k in ("mapped_ms", "ready_ms", "max_gap_ms", "max_gap_after_mapped_ms")}}
            else:
                root = Path(temporary)
                live = root / "live"
                live.mkdir()
                app = LauncherApplication(SimpleNamespace(profile_root=live,
                                          game_running=lambda: False), root / "library")
                app.profiles.state_file.parent.mkdir(parents=True, exist_ok=True)
                app.profiles.state_file.write_text("{}")
                records = []
                for index in range(count):
                    key = f"{index:032x}"
                    directory = app.imports / key
                    directory.mkdir(parents=True)
                    (directory / "mapInfo.txt").write_text(
                        f"Map Name: Synthetic {index}\nAuthor: Benchmark\n"
                        "Date Created: 2026/01/01\n\nSynthetic fixture only.")
                    records.append(MapRecord(name=f"Synthetic {index}",
                        archive_sha256=f"{index:064x}", variant_id=key,
                        profile_id=f"map-{key}", scenarios=(),
                        prepared_directory=key, game_executable_sha256="0" * 64,
                        imported_directory=key))
                app.catalogue_file.write_text(json.dumps(
                    {"schema": 1, "maps": [record.as_json() for record in records]}))
                window.app = app
                window.metadata_cache.clear()
                result = {"mode": mode, "maps": count, "no_icons_or_evidence": True,
                          "call_ms": None, "first_card_ms": None, "all_cards_ms": None}
                clock["max_gap_ms"] = 0.0
                clock["last_tick"] = time.perf_counter()
                started = None
                def render():
                    nonlocal started
                    started = time.perf_counter()
                    window._show("maps")
                    result["call_ms"] = (time.perf_counter() - started) * 1000
                def inspect():
                    if started is not None:
                        cards = len(getattr(window, "cards", {}))
                        if cards and result["first_card_ms"] is None:
                            result["first_card_ms"] = (time.perf_counter() - started) * 1000
                        if cards == count:
                            window.root.update_idletasks()
                            result["all_cards_ms"] = (time.perf_counter() - started) * 1000
                            result["max_gap_ms"] = max(clock["max_gap_ms"],
                                (time.perf_counter() - clock["last_tick"]) * 1000)
                            window.root.quit()
                            return
                    if time.perf_counter() >= deadline:
                        window.root.quit()
                        return
                    window.root.after(5, inspect)
                window.root.after(1, render)
                window.root.after(5, inspect)
                window.root.mainloop()
                if result["all_cards_ms"] is None:
                    raise RuntimeError("Gallery did not finish")
            window._close()
            return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--mode", choices=("startup", "gallery"), default="gallery")
    parser.add_argument("--maps", type=int, default=250)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.samples < 1 or not 1 <= args.maps <= 2000:
        parser.error("samples must be positive and maps must be 1..2000")
    if args.child:
        print(json.dumps(child(args.source_root.resolve(), args.mode, args.maps)))
        return
    samples = []
    for _ in range(args.samples):
        output = subprocess.check_output([sys.executable, str(Path(__file__).resolve()),
            "--source-root", str(args.source_root.resolve()), "--mode", args.mode,
            "--maps", str(args.maps), "--child"], text=True, timeout=25)
        samples.append(json.loads(output))
    fields = [key for key, value in samples[0].items()
              if key.endswith("_ms") and key != "injected_discovery_ms"
              and isinstance(value, (float, int))]
    print(json.dumps({"method": "fresh process, isolated HOME, native Tk, imports excluded",
        "python": sys.version.split()[0], "n": args.samples, "samples": samples,
        "summary_ms": {key: {"min": min(row[key] for row in samples),
            "median": statistics.median(row[key] for row in samples),
            "max": max(row[key] for row in samples)} for key in fields}}, indent=2))


if __name__ == "__main__":
    main()
