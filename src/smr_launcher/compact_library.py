"""Explicit compact-library migration and bounded working-set policy.

The application owns both stopped-state locks. Unknown directories and explicit
pins are measured exclusions, never inferred deletion or reconstruction targets.
"""

from __future__ import annotations

import hashlib
import json

from .activation import ORIGINAL, _assert_no_symlink_ancestor, _atomic_json, _fsync_dir
from .cold_storage import ColdStorageError, _relative, _checkpoint


CONFIG = "compact-library.json"
JOURNAL = "compact-migration.json"


def _read(path):
    _assert_no_symlink_ancestor(path)
    if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 256 * 1024**2:
        raise ColdStorageError("Compact-library record is missing, linked or oversized")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ColdStorageError("Compact-library record must be a JSON object")
    return value


def config(app):
    path = app.library / CONFIG
    _assert_no_symlink_ancestor(path)
    if not path.exists():
        return None
    value = _read(path)
    if (
        value.get("schema") != 1
        or not isinstance(value.get("exclude"), list)
        or any(_relative(p) != p for p in value["exclude"])
    ):
        raise ColdStorageError("Compact-library configuration needs inspection")
    return value


def _binding(app):
    return dict(
        catalogue_sha256=hashlib.sha256(app.catalogue_file.read_bytes()).hexdigest(),
        active=app.profiles._state()["active"],
        installation_sha256=hashlib.sha256(app.installation_binding.read_bytes()).hexdigest(),
    )


