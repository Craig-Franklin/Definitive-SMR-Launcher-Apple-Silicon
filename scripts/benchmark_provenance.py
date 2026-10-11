#!/usr/bin/env python3
"""Compare pre-batching provenance reconciliation with current source.

Only generated opaque bytes in a temporary HOME/library are used. No discovery,
game, profile, archive extraction, network, or GUI runs. Prints JSON measurements;
retain raw output under ignored .handoff when running in the project checkout.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.application import LauncherApplication, MapRecord
from smr_launcher.collection import RemoteMap
from smr_launcher.activation import _assert_no_symlink_ancestor
import smr_launcher.application as application


def old_match_sources(app, remotes):
    """Pre-optimization algorithm, retained solely for synthetic comparison."""
    candidates = {}
    for remote in remotes:
        candidates.setdefault(remote.size, []).append(remote)
    matched = 0
    with app._locked():
        for record in app.catalogue():
            if record.source_url and record.archive_sha1:
                continue
            archive = app.originals / (record.archive_sha256 + ".7z")
            _assert_no_symlink_ancestor(archive)
            if not archive.is_file():
                continue
            for remote in candidates.get(archive.stat().st_size, []):
                if app._matches_remote(archive, remote):
                    app._record_source(record, remote)
                    matched += 1
                    break
    return matched


def fixture(home, n, collision):
    app = LauncherApplication.__new__(LauncherApplication)
    app.library = home / ("library-%d-%s" % (n, "same-size" if collision else "unique-size"))
    app.originals = app.library / "original-archives"
    app.catalogue_file = app.library / "catalogue.json"
    app.originals.mkdir(parents=True)
    records, remotes = [], []
    for i in range(n):
        payload = (("synthetic-%04d" % i).encode() * 400)[:4096]
        if not collision:
            payload += b"x" * i
        archive = hashlib.sha256(payload).hexdigest()
        variant = hashlib.sha256(("edition-%d" % i).encode()).hexdigest()
        (app.originals / (archive + ".7z")).write_bytes(payload)
        imported = app.library / "imports" / variant
        imported.mkdir(parents=True)
        metadata = ("Map Name: Synthetic %04d\nAuthor: Author %d\nDate Created: 2026-01-01\nType: Singleplayer\n\n"
                    % (i, i % 10) + "Synthetic railway briefing. " * 60).encode()
        (imported / "mapInfo.txt").write_bytes(metadata)
        records.append(MapRecord("Synthetic %04d" % i, archive, variant, "map-" + variant,
                                 ("scenario.xml",), variant, "e" * 64, variant,
                                 "Synthetic_%04d.7z" % i))
        remotes.append(RemoteMap("Synthetic_%04d.7z" % i, len(payload), hashlib.sha1(payload).hexdigest()))
    initial = json.dumps(dict(schema=1, maps=[record.as_json() for record in records]))
    return app, tuple(remotes), initial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--work-dir", type=Path, default=Path(".handoff/provenance-benchmark"))
    args = parser.parse_args()
    if args.samples < 3:
        parser.error("use at least three samples")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    previous_home = os.environ.get("HOME")
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=args.work_dir.resolve()) as temporary:
        home = Path(temporary) / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        try:
            for n in (25, 250):
                for collision in (False, True):
                    app, remotes, initial = fixture(home, n, collision)
                    expected = None
                    for name, operation in (("before", lambda: old_match_sources(app, remotes)),
                                            ("after", lambda: app.match_collection_sources(remotes))):
                        elapsed = []
                        for _ in range(args.samples):
                            app.catalogue_file.write_text(initial)
                            start = time.perf_counter_ns()
                            matched = operation()
                            elapsed.append((time.perf_counter_ns() - start) / 1e6)
                            assert matched == n
                        result = json.loads(app.catalogue_file.read_text())
                        if expected is None:
                            expected = result
                        else:
                            assert result == expected, "matching/source semantics changed"
                        app.catalogue_file.write_text(initial)
                        helper = "_matches_remote" if name == "before" else "_collection_archive_sha1"
                        with patch.object(app, helper, wraps=getattr(app, helper)) as hashing, \
                             patch.object(application, "_atomic_json", wraps=application._atomic_json) as writing:
                            assert operation() == n
                        row = dict(phase=name, n=n, collision=collision, samples=args.samples,
                                   catalogue_bytes=len(initial.encode()), archive_bytes=sum(r.size for r in remotes),
                                   min_ms=min(elapsed), median_ms=statistics.median(elapsed), max_ms=max(elapsed),
                                   raw_ms=elapsed, untimed_hash_calls=hashing.call_count,
                                   untimed_catalogue_writes=writing.call_count)
                        rows.append(row)
                        for remote, record in zip(remotes, app.catalogue()):
                            payload = (app.originals / (record.archive_sha256 + ".7z")).read_bytes()
                            assert len(payload) == remote.size and hashlib.sha1(payload).hexdigest() == remote.sha1
        finally:
            if previous_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous_home
    print(json.dumps(dict(python=sys.version, platform=platform.platform(), machine=platform.machine(),
                         clock="perf_counter_ns", os_cache="not flushed", timing="uninstrumented",
                         fixtures="25/250 distinct 4KiB opaque archives; unique/all-same sizes; no art/evidence",
                         results=rows), indent=2))


if __name__ == "__main__":
    main()
