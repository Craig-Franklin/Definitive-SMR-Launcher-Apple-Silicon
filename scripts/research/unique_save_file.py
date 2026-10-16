"""Read-only exact-new-file binding for one finite diagnostic save operation.

The caller supplies an independently observed UI-to-file mapping and authenticates
job/profile ownership. These filesystem facts grant no save/reopen/gameplay claim.
Only flat plain-file directories are supported; unknown layout refuses safely.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
import os
from pathlib import Path
import stat
import time

from smr_launcher.history_ui import save_digest
from smr_launcher.resource_identity import _open_directory_path, _verify_directory_path
from smr_launcher.scenario_evidence import digest, sha, text


class SaveFileRefused(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise SaveFileRefused(reason)


def absolute(value):
    path = Path(value)
    need(path.is_absolute() and ".." not in path.parts, "Exact absolute owned path required")
    return path


@dataclass(frozen=True)
class SaveScope:
    job: str
    archive_sha256: str
    variant_id: str
    scenario: str
    game_executable_sha256: str
    diagnostic_profile: str
    owned_profile_path: str

    def __post_init__(self):
        for value in (self.job, self.scenario, self.diagnostic_profile):
            text(value)
        for value in (self.archive_sha256, self.variant_id, self.game_executable_sha256):
            sha(value)
        absolute(self.owned_profile_path)


@dataclass(frozen=True)
class SaveIntent:
    save_directory: str
    filename: str
    ui_mapping_observation_sha256: str

    def __post_init__(self):
        absolute(self.save_directory)
        text(self.filename)
        need(
            self.filename not in (".", "..")
            and not any(c in self.filename for c in ("/", "\\", "\x00")),
            "Observed exact single-component filename required",
        )
        sha(self.ui_mapping_observation_sha256)


@dataclass(frozen=True)
class Limits:
    entries: int = 25000
    total_bytes: int = 2 * 1024**3
    file_bytes: int = 512 * 1024**2

    def __post_init__(self):
        for value, ceiling in (
            (self.entries, 25000),
            (self.total_bytes, 2 * 1024**3),
            (self.file_bytes, 512 * 1024**2),
        ):
            need(
                type(value) is int and 0 < value <= ceiling,
                "Positive bounded inventory limits required",
            )


def signature(info):
    """Stable identity/metadata; access time changes when this helper reads."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        getattr(info, "st_birthtime_ns", None),
    )


def directory_identity(signature_value):
    # A new directory entry legitimately changes directory size/timestamps/link
    # count. Device/inode/type/mode/owner still fence the same owned directory.
    return tuple(signature_value[index] for index in (0, 1, 2, 4, 5))


@dataclass(frozen=True)
class FileFact:
    name: str
    identity: tuple
    sha256: str


@dataclass(frozen=True)
class Inventory:
    directory: str
    ancestors: tuple
    root: tuple
    files: tuple[FileFact, ...]
    started: float
    complete: float


def inventory(directory, limits):
    """One complete bounded read using existing no-follow and digest guards."""
    path = absolute(directory)
    started = time.monotonic()
    fd, ancestors = _open_directory_path(path, "diagnostic saves")
    try:
        root = signature(os.fstat(fd))
        names = []
        with os.scandir(fd) as scan:
            for entry in scan:
                names.append(entry.name)
                need(len(names) <= limits.entries, "Save inventory entry budget exceeded")
        files, total = [], 0
        for name in sorted(names):
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need(
                stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                "Only plain unlinked save files supported",
            )
            need(
                0 < before.st_size <= limits.file_bytes, "Save file is empty or exceeds byte budget"
            )
            total += before.st_size
            need(total <= limits.total_bytes, "Save inventory total byte budget exceeded")
            value = save_digest(path / name)
            after = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need(signature(before) == signature(after), "Save entry changed while hashing")
            files.append(FileFact(name, signature(before), value))
        # Recheck files already read: an in-place writer need not change the
        # directory's timestamps. No cache or mtime-based file selection.
        for item in files:
            need(
                signature(os.stat(item.name, dir_fd=fd, follow_symlinks=False)) == item.identity,
                "Save inventory changed before completion",
            )
        need(signature(os.fstat(fd)) == root, "Save directory changed during inventory")
        _verify_directory_path(path, "diagnostic saves", list(ancestors))
        return Inventory(str(path), tuple(ancestors), root, tuple(files), started, time.monotonic())
    finally:
        os.close(fd)


