#!/usr/bin/env python3
"""Closed synthetic workflow provider. Never constructs launcher services or discovers games.

Only root's visible acceptance lane launches previews. This script serves JSON over
stdio, using real history/annotation validators in a temporary synthetic library.
Every unlisted method, including game/profile/collector/update operations, refuses.
"""

import argparse
import base64
from collections import Counter
from dataclasses import asdict
import json
import hashlib
import os
from pathlib import Path
import struct
import sys
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.annotations import AnnotationStore
from smr_launcher.history_ui import save_digest
from smr_launcher.native_backend import NativeLibrary
from smr_launcher.play_history import PlayHistoryStore, context, digest, summarize

BUILD = digest("workflow synthetic build")
SCENARIO = "Synthetic Coast.xml"
ATTEMPT = digest("workflow synthetic open")
GAME = digest("workflow synthetic game")
SAVE_BYTES = b"Fictional save fixture; not a game asset."
SAVE = hashlib.sha256(SAVE_BYTES).hexdigest()


def png(color=(0x55, 0x88, 0xBB, 0xFF)):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0" + bytes(color)))
        + chunk(b"IEND", b"")
    )


def records():
    return tuple(
        SimpleNamespace(
            profile_id="workflow-" + kind,
            name="Synthetic " + kind.title(),
            archive_sha256=digest("workflow archive", kind),
            variant_id=digest("workflow edition", kind),
            scenarios=(SCENARIO, "Synthetic Highlands.xml"),
            source_url="",
            source_label="Synthetic fixture; no source download",
            archive_modified="Not provided",
            archive_filename="synthetic-" + kind + ".zip",
        )
        for kind in ("known", "missing", "other")
    )


def seed(store, record, case="common"):
    ctx = context(record, BUILD, "" if case == "missing-scenario" else SCENARIO)
    rows = [store.event(ctx, "process_confirmed", attempt_id=ATTEMPT, source="observed")]
    if case != "missing-scenario":
        rows.append(store.event(ctx, "game_started", attempt_id=ATTEMPT, game_id=GAME))
        if case == "ambiguous":
            rows.append(
                store.event(ctx, "game_started", attempt_id=ATTEMPT, game_id=digest("second game"))
            )
    rows.append(store.event(ctx, "unknown_exit", attempt_id=ATTEMPT, source="observed"))
    if case == "problem":
        rows.append(
            store.event(
                ctx,
                "crash_unknown",
                attempt_id=ATTEMPT,
                incident_id=digest("retained incident"),
                source="observed",
            )
        )
    store.append(*rows)


