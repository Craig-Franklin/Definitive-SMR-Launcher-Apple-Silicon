#!/usr/bin/env python3
"""Opt-in paired source/fake progress and local-cache timings.

Run from this checkout with Python/Tk installed; no native window is created.
Requires the pinned baseline commit in git history and the test fixture module.
Five progress events per each of 2,000 rows differ from the original analysis's
repeated final-row stress. Output JSON goes to stdout; no real library is read.
"""

import ast
import json
from pathlib import Path
from queue import Empty, Queue
import statistics
import subprocess
import sys
import tempfile
import time
from threading import Event
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from smr_launcher.gui import LauncherWindow
from smr_launcher.collection import RemoteMap, read_cached_catalogue, write_cached_catalogue
from test_gui_performance import window

BASE = "0fdd684c591f23b6a85ccf89af62fd2bfef87c70"
source = subprocess.check_output(
    ["git", "show", BASE + ":src/smr_launcher/gui.py"], cwd=ROOT, text=True
)
module = ast.parse(source)
cls = next(
    node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "LauncherWindow"
)
method = next(
    node
    for node in cls.body
    if isinstance(node, ast.FunctionDef) and node.name == "_update_download_rows"
)
ns = {"Empty": Empty}
exec(compile(ast.Module(body=[method], type_ignores=[]), "<baseline progress method>", "exec"), ns)


class Tree:
    def __init__(self):
        self.writes = 0

    def winfo_exists(self):
        return True

    def set(self, *args):
        self.writes += 1


records = tuple(RemoteMap(f"Map{i}.7z", 5, f"{i:040x}") for i in range(2000))
results = {
    "method": "synthetic 2000 rows / 10000 producer events; prefill excluded from drain timing; fake Treeview; 11 samples",
    "baseline_commit": BASE,
    "progress": {},
}
for name in ("baseline", "after"):
    total = []
    maximum = []
    producing = []
    retained = []
    writes = []
    for _ in range(11):
        w = window()
        w.collection_list = Tree()
        w.remote_rows = {str(i): r for i, r in enumerate(records)}
        w.remote_row_ids = {r.name: str(i) for i, r in enumerate(records)}
        if name == "baseline":
            w.download_events = Queue()
        begin = time.perf_counter()
        for count in range(1, 6):
            for record in records:
                w._download_event(record, "Downloading", count, 5, "")
        producing.append((time.perf_counter() - begin) * 1000)
        retained.append(w.download_events.qsize())
        durations = []
        while not w.download_events.empty():
            begin = time.perf_counter()
            (
                ns["_update_download_rows"]
                if name == "baseline"
                else LauncherWindow._update_download_rows
            )(w)
            durations.append((time.perf_counter() - begin) * 1000)
        total.append(sum(durations))
        maximum.append(max(durations))
        writes.append(w.collection_list.writes)
        assert len(w.download_states) == 2000
        assert all(state[1] == 5 for state in w.download_states.values())
    results["progress"][name] = dict(
        drain_ms=total,
        max_callback_ms=maximum,
        producing_ms=producing,
        retained_events=retained,
        row_writes=writes,
    )
with tempfile.TemporaryDirectory(prefix="smr-cache-synthetic-") as temporary:
    path = Path(temporary).resolve() / "catalogue.json"
    cached = records[:221]
    write_cached_catalogue(path, cached)
    samples = []
    for _ in range(11):
        begin = time.perf_counter()
        saved = read_cached_catalogue(path)
        samples.append((time.perf_counter() - begin) * 1000)
        assert saved.records == tuple(sorted(cached, key=lambda r: r.name.casefold()))
    results["cache"] = dict(
        maps=221, bytes=path.stat().st_size, read_validate_ms=samples, network_calls=0
    )


def spread(values):
    return dict(min=min(values), median=statistics.median(values), max=max(values))


results["summary"] = {
    name: {key: spread(value) for key, value in group.items() if key.endswith("_ms")}
    for name, group in results["progress"].items()
}
results["summary"]["cache_read_validate_ms"] = spread(results["cache"]["read_validate_ms"])
samples = []
publication_records = tuple(RemoteMap(f"Map{i}.7z", 5, f"{i:040x}") for i in range(221))
for _ in range(11):
    w = window()
    release = Event()
    provenance = Event()
    community = Event()
    cache = Event()
    fetched = []

    def fetch():
        fetched.append(time.perf_counter())
        return publication_records

    def matching(records):
        provenance.set()
        release.wait(2)

    def community_fetch(path):
        community.set()
        release.wait(2)
        return {}

    def cache_write(path, records):
        cache.set()
        release.wait(2)

    w.app.match_collection_sources.side_effect = matching
    w.app.catalogue.return_value = ()
    try:
        with (
            patch("smr_launcher.gui.fetch_catalogue", side_effect=fetch),
            patch("smr_launcher.gui.fetch_community_index", side_effect=community_fetch),
            patch("smr_launcher.gui.write_cached_catalogue", side_effect=cache_write),
        ):
            w._refresh_collection()
            # Deliberately exercise a nearly worst-case normal 100ms poll phase.
            time.sleep(0.1)
            w._poll()
            assert w.remote_records == publication_records and not w.busy
            samples.append((time.perf_counter() - fetched[0]) * 1000)
            assert provenance.wait(1) and community.wait(1) and cache.wait(1)
            assert not release.is_set()
    finally:
        release.set()
        w.background_results.close()
result = dict(
    method="221 records, fake Treeview callback, 100ms mailbox poll phase, all three maintenance tasks blocked, no Tk/network",
    samples_ms=samples,
    min_ms=min(samples),
    median_ms=statistics.median(samples),
    max_ms=max(samples),
)
results["publication"] = result
results["python"] = sys.version.split()[0]
print(json.dumps(results, indent=2))