@dataclass(frozen=True)
class BeforeSave:
    scope: SaveScope
    intent: SaveIntent
    limits: Limits
    inventory: Inventory


def capture_before(scope, intent, *, limits=Limits()):
    need(
        isinstance(scope, SaveScope) and isinstance(intent, SaveIntent),
        "Exact save scope/intent required",
    )
    need(isinstance(limits, Limits), "Explicit bounded inventory limits required")
    directory, profile = absolute(intent.save_directory), absolute(scope.owned_profile_path)
    need(
        directory != profile and directory.is_relative_to(profile),
        "Save directory must be inside the exact owned profile",
    )
    observed = inventory(directory, limits)
    need(
        intent.filename not in {row.name for row in observed.files},
        "Intended exact save already exists; overwrite refused",
    )
    before = BeforeSave(scope, intent, limits, observed)
    require_absent(before)
    return before


def require_absent(before):
    """Recheck the complete before image and filesystem name before save input.

    Checking the actual name also catches case/normalization aliases on the
    filesystem. A read-only helper cannot reserve a name against future writers;
    the admitted UI caller must independently refuse any overwrite prompt.
    """
    observed = inventory(before.intent.save_directory, before.limits)
    need(
        same_inventory(before.inventory, observed),
        "Before-save inventory drift; no save input permitted",
    )
    path = absolute(before.intent.save_directory)
    fd, ancestors = _open_directory_path(path, "diagnostic saves")
    try:
        need(
            tuple(ancestors) == observed.ancestors and signature(os.fstat(fd)) == observed.root,
            "Owned directory changed before absence check",
        )
        try:
            os.stat(before.intent.filename, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise SaveFileRefused("Intended filesystem target already exists; overwrite refused")
        need(
            signature(os.fstat(fd)) == observed.root, "Save directory changed during absence check"
        )
        _verify_directory_path(path, "diagnostic saves", list(ancestors))
    finally:
        os.close(fd)


@dataclass(frozen=True)
class NewSaveSample:
    before_sha256: str
    inventory: Inventory


def inspect_new(before):
    """Observe only the intended exact target, with no other additions/drift."""
    current = inventory(before.intent.save_directory, before.limits)
    old = before.inventory
    need(
        current.ancestors == old.ancestors
        and directory_identity(current.root) == directory_identity(old.root),
        "Owned save directory identity changed",
    )
    previous, now = ({row.name: row for row in sample.files} for sample in (old, current))
    need(
        set(now) - set(previous) == {before.intent.filename},
        "Exact unique new target unavailable or additional files ambiguous",
    )
    need(
        all(now.get(name) == row for name, row in previous.items()),
        "Preexisting save preservation drift",
    )
    return NewSaveSample(digest(asdict(before)), current)


@dataclass(frozen=True)
class BoundSave:
    before: BeforeSave
    first: NewSaveSample
    second: NewSaveSample
    minimum_stable_seconds: float
    deadline: float


def same_inventory(left, right):
    return (left.directory, left.ancestors, left.root, left.files) == (
        right.directory,
        right.ancestors,
        right.root,
        right.files,
    )


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def bind_stable(before, first, *, minimum_stable_seconds, deadline):
    """Take the second read; caller waits using its unchanged finite clock.

    No internal sleep, polling, retry or save-format completion inference.
    """
    need(
        finite(minimum_stable_seconds) and 0 < minimum_stable_seconds <= 30,
        "Finite positive stability interval required",
    )
    need(finite(deadline) and time.monotonic() < deadline, "Original save-cycle deadline expired")
    need(first.before_sha256 == digest(asdict(before)), "Save sample belongs to another intent/job")
    second = inspect_new(before)
    need(second.inventory.complete < deadline, "Original save-cycle deadline expired during read")
    need(
        second.inventory.started - first.inventory.complete >= minimum_stable_seconds,
        "Save stability interval not observed",
    )
    need(
        same_inventory(first.inventory, second.inventory),
        "New save changed between stability samples",
    )
    return BoundSave(before, first, second, minimum_stable_seconds, deadline)


@dataclass(frozen=True)
class OwnedPathMapping:
    from_profile_path: str
    to_profile_path: str
    from_save_directory: str
    to_save_directory: str
    observation_sha256: str

    def __post_init__(self):
        for value in (
            self.from_profile_path,
            self.to_profile_path,
            self.from_save_directory,
            self.to_save_directory,
        ):
            absolute(value)
        sha(self.observation_sha256)


def verify_reopen(bound, scope, *, deadline, mapping=None, scratch_autosaves=()):
    """Verify the same bound inode and bytes immediately before guarded reopen.

    Explicit owned directory relocation may change the path. Copies, including
    byte-identical copies, cannot become this job's exact-file authority.
    An authenticated diagnostic-copy owner may explicitly allow its five known
    scratch autosave slots to rotate. The target and every other file stay strict.
    """
    need(
        finite(deadline) and deadline == bound.deadline,
        "Original save-cycle deadline changed; reopening cannot renew clocks",
    )
    need(time.monotonic() < deadline, "Original save-cycle deadline expired")
    original = bound.before
    directory = original.intent.save_directory
    if mapping is None:
        need(scope == original.scope, "Changed profile/scope requires explicit owned mapping")
    else:
        need(isinstance(mapping, OwnedPathMapping), "Observed owned path mapping required")
        relative = absolute(directory).relative_to(absolute(original.scope.owned_profile_path))
        need(
            mapping.from_profile_path == original.scope.owned_profile_path
            and mapping.from_save_directory == directory
            and scope == replace(original.scope, owned_profile_path=mapping.to_profile_path)
            and absolute(mapping.to_save_directory) == absolute(mapping.to_profile_path) / relative,
            "Owned mapping does not match exact job/profile/save directory",
        )
        directory = mapping.to_save_directory
    current = inventory(directory, original.limits)
    expected = bound.second.inventory
    need(
        type(scratch_autosaves) is tuple
        and all(type(name) is str for name in scratch_autosaves)
        and len(set(scratch_autosaves)) == len(scratch_autosaves)
        and set(scratch_autosaves).issubset({f"Auto{i}.SMRailroadsSavedGame" for i in range(5)})
        and original.intent.filename not in scratch_autosaves,
        "Only explicit diagnostic scratch autosave slots may rotate",
    )
    previous, now = ({row.name: row for row in sample.files} for sample in (expected, current))
    need(current.complete < deadline, "Original save-cycle deadline expired during read")
    need(
        directory_identity(current.root) == directory_identity(expected.root)
        and set(now) == set(previous)
        and all(
            now[name] == row for name, row in previous.items() if name not in scratch_autosaves
        ),
        "Bound save or preserved files replaced/changed; reopen refused",
    )
    if mapping is None:
        need(
            current.ancestors == expected.ancestors
            and (bool(scratch_autosaves) or current.root == expected.root),
            "Save path/directory drift; reopen refused",
        )
    return dict(
        bound_save_sha256=digest(asdict(bound)),
        scope=asdict(scope),
        inventory=asdict(current),
        owned_mapping=asdict(mapping) if mapping else None,
        exact_target=asdict(
            next(row for row in current.files if row.name == original.intent.filename)
        ),
        claim="filesystem_identity_only",
    )


def public_reference(bound):
    """Content digest for existing ledger callers; no success event or path."""
    target = next(
        row for row in bound.second.inventory.files if row.name == bound.before.intent.filename
    )
    return dict(
        save_id=target.sha256,
        claim="filesystem_identity_only",
        save_made="unknown",
        save_reopened="unknown",
        continued_action="unknown",
    )
