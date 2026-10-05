"""Recoverable removal of launcher-owned map files, with exact-variant save retention."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import uuid

from .activation import _assert_no_symlink_ancestor, _assert_regular_tree, _atomic_json, _fsync_dir
from .extraction import _unfreeze_directories
from .variants import _tree_manifest


class RemovalError(RuntimeError):
    pass


def _leaf(root: Path, name: str, pattern: str) -> Path:
    if not isinstance(name, str) or not re.fullmatch(pattern, name):
        raise RemovalError("Map storage identity is invalid")
    path = root / name
    _assert_no_symlink_ancestor(path)
    return path


def retained_saves(app, variant: str):
    index = _leaf(app.library / "retained-saves", variant + ".json", r"[0-9a-f]{64}\.json")
    if not index.exists():
        return None
    value = json.loads(index.read_text())
    if value.get("variant_id") != variant:
        raise RemovalError("Retained saves belong to another map version")
    folder = _leaf(index.parent, value["snapshot"], r"[0-9a-f]{32}") / "Saves"
    _assert_no_symlink_ancestor(folder)
    _assert_regular_tree(folder)
    files, directories = _tree_manifest(folder)
    if files != value["files"] or list(directories) != value["directories"]:
        raise RemovalError("Retained save snapshot changed; preserved for inspection")
    return folder


def _targets(app, record, remaining, remove_download, download_sha1):
    targets = [("profile", app.profiles._profile_path(record.profile_id))]
    if not any(r.prepared_directory == record.prepared_directory for r in remaining):
        targets.append(("prepared", _leaf(app.prepared, record.prepared_directory, r"[0-9a-f]{32}")))
    if record.imported_directory and not any(r.imported_directory == record.imported_directory for r in remaining):
        targets.append(("import", _leaf(app.imports, record.imported_directory, r"[0-9a-f]{32}")))
    targets.append(("icon", _leaf(app.icons, record.variant_id + ".png", r"[0-9a-f]{64}\.png")))
    targets.append(("checks", _leaf(app.library / "import-checks", record.variant_id + ".json", r"[0-9a-f]{64}\.json")))
    if remove_download and not any(r.archive_sha256 == record.archive_sha256 for r in remaining):
        targets.append(("archive", _leaf(app.originals, record.archive_sha256 + ".7z", r"[0-9a-f]{64}\.7z")))
        if download_sha1 and not any(r.archive_sha1 == download_sha1 for r in remaining):
            targets.append(("download", _leaf(app.library / "downloads", download_sha1 + ".7z", r"[0-9a-f]{40}\.7z")))
    return targets


def _discard(path: Path):
    _assert_no_symlink_ancestor(path)
    if path.is_dir():
        _assert_regular_tree(path)
        _unfreeze_directories(path)
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def resume(app):
    """Caller holds application lock; all other mutations wait for this journal."""
    from .application import MapRecord
    journal = app.library / "removal.json"
    _assert_no_symlink_ancestor(journal)
    if not journal.exists():
        return
    app._stopped()
    plan = json.loads(journal.read_text())
    if plan.get("schema") != 1 or type(plan.get("remove_download")) is not bool:
        raise RemovalError("Map removal journal needs inspection")
    record = MapRecord.from_json(plan["record"])
    if record.profile_id != "map-" + record.variant_id or record.as_json() not in plan["before"]:
        raise RemovalError("Invalid removal identity")
    stage = _leaf(app.library, ".removing-" + plan["id"], r"\.removing-[0-9a-f]{32}")
    before = plan["before"]
    after = [r for r in before if r["variant_id"] != record.variant_id]
    current = [r.as_json() for r in app.catalogue()]
    if current not in (before, after):
        raise RemovalError("Library changed during map removal; recovery stopped")
    remaining = [MapRecord.from_json(r) for r in after]
    targets = dict(_targets(app, record, remaining, plan["remove_download"], plan["download_sha1"]))
    if not set(plan["targets"]).issubset(targets):
        raise RemovalError("Unexpected removal target")
    with app.profiles._locked():
        app.profiles._require_stopped()
        if app.profiles.journal.exists() or app.profiles._state()["active"] == record.profile_id:
            raise RemovalError("Switch to Original Game and quit Railroads before removing this map")
        if retained_saves(app, record.variant_id) is None:
            raise RemovalError("Retained saves are missing; removal stopped")
        if stage.exists() and not {p.name for p in stage.iterdir()}.issubset(plan["targets"]):
            raise RemovalError("Unexpected files in removal stage; preserved for inspection")
        if current == before:
            for key in plan["targets"]:
                source, destination = targets[key], stage / key
                _assert_no_symlink_ancestor(destination)
                if source.exists() and not destination.exists():
                    if source.is_dir():
                        _assert_regular_tree(source)
                        source.chmod(source.stat().st_mode | 0o700)
                    source.rename(destination)
                    _fsync_dir(source.parent); _fsync_dir(stage)
                elif not source.exists() and destination.exists():
                    pass
                else:
                    raise RemovalError("Ambiguous map removal state; files preserved for inspection")
            _atomic_json(app.catalogue_file, dict(schema=1, maps=after))
        # Once catalogue commit is durable, only this transaction's stage is deleted.
        _discard(stage)
        journal.unlink()
        _fsync_dir(app.library)


def remove(app, record, remove_download=True):
    app._stopped()
    records = list(app.catalogue())
    if record not in records:
        raise RemovalError("Map is no longer in this library")
    app._authorize_profile(record.profile_id)
    if record.profile_id != "map-" + record.variant_id:
        raise RemovalError("Map identity is invalid")
    with app.profiles._locked():
        app.profiles._require_stopped()
        if app.profiles.journal.exists() or app.profiles._state()["active"] == record.profile_id:
            raise RemovalError("Switch to Original Game and quit Railroads before removing this map")
        profile = app.profiles._profile_path(record.profile_id)
        app.profiles._marker(profile, record.profile_id)
        remaining = [r for r in records if r != record]
        download_sha1 = record.archive_sha1
        if not download_sha1:
            archive = _leaf(app.originals, record.archive_sha256 + ".7z", r"[0-9a-f]{64}\.7z")
            if archive.is_file():
                sha256, sha1 = hashlib.sha256(), hashlib.sha1()
                with archive.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b""):
                        sha256.update(chunk); sha1.update(chunk)
                if sha256.hexdigest() != record.archive_sha256:
                    raise RemovalError("Preserved archive changed; removal stopped")
                download_sha1 = sha1.hexdigest()
        targets = [(key, path) for key, path in _targets(app, record, remaining, remove_download, download_sha1) if path.exists()]
        for _, path in targets:
            _assert_no_symlink_ancestor(path)
            if path.is_dir(): _assert_regular_tree(path)
        retained = app.library / "retained-saves"
        _assert_no_symlink_ancestor(retained)
        retained.mkdir(exist_ok=True)
        transaction = uuid.uuid4().hex
        snapshot = retained / transaction
        snapshot.mkdir()
        saves = profile / "Saves"
        _assert_regular_tree(saves)
        before = _tree_manifest(saves)
        shutil.copytree(saves, snapshot / "Saves", copy_function=shutil.copy2)
        if _tree_manifest(snapshot / "Saves") != before or _tree_manifest(saves) != before:
            raise RemovalError("Saved games changed during preservation; removal stopped")
        # Durable files before the pointer/journal permit any destructive work.
        from .activation import _fsync_tree
        _fsync_tree(snapshot)
        _atomic_json(retained / (record.variant_id + ".json"), dict(
            variant_id=record.variant_id, archive_sha256=record.archive_sha256,
            game_executable_sha256=record.game_executable_sha256, snapshot=transaction,
            files=before[0], directories=list(before[1])))
        stage = app.library / (".removing-" + transaction)
        stage.mkdir()
        _atomic_json(app.library / "removal.json", dict(schema=1, id=transaction,
            record=record.as_json(), before=[r.as_json() for r in records],
            remove_download=remove_download, download_sha1=download_sha1, targets=[key for key, _ in targets]))
    resume(app)
    return snapshot / "Saves"
