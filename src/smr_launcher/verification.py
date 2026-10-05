"""Private, input-bound gameplay observations; static checks never earn a badge."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
import json

from .activation import _assert_no_symlink_ancestor, _atomic_json


CHECKS = (
    "scenario_loaded",
    "gameplay",
    "autosave_written",
    "manual_save_written",
    "manual_save_reloaded",
    "continued_after_reload",
)


class VerificationError(ValueError):
    """The private gameplay record needs inspection."""


@dataclass(frozen=True)
class GameplayVerification:
    archive_sha256: str
    game_executable_sha256: str
    variant_id: str
    assets_sha256: str
    observed_at: str
    reporter: str
    checks: dict[str, bool]
    scope: str
    issue: str = ""
    resources_sha256: str = ""

    def __post_init__(self) -> None:
        for value in (self.archive_sha256, self.game_executable_sha256,
                      self.variant_id, self.assets_sha256):
            if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise VerificationError("Gameplay record needs exact SHA-256 identities")
        if (not isinstance(self.resources_sha256, str) or
                (self.resources_sha256 and (len(self.resources_sha256) != 64 or
                 any(char not in "0123456789abcdef" for char in self.resources_sha256)))):
            raise VerificationError("Gameplay resource identity must be a SHA-256 digest")
        try:
            observed = datetime.fromisoformat(self.observed_at)
        except (ValueError, TypeError) as exc:
            raise VerificationError("Gameplay record date is invalid") from exc
        if observed.tzinfo is None:
            raise VerificationError("Gameplay record needs a dated time zone")
        if (not isinstance(self.reporter, str) or not isinstance(self.scope, str)
                or not isinstance(self.issue, str) or not self.reporter.strip()
                or len(self.reporter) > 120 or not self.scope.strip()
                or len(self.scope) > 500 or len(self.issue) > 500):
            raise VerificationError("Gameplay record needs bounded reporter and scope")
        if (not isinstance(self.checks, dict) or set(self.checks) != set(CHECKS)
                or any(type(value) is not bool for value in self.checks.values())):
            raise VerificationError("Gameplay record has incomplete checks")

    @property
    def status(self) -> str:
        if self.issue:
            return "Known issue"
        # Legacy observations remain readable, but their missing timestamps
        # cannot establish current resource selection on this game build.
        return "Verified" if self.resources_sha256 and all(self.checks.values()) else "Not verified"

    def as_json(self) -> dict:
        return dict(archive_sha256=self.archive_sha256,
                    game_executable_sha256=self.game_executable_sha256,
                    variant_id=self.variant_id, assets_sha256=self.assets_sha256,
                    observed_at=self.observed_at, reporter=self.reporter,
                    checks=self.checks, scope=self.scope, issue=self.issue,
                    resources_sha256=self.resources_sha256)

    @classmethod
    def from_json(cls, value: dict) -> "GameplayVerification":
        if not isinstance(value, dict):
            raise VerificationError("Gameplay record is not an object")
        try:
            return cls(value["archive_sha256"], value["game_executable_sha256"],
                       value["variant_id"], value["assets_sha256"],
                       value["observed_at"], value["reporter"], value["checks"],
                       value["scope"], value.get("issue", ""), value.get("resources_sha256", ""))
        except (KeyError, TypeError) as exc:
            raise VerificationError("Gameplay record is incomplete") from exc


class VerificationStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def records(self) -> tuple[GameplayVerification, ...]:
        _assert_no_symlink_ancestor(self.path)
        if not self.path.exists():
            return ()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("schema") != 1 or not isinstance(document.get("records"), list):
                raise VerificationError("Gameplay record file has an unexpected schema")
            if len(document["records"]) > 1000:
                raise VerificationError("Gameplay record file exceeds the entry limit")
            return tuple(GameplayVerification.from_json(value) for value in document["records"])
        except (OSError, ValueError, AttributeError) as exc:
            raise VerificationError("Gameplay record file needs inspection") from exc

    def latest(self, archive_sha256: str, game_executable_sha256: str,
               variant_id: str, assets_sha256: str, *,
               resources_sha256: Optional[str] = None) -> Optional[GameplayVerification]:
        matched = [
            item for item in self.records()
            if (item.archive_sha256, item.game_executable_sha256,
                item.variant_id, item.assets_sha256) ==
               (archive_sha256, game_executable_sha256, variant_id, assets_sha256)
            and (resources_sha256 is None or item.resources_sha256 == resources_sha256)
        ]
        return matched[-1] if matched else None

    def append(self, record: GameplayVerification) -> None:
        existing = list(self.records())
        if len(existing) >= 1000:
            raise VerificationError("Gameplay record entry limit reached")
        existing.append(record)
        _atomic_json(self.path, dict(schema=1, records=[item.as_json() for item in existing]))
