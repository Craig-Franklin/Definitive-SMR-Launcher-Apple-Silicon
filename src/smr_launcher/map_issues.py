"""Durable local failure warnings, scoped to a map edition and game build."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import stat

from .activation import _assert_no_symlink_ancestor


class MapIssueStore:
    def __init__(self, library):
        self.root = Path(library) / "map-issues"

    def record(self, context, *, incident_id, occurred_at, kind, detail):
        # Stock sessions and unbound diagnostics cannot implicate a custom map.
        keys = ("archive_sha256", "variant_id", "game_executable_sha256")
        if any(not re.fullmatch(r"[0-9a-f]{64}", context.get(k) or "") for k in keys):
            return None
        value = dict(
            schema=1,
            **{k: context[k] for k in keys},
            incident_id=incident_id,
            occurred_at=occurred_at,
            kind=kind,
            detail=detail,
        )
        self._validate(value)
        _assert_no_symlink_ancestor(self.root)
        self.root.mkdir(mode=0o700, exist_ok=True)
        if stat.S_IMODE(self.root.stat().st_mode) != 0o700:
            raise ValueError("Map issue directory must be private")
        from .crash_monitor import _private_json, _read_regular

        path = self.root / (hashlib.sha256((kind + incident_id).encode()).hexdigest() + ".json")
        if path.exists():
            existing = json.loads(_read_regular(path, 8192))
            self._validate(existing)
            if existing != value:
                raise ValueError("Map issue identity collision")
        else:
            _private_json(path, value)
        return value

    @staticmethod
    def _validate(value):
        keys = {
            "schema",
            "archive_sha256",
            "variant_id",
            "game_executable_sha256",
            "incident_id",
            "occurred_at",
            "kind",
            "detail",
        }
        if not isinstance(value, dict) or set(value) != keys or value["schema"] != 1:
            raise ValueError("Invalid map issue")
        if any(
            not isinstance(value[k], str) or not re.fullmatch(r"[0-9a-f]{64}", value[k])
            for k in ("archive_sha256", "variant_id", "game_executable_sha256", "incident_id")
        ):
            raise ValueError("Invalid map issue identity")
        if value["kind"] not in ("crash", "resource_limit"):
            raise ValueError("Invalid map issue kind")
        if datetime.fromisoformat(value["occurred_at"]).tzinfo is None:
            raise ValueError("Invalid map issue date")
        if not isinstance(value["detail"], str) or not 0 < len(value["detail"]) <= 500:
            raise ValueError("Invalid map issue detail")

    def records(self):
        _assert_no_symlink_ancestor(self.root)
        if not self.root.exists():
            return ()
        from .crash_monitor import _read_regular

        result = []
        for index, path in enumerate(sorted(self.root.glob("*.json"))):
            if index >= 4096:
                raise ValueError("Map issue history needs inspection")
            value = json.loads(_read_regular(path, 8192))
            self._validate(value)
            result.append(value)
        return tuple(sorted(result, key=lambda v: v["occurred_at"]))

    def for_map(self, record, game_sha256):
        return tuple(
            value
            for value in self.records()
            if (value["archive_sha256"], value["variant_id"], value["game_executable_sha256"])
            == (record.archive_sha256, record.variant_id, game_sha256)
        )
