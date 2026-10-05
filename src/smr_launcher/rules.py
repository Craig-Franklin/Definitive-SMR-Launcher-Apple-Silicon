"""Exact, versioned byte repairs for independently prepared map copies.

Rules are intentionally narrow. A rule must name the source archive and game
executable hashes and each file's complete before and after hashes. It cannot
modify an original archive or infer a repair from a filename alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import hashlib
import json
import os
import re
import tempfile
import unicodedata

from .activation import ActivationError, _assert_regular_tree


class RuleError(ValueError):
    """A compatibility rule or its input does not match its declaration."""


MAX_PATCHED_FILE_BYTES = 16 * 1024 * 1024
PRESERVE_RESOURCE_MTIMES = "preserve-imported-resource-mtimes-v1"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


@dataclass(frozen=True)
class TokenPatch:
    path: str
    before_sha256: str
    after_sha256: str
    old: bytes
    new: bytes

    def __post_init__(self) -> None:
        parts = self.path.split("/")
        if (len(parts) < 2 or parts[0] not in ("CustomAssets", "UserMaps")
                or unicodedata.normalize("NFC", self.path) != self.path
                or any(part in ("", ".", "..") or "\\" in part or ":" in part
                       or any(ord(char) < 32 or ord(char) == 127 for char in part)
                       for part in parts)):
            raise RuleError("Patch path must be a regular file below an install root")
        if not _hash(self.before_sha256) or not _hash(self.after_sha256):
            raise RuleError("Patch must declare exact before and after SHA-256 hashes")
        if not self.old or self.old == self.new or len(self.old) > 1 << 20 or len(self.new) > 1 << 20:
            raise RuleError("Patch tokens must be small, nonempty, and different")


@dataclass(frozen=True)
class AssetAddition:
    """A reviewed file copied into an isolated compatibility edition.

    Additions are deliberately data-bearing and hash-bound.  A recipe may
    source the bytes from another preserved import, but the resulting rule is
    self-contained and cannot read arbitrary paths during preparation.
    """
    path: str
    after_sha256: str
    data: bytes
    mtime_ns: int

    def __post_init__(self) -> None:
        parts = self.path.split("/")
        if (len(parts) < 2 or parts[0] not in ("CustomAssets", "UserMaps")
                or unicodedata.normalize("NFC", self.path) != self.path
                or any(part in ("", ".", "..") or "\\" in part or ":" in part
                       or any(ord(char) < 32 or ord(char) == 127 for char in part)
                       for part in parts)):
            raise RuleError("Added asset path must be a regular file below an install root")
        if not _hash(self.after_sha256) or not isinstance(self.data, bytes):
            raise RuleError("Added asset needs an exact SHA-256 and byte payload")
        if len(self.data) > MAX_PATCHED_FILE_BYTES or _digest(self.data) != self.after_sha256:
            raise RuleError("Added asset payload is too large or has the wrong hash")
        if type(self.mtime_ns) is not int or self.mtime_ns < 0:
            raise RuleError("Added asset needs a nonnegative nanosecond mtime")


@dataclass(frozen=True)
class CompatibilityRule:
    rule_id: str
    version: int
    provenance: str
    reason: str
    archive_sha256: str
    game_executable_sha256: str
    patches: tuple[TokenPatch, ...]
    additions: tuple[AssetAddition, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", self.rule_id):
            raise RuleError("Rule ID must be a stable lowercase slug")
        if self.version < 1 or not self.provenance.strip() or not self.reason.strip():
            raise RuleError("Rule needs a positive version, provenance, and reason")
        if not _hash(self.archive_sha256) or not _hash(self.game_executable_sha256):
            raise RuleError("Rule needs exact archive and game SHA-256 hashes")
        if not self.patches and not self.additions:
            raise RuleError("Rule needs at least one patch or asset addition")
        paths = [patch.path.casefold() for patch in self.patches]
        paths.extend(addition.path.casefold() for addition in self.additions)
        if len(paths) != len(set(paths)):
            raise RuleError("Rule needs distinct patched and added files")

    @property
    def key(self) -> str:
        return f"{self.rule_id}@{self.version}"


@dataclass(frozen=True)
class RuleApplication:
    rule_keys: tuple[str, ...]
    fingerprint: str
    output_sha256: tuple[tuple[str, str], ...]


def applicable_rules(
    rules: Iterable[CompatibilityRule], archive_sha256: str, game_executable_sha256: str
) -> tuple[CompatibilityRule, ...]:
    """Select exact matches, rejecting ambiguous versions or overlapping files."""
    selected = tuple(sorted(
        (rule for rule in rules if rule.archive_sha256 == archive_sha256
         and rule.game_executable_sha256 == game_executable_sha256),
        key=lambda rule: (rule.rule_id, rule.version),
    ))
    identifiers = [rule.rule_id for rule in selected]
    paths = [patch.path.casefold() for rule in selected for patch in rule.patches]
    paths.extend(addition.path.casefold() for rule in selected for addition in rule.additions)
    if len(identifiers) != len(set(identifiers)) or len(paths) != len(set(paths)):
        raise RuleError("Applicable rules have multiple versions or overlapping patch paths")
    return selected


def apply_rules(
    prepared_copy: Path, archive_sha256: str, game_executable_sha256: str,
    selected: Iterable[CompatibilityRule],
    *, metadata_policy: str | None = None,
) -> RuleApplication:
    """Apply exact rules to a disposable prepared copy, after a full preflight.

    Running this twice is safe: a file already at its declared output hash is
    left alone. The caller owns the prepared directory and discards it on any
    error; this function never reads or writes the source archive.
    """
    if metadata_policy not in (None, PRESERVE_RESOURCE_MTIMES):
        raise RuleError("Unsupported compatibility metadata policy")
    selected = tuple(sorted(selected, key=lambda rule: (rule.rule_id, rule.version)))
    if any(rule.archive_sha256 != archive_sha256 or
           rule.game_executable_sha256 != game_executable_sha256 for rule in selected):
        raise RuleError("A selected rule does not apply to this archive and game")
    if applicable_rules(selected, archive_sha256, game_executable_sha256) != selected:
        raise RuleError("Rule selection is ambiguous")
    root = Path(prepared_copy).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise RuleError("Prepared copy must be a regular directory")
    try:
        _assert_regular_tree(root)
    except ActivationError as exc:
        raise RuleError("Prepared copy contains an unsafe file or link") from exc
    writes: dict[Path, bytes] = {}
    metadata: dict[Path, os.stat_result] = {}
    outputs: list[tuple[str, str]] = []
    for rule in selected:
        for patch in rule.patches:
            path = root.joinpath(*patch.path.split("/"))
            if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_PATCHED_FILE_BYTES:
                raise RuleError("Patch target is missing or too large: " + patch.path)
            before = path.stat()
            data = path.read_bytes()
            if metadata_policy:
                after = path.stat()
                identity = lambda st: (st.st_dev, st.st_ino, st.st_mode, st.st_size,
                                       st.st_mtime_ns, st.st_ctime_ns)
                if identity(before) != identity(after) or len(data) != before.st_size:
                    raise RuleError("Patch resource metadata changed during preflight")
                metadata[path] = before
            current = _digest(data)
            if current == patch.after_sha256:
                outputs.append((patch.path, current))
                continue
            if current != patch.before_sha256 or data.count(patch.old) != 1:
                raise RuleError("Patch preimage or unique token differs: " + patch.path)
            changed = data.replace(patch.old, patch.new, 1)
            if len(changed) > MAX_PATCHED_FILE_BYTES:
                raise RuleError("Patch result exceeds the file size limit: " + patch.path)
            if _digest(changed) != patch.after_sha256:
                raise RuleError("Patch result differs from expected hash: " + patch.path)
            writes[path] = changed
            outputs.append((patch.path, patch.after_sha256))
        for addition in rule.additions:
            path = root.joinpath(*addition.path.split("/"))
            if path.exists() or path.is_symlink():
                if path.is_file() and not path.is_symlink() and _digest(path.read_bytes()) == addition.after_sha256:
                    outputs.append((addition.path, addition.after_sha256))
                    continue
                raise RuleError("Added asset target already exists: " + addition.path)
            parent = path.parent
            while True:
                if parent.is_symlink():
                    raise RuleError("Added asset parent contains a link: " + addition.path)
                if parent == root:
                    break
                if root not in parent.parents:
                    raise RuleError("Added asset parent escapes prepared root: " + addition.path)
                parent = parent.parent
            path.parent.mkdir(parents=True, exist_ok=True)
            writes[path] = addition.data
            outputs.append((addition.path, addition.after_sha256))
    descriptor = []
    for rule in selected:
        item = dict(id=rule.rule_id, version=rule.version, provenance=rule.provenance,
                    reason=rule.reason, archive=rule.archive_sha256,
                    game=rule.game_executable_sha256,
                    patches=[dict(path=patch.path, before=patch.before_sha256,
                                  after=patch.after_sha256, old=_digest(patch.old),
                                  new=_digest(patch.new)) for patch in rule.patches])
        if rule.additions:
            item["additions"] = [dict(path=addition.path, after=addition.after_sha256,
                                       size=len(addition.data), mtime_ns=addition.mtime_ns)
                                 for addition in rule.additions]
        descriptor.append(item)
    # Preserve every legacy rule fingerprint. The opt-in procedure receives a
    # distinct identity; callers still need a separate source-context binding.
    identity_descriptor = ({"rules": descriptor, "metadata_policy": metadata_policy}
                           if metadata_policy else descriptor)
    fingerprint = _digest(json.dumps(identity_descriptor, sort_keys=True, separators=(",", ":")).encode())
    for path, data in writes.items():
        fd, name = tempfile.mkstemp(prefix=".smr-rule-", dir=path.parent)
        stage = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if metadata_policy:
                source_stat = metadata.get(path)
                if source_stat is not None:
                    os.utime(stage, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
                    if stage.stat().st_mtime_ns != source_stat.st_mtime_ns:
                        raise RuleError("Patch stage could not preserve resource mtime")
                else:
                    addition = next((item for rule in selected for item in rule.additions
                                     if root.joinpath(*item.path.split("/")) == path), None)
                    if addition is None:
                        raise RuleError("Added asset metadata is missing")
                    os.utime(stage, ns=(addition.mtime_ns, addition.mtime_ns))
                    if stage.stat().st_mtime_ns != addition.mtime_ns:
                        raise RuleError("Added asset mtime could not be recorded")
            os.replace(stage, path)
            if metadata_policy:
                expected_mtime = metadata[path].st_mtime_ns if path in metadata else next(
                    item.mtime_ns for rule in selected for item in rule.additions
                    if root.joinpath(*item.path.split("/")) == path)
                if path.stat().st_mtime_ns != expected_mtime:
                    raise RuleError("Compatibility resource mtime differs")
        finally:
            stage.unlink(missing_ok=True)
    return RuleApplication(tuple(rule.key for rule in selected), fingerprint, tuple(outputs))