class WorkflowFixture:
    def __init__(self, root, case="common"):
        self.root, self.case = Path(root).resolve(), case
        self.root.mkdir(parents=True, exist_ok=True)
        self.library = self.root / "library"
        self.records = records()
        self.app = SimpleNamespace(
            library=self.library,
            installation=SimpleNamespace(executable_sha256=BUILD),
            catalogue=lambda: self.records,
        )
        self.native = NativeLibrary()
        self.store = PlayHistoryStore(self.library)
        for record in self.records:
            seed(self.store, record, case)
        self.counts, self.last = Counter(), {}
        self.failed = False
        self.delay_record = False
        self.assets = self.root / "assets"
        self.assets.mkdir()
        (self.assets / "synthetic.png").write_bytes(png())
        (self.assets / "synthetic-2.png").write_bytes(png((0x55, 0xAA, 0x66, 0xFF)))
        (self.assets / "synthetic-3.png").write_bytes(png((0xCC, 0x88, 0x55, 0xFF)))
        (self.assets / "unsupported.txt").write_text("Synthetic unsupported image")
        (self.assets / "unsupported.png").write_text("Synthetic unsupported image bytes")
        (self.assets / "oversized.png").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
        (self.assets / "synthetic.sav").write_bytes(SAVE_BYTES)

    def presentation(self, record):
        ctx = context(record, BUILD)
        summary = asdict(summarize(self.store.records(), ctx))
        summary.pop("events")
        known = record.profile_id.endswith("known")
        return dict(
            id=record.profile_id,
            name=record.name,
            **ctx,
            metadata=dict(
                author="Synthetic Author" if known else "",
                version="1.0" if known else "",
                created="12 June 2010" if known else "",
                description="Fictional coast railway." if known else "",
                source_urls=[],
                map_type="Single player",
            ),
            art=base64.b64encode(png()).decode() if known else "",
            annotation=AnnotationStore(self.library).load(record),
            summary=summary,
            verification="Not verified",
            notice="Synthetic fixture only",
            history_error="",
        )

    def call(self, method, params):
        self.counts[method] += 1
        self.last[method] = params
        if method == "fixture.counts":
            return dict(self.counts)
        if method == "fixture.last":
            return self.last
        if method == "fixture.info":
            return dict(
                assets=str(self.assets),
                build=BUILD,
                attempt=ATTEMPT,
                game=GAME,
                save=SAVE,
                case=self.case,
            )
        if method == "fixture.configure":
            self.delay_record = params.get("delay_record") is True
            return {}
        if method == "snapshot":
            return dict(
                maps=[self.presentation(r) for r in self.records],
                available=True,
                enrolled=True,
                status="ISOLATED SYNTHETIC ACCEPTANCE — no launcher service or game",
                crash_status="No monitor or collector exists in this fixture",
            )
        if method in ("prefs", "tools.status"):
            return dict(strings={}, available=True, enrolled=True, job={}, languages=[], voices=[])
        if method == "history.save_digest":
            path = Path(params["path"]).resolve()
            if path.parent != self.assets or path.name != "synthetic.sav":
                raise ValueError("Only the generated synthetic save fixture may be read")
            return dict(save_id=save_digest(path))
        record = next((r for r in self.records if r.profile_id == params.get("profile_id")), None)
        if record is None:
            raise ValueError("The synthetic provider refuses this method/target")
        if method == "details":
            ctx = context(record, BUILD)
            history = [
                e
                for e in self.store.records()
                if all(
                    e[k] == ctx[k]
                    for k in ("archive_sha256", "variant_id", "game_executable_sha256")
                )
            ]
            return dict(
                profile_id=record.profile_id,
                context=ctx,
                scenarios=record.scenarios,
                history=history,
                summary=self.presentation(record)["summary"],
                verification_text=(
                    "\n\n".join(
                        f"Synthetic unresolved issue {index}: gameplay remains unknown. "
                        "Keep this complete evidence body below the selected edition identities."
                        for index in range(1, 41)
                    )
                    if self.case == "long-issues"
                    else "Synthetic; gameplay unknown"
                ),
                checks_text="No game asset scan ran",
            )
        if method == "annotations.save":
            self.native.require_scope(self.app, record, params)
            image = "" if params.get("reset_image") else params.get("image_png")
            saved = AnnotationStore(self.library).save(
                record,
                title=params.get("title", ""),
                notes=params.get("notes", ""),
                image_png=image,
            )
            return dict(
                profile_id=record.profile_id,
                **context(record, BUILD),
                annotation=saved,
                art=self.presentation(record)["art"],
                saved=True,
            )
        if method == "history.record":
            if self.delay_record:
                time.sleep(0.15)
            if self.case == "reject" and not self.failed:
                self.failed = True
                raise ValueError(
                    "Synthetic validation rejection before write; draft retained. Correct/review and confirm again."
                )
            if self.case == "unknown" and not self.failed:
                self.failed = True
                real_write, written = os.write, []

                def interrupted(fd, data):
                    written.append(True)
                    return real_write(fd, data[:12]) if len(written) == 1 else 0

                with patch("smr_launcher.play_history.os.write", side_effect=interrupted):
                    return self.native.history_record(self.app, params)
            return self.native.history_record(self.app, params)
        raise ValueError(
            "This isolated provider refuses all game/profile/collector/update/network operations"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        choices=(
            "common",
            "ambiguous",
            "missing-scenario",
            "problem",
            "reject",
            "unknown",
            "long-issues",
        ),
        default="common",
    )
    args = parser.parse_args()
    with TemporaryDirectory(prefix="smr-workflow-fixture-") as folder:
        fixture = WorkflowFixture(folder, args.case)
        for line in sys.stdin:
            request = json.loads(line)
            try:
                data = fixture.call(request["method"], request.get("params", {}))
                reply = dict(id=request["id"], ok=True, data=data)
            except Exception as exc:
                reply = dict(id=request["id"], ok=False, error=str(exc))
            print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