def _logical_bytes(path):
    _assert_no_symlink_ancestor(path)
    if not path.exists():
        return 0
    if path.is_symlink():
        raise ColdStorageError("Linked compact-library exclusion")
    return sum(p.lstat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def targets(app, exclude, include_profiles=()):
    known = {}
    for record in app.catalogue():
        known[app._prepared_root(record)] = "prepared"
        if record.imported_directory:
            from .removal import _leaf

            known[_leaf(app.imports, record.imported_directory, r"[0-9a-f]{32}")] = "import"
        if app.profiles._state()["active"] != record.profile_id:
            known[app.profiles._profile_path(record.profile_id)] = "profile"
    if app.profiles._state()["active"] != ORIGINAL:
        known[app.profiles._profile_path(ORIGINAL)] = "profile"
    for profile_id in include_profiles:
        path = app.profiles._profile_path(profile_id)
        if profile_id == app.profiles._state()["active"]:
            continue  # An explicitly included inactive profile may later become live.
        if not app.cold_storage.is_cold(path):
            app.profiles._marker(path, profile_id)
        known[path] = "profile"
    selected, excluded = [], []
    for path, kind in sorted(known.items()):
        key = path.relative_to(app.library).as_posix()
        if key in exclude:
            excluded.append(
                dict(
                    path=key, reason="explicit preservation pin", logical_bytes=_logical_bytes(path)
                )
            )
        elif path.exists() or app.cold_storage.is_cold(path):
            selected.append((key, kind, path))
        else:
            raise ColdStorageError("Known library tree is missing: " + key)
    for parent in (app.imports, app.prepared, app.profiles.profiles):
        _assert_no_symlink_ancestor(parent)
        for child in sorted(parent.iterdir()) if parent.exists() else ():
            if child in known or child.name.endswith(".cold.json"):
                continue
            # Temporary transactions and unowned directories are never adopted.
            excluded.append(
                dict(
                    path=child.relative_to(app.library).as_posix(),
                    reason="unowned or unrecognized tree",
                    logical_bytes=_logical_bytes(child),
                )
            )
    return selected, excluded


def plan(app, exclude=(), include_profiles=(), *, cancelled=None, progress=None):
    exclude = sorted(set(_relative(p) for p in exclude))
    include_profiles = sorted(set(include_profiles))
    selected, excluded = targets(app, exclude, include_profiles)
    recognized = {key for key, _, _ in selected} | {entry["path"] for entry in excluded}
    if set(exclude) - recognized:
        raise ColdStorageError("Unknown preservation pin; no compact plan created")
    from .archive_references import archive_references

    references = {}
    for digest in sorted({record.archive_sha256 for record in app.catalogue()}):
        _checkpoint(cancelled)
        if progress:
            progress("archive", len(references))
        archive = app.originals / (digest + ".7z")
        for content, source in archive_references(archive, cancelled=cancelled).items():
            references.setdefault(content, source)
    trees = []
    for key, kind, path in selected:
        if app.cold_storage.is_cold(path):
            raise ColdStorageError(
                "Existing cold tree requires its recorded migration, not a new plan"
            )
        trees.append(
            dict(path=key, kind=kind, entries=app.cold_storage.inspect(path, cancelled=cancelled))
        )
    return dict(
        schema=1,
        binding=_binding(app),
        exclude=exclude,
        excluded=excluded,
        trees=trees,
        archive_references=references,
        include_profiles=include_profiles,
    )


def migrate(app, candidate, *, cancelled=None, progress=None):
    """All-tree preflight, then resumable per-tree compact commits under both locks."""
    journal = app.library / JOURNAL
    _assert_no_symlink_ancestor(journal)
    if journal.exists():
        pending = _read(journal)
        if (
            pending.get("plan") != candidate
            or pending.get("schema") != 1
            or pending.get("direction") != "compact"
        ):
            raise ColdStorageError("Another exact compact migration owns the journal")
    else:
        if config(app) is not None:
            raise ColdStorageError("Compact library is already enabled")
        if candidate != plan(
            app,
            candidate.get("exclude", ()),
            candidate.get("include_profiles", ()),
            cancelled=cancelled,
            progress=progress,
        ):
            raise ColdStorageError(
                "Compact migration plan is stale or differs from current ownership"
            )
        pending = dict(schema=1, plan=candidate, completed=[], direction="compact")
        _atomic_json(journal, pending)
    reference_index = app.library / "cold-storage/archive-references.json"
    _assert_no_symlink_ancestor(reference_index)
    reference_index.parent.mkdir(exist_ok=True)
    if reference_index.exists():
        if app.cold_storage.references() != candidate["archive_references"]:
            raise ColdStorageError("Migration archive-reference index changed")
    else:
        _atomic_json(reference_index, dict(schema=1, references=candidate["archive_references"]))
    if candidate["binding"] != _binding(app):
        raise ColdStorageError("Compact migration binding changed; retain its journal")
    for tree in candidate["trees"]:
        path = app.library / _relative(tree["path"])
        if app.cold_storage.is_cold(path):
            value = app.cold_storage._load(path)
            if value["entries"] != tree["entries"]:
                raise ColdStorageError("Compact migration tree differs from its approved manifest")
            app.cold_storage.recover(path)
            if not app.cold_storage.is_cold(path):
                app.cold_storage.pack(
                    path, expected=tree["entries"], cancelled=cancelled, progress=progress
                )
        elif tree["path"] in pending["completed"]:
            raise ColdStorageError(
                "Previously compacted migration tree was materialized externally"
            )
        else:
            app.cold_storage.pack(
                path, expected=tree["entries"], cancelled=cancelled, progress=progress
            )
        if tree["path"] not in pending["completed"]:
            pending["completed"].append(tree["path"])
            _atomic_json(journal, pending)
    _atomic_json(
        app.library / "compact-migration-receipt.json",
        dict(schema=1, plan=candidate, outcome="compact"),
    )
    _atomic_json(
        app.library / CONFIG,
        dict(
            schema=1, exclude=candidate["exclude"], include_profiles=candidate["include_profiles"]
        ),
    )
    journal.unlink()
    _fsync_dir(app.library)


def rollback(app, candidate, *, cancelled=None, progress=None):
    """Restore exact pre-migration trees; refuse later edits or changed active state."""
    journal = app.library / JOURNAL
    _assert_no_symlink_ancestor(journal)
    if journal.exists():
        pending = _read(journal)
        if pending.get("schema") != 1 or pending.get("plan") != candidate:
            raise ColdStorageError("Another compact migration owns rollback")
    else:
        receipt_path = app.library / "compact-migration-receipt.json"
        _assert_no_symlink_ancestor(receipt_path)
        if not receipt_path.exists() and config(app) is None:
            # Cancellation during read-only preflight has no transaction to undo.
            if candidate == plan(
                app, candidate.get("exclude", ()), candidate.get("include_profiles", ())
            ):
                return
            raise ColdStorageError("Unstarted migration no longer matches its original trees")
        receipt = _read(receipt_path)
        if receipt.get("plan") != candidate or receipt.get("outcome") != "compact":
            raise ColdStorageError("Rollback requires the exact completed migration receipt")
    if candidate["binding"] != _binding(app):
        raise ColdStorageError("Library changed since migration; exact rollback refused")
    # Validate every remaining representation before restoring any tree.
    for tree in candidate["trees"]:
        path = app.library / _relative(tree["path"])
        if app.cold_storage.is_cold(path):
            if app.cold_storage._load(path)["entries"] != tree["entries"]:
                raise ColdStorageError("Cold tree differs from rollback manifest")
        elif app.cold_storage.inspect(path) != tree["entries"]:
            raise ColdStorageError("Working tree changed since migration; rollback refused")
    _atomic_json(journal, dict(schema=1, plan=candidate, direction="rollback"))
    for tree in candidate["trees"]:
        path = app.library / tree["path"]
        app.cold_storage.materialize(path, cancelled=cancelled, progress=progress)
        if app.cold_storage.inspect(path) != tree["entries"]:
            raise ColdStorageError("Rollback readback differs from its approved manifest")
    policy = app.library / CONFIG
    _assert_no_symlink_ancestor(policy)
    if policy.exists():
        policy.unlink()
        _fsync_dir(app.library)
    _atomic_json(
        app.library / "compact-migration-receipt.json",
        dict(schema=1, plan=candidate, outcome="rolled_back"),
    )
    journal.unlink()
    _fsync_dir(app.library)


def warm(app, record, *, readonly=False, cancelled=None, progress=None):
    if record not in app.catalogue():
        raise ColdStorageError("Map is no longer in this library")
    paths = [app._prepared_root(record)]
    if record.imported_directory:
        from .removal import _leaf

        paths.append(_leaf(app.imports, record.imported_directory, r"[0-9a-f]{32}"))
    if app.profiles._state()["active"] != record.profile_id:
        paths.append(app.profiles._profile_path(record.profile_id))
    for path in paths:
        if app.cold_storage.is_cold(path):
            if readonly:
                raise ColdStorageError("Prepare this map before guarded or read-only use")
            app.cold_storage.materialize(path, cancelled=cancelled, progress=progress)


def cool(app, *, keep=None, cancelled=None, progress=None):
    value = config(app)
    if value is None:
        return
    records = app.catalogue()
    active = app.profiles._state()["active"]
    keep_records = [r for r in records if r == keep]
    keep_paths = {app._prepared_root(r) for r in keep_records}
    keep_paths.update(
        app.imports / r.imported_directory for r in keep_records if r.imported_directory
    )
    if keep is not None and keep.profile_id != active:
        keep_paths.add(app.profiles._profile_path(keep.profile_id))
    selected, _ = targets(app, value["exclude"], value.get("include_profiles", ()))
    for _, _, path in selected:
        if path in keep_paths:
            continue
        if app.cold_storage.is_cold(path):
            # Recovery is explicit; ordinary actions refuse a pending cold commit.
            value = app.cold_storage._load(path)
            if (
                value["phase"] != "verified"
                or path.exists()
                or path.with_name(value["backup"]).exists()
            ):
                raise ColdStorageError("Pending cold transaction requires explicit recovery")
        else:
            app.cold_storage.pack(
                path,
                expected=app.cold_storage.inspect(path, cancelled=cancelled),
                cancelled=cancelled,
                progress=progress,
            )
