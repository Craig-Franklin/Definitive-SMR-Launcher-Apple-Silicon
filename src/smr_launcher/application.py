"""Local application workflow around the verified profile and package services."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable, Iterator, Optional
import fcntl
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
import uuid

from .activation import (
    ActivationError,
    FilesystemProfiles,
    MARKER,
    ORIGINAL,
    _assert_no_symlink_ancestor,
    _assert_regular_tree,
    _atomic_json,
    _canonical_directory_path,
    _fsync_dir,
)
from .extraction import _freeze_tree, _publish_exclusive, _unfreeze_directories, extract_package
from .packages import _digest, inspect_package
from .steam import SteamInstallation
from .variants import _tree_manifest, prepare_original_variant, assets_manifest_hash
from .verification import (
    GameplayVerification,
    VerificationStore,
    VerificationSummary,
    reduce_verification,
)
from .scenario_evidence import (
    EvidenceStore,
    EvidenceRecord,
    EvidenceError,
    bind_inventory,
    build_record,
    scenario_key,
    read_bytes,
    MAX_RECEIPT_BYTES,
    engine_context,
    relative,
)
from .collection import RemoteMap
from .map_metadata import MapMetadata, read_map_metadata, read_scenario_labels
from . import removal, compact_library
from .cold_storage import ColdStorage, _checkpoint
from .map_inspection import inspect_map, input_binding
from .launch_preferences import prepare_settings
from .resource_identity import ContentHashCache, fingerprint_resources
from .map_issues import MapIssueStore


class ApplicationError(RuntimeError):
    """A requested launcher operation cannot safely proceed."""


@dataclass(frozen=True)
class MapRecord:
    name: str
    archive_sha256: str
    variant_id: str
    profile_id: str
    scenarios: tuple[str, ...]
    prepared_directory: str
    game_executable_sha256: str
    imported_directory: str = ""
    archive_filename: str = ""
    source_url: str = ""
    source_label: str = "Local archive · origin not recorded"
    archive_modified: str = ""
    imported_at: str = ""
    archive_sha1: str = ""
    recipe_receipt_sha256: str = ""

    @classmethod
    def from_json(cls, value: dict) -> "MapRecord":
        return cls(
            value["name"],
            value["archive_sha256"],
            value["variant_id"],
            value["profile_id"],
            tuple(value["scenarios"]),
            value["prepared_directory"],
            value["game_executable_sha256"],
            value.get("imported_directory", ""),
            value.get("archive_filename", ""),
            value.get("source_url", ""),
            value.get("source_label", "Local archive · origin not recorded"),
            value.get("archive_modified", ""),
            value.get("imported_at", ""),
            value.get("archive_sha1", ""),
            value.get("recipe_receipt_sha256", ""),
        )

    def as_json(self) -> dict:
        result = dict(
            name=self.name,
            archive_sha256=self.archive_sha256,
            variant_id=self.variant_id,
            profile_id=self.profile_id,
            scenarios=list(self.scenarios),
            prepared_directory=self.prepared_directory,
            game_executable_sha256=self.game_executable_sha256,
            imported_directory=self.imported_directory,
            archive_filename=self.archive_filename,
            source_url=self.source_url,
            source_label=self.source_label,
            archive_modified=self.archive_modified,
            imported_at=self.imported_at,
            archive_sha1=self.archive_sha1,
        )
        if self.recipe_receipt_sha256:
            result["recipe_receipt_sha256"] = self.recipe_receipt_sha256
        return result


class LauncherApplication:
    """One user's local library; package archives and prepared inputs stay immutable."""

    def __init__(self, installation: SteamInstallation, library: Path) -> None:
        self.installation = installation
        self.library = _canonical_directory_path(Path(library).expanduser())
        self.managed = self.library / "managed"
        self.baseline = self.library / "clean-baseline"
        self.originals = self.library / "original-archives"
        self.imports = self.library / "imports"
        self.prepared = self.library / "prepared"
        self.icons = self.library / "icons"
        self.cold_storage = ColdStorage(self.library)
        self.catalogue_file = self.library / "catalogue.json"
        self.verification_store = VerificationStore(self.library / "gameplay-verification.json")
        self.scenario_evidence_store = EvidenceStore(self.library / "scenario-evidence.jsonl")
        self._resource_hash_cache = ContentHashCache()
        self.installation_binding = self.library / "installation-binding.json"
        self.profiles = FilesystemProfiles(
            installation.profile_root,
            self.managed,
            ("CustomAssets", "UserMaps"),
            installation.game_running,
            cold_storage=self.cold_storage,
        )

    @classmethod
    def discover(
        cls, home: Optional[Path] = None, steamapps_root: Optional[Path] = None
    ) -> "LauncherApplication":
        home = Path.home() if home is None else Path(home)
        library = home / "Library/Application Support/Definitive SMR Launcher Apple Silicon"
        config = library / "installation.json"
        if steamapps_root is None and config.exists():
            value = json.loads(config.read_text(encoding="utf-8"))
            if value.get("schema") != 1 or not isinstance(value.get("steamapps_root"), str):
                raise ApplicationError("Saved Steam library choice needs inspection")
            steamapps_root = Path(value["steamapps_root"])
        installation = SteamInstallation.discover(home, steamapps_root)
        return cls(installation, library)

    @classmethod
    def choose_steam_library(
        cls, steamapps_root: Path, home: Optional[Path] = None
    ) -> "LauncherApplication":
        home = Path.home() if home is None else Path(home)
        app = cls.discover(home, steamapps_root)
        with app._locked():
            if app.installation_binding.exists() or app.profiles.state_file.exists():
                app._verify_installation_binding()
            _atomic_json(
                app.library / "installation.json",
                dict(schema=1, steamapps_root=str(app.installation.steamapps_root)),
            )
        return app

    @contextmanager
    def _locked(
        self, *, observed_io=None, recover_removal=True, nonblocking=False, allow_compaction=False
    ) -> Iterator[None]:
        _assert_no_symlink_ancestor(self.library)
        self.library.mkdir(parents=True, exist_ok=True)
        call = (lambda name, fn, *args: fn(*args)) if observed_io is None else observed_io.call
        pending = self.library / "removal.json"
        if (observed_io is not None or not recover_removal) and (
            pending.exists() or pending.is_symlink()
        ):
            raise ApplicationError(
                "Pending removal requires its owner; observed runner refuses recovery"
                if observed_io is not None
                else "Pending removal requires its owner; read-only coordination refuses recovery"
            )
        fd = call(
            "application.lock_open",
            os.open,
            self.library / ".application.lock",
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            try:
                call(
                    "application.lock_acquire",
                    fcntl.flock,
                    fd,
                    fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0),
                )
            except BlockingIOError as exc:
                raise ApplicationError(
                    "Launcher application is busy; guarded play refused"
                ) from exc
            migration = self.library / compact_library.JOURNAL
            _assert_no_symlink_ancestor(migration)
            if migration.exists() and not allow_compaction:
                raise ApplicationError("Pending compact migration requires its exact owner")
            if observed_io is None and recover_removal:
                removal.resume(self)
            elif pending.exists() or pending.is_symlink():
                raise ApplicationError("Removal appeared after locking; all evidence retained")
            yield
        finally:
            try:
                call("application.lock_release", fcntl.flock, fd, fcntl.LOCK_UN)
            finally:
                call("application.lock_close", os.close, fd)
        call("application.lock_exit", lambda: None)

    def _is_cold(self, path):
        storage = getattr(self, "cold_storage", None)
        return storage is not None and storage.is_cold(path)

    def plan_compaction(
        self, *, exclude=(), include_profiles=(), cancelled=None, progress=None
    ) -> dict:
        """Read-only exact migration proposal; exclusions retain ownership and bytes."""
        with self._locked(recover_removal=False):
            self._stopped()
            with self.profiles._locked():
                self.profiles._recover_locked(recover_pending=False)
                return compact_library.plan(
                    self, exclude, include_profiles, cancelled=cancelled, progress=progress
                )

    def compact_library(self, plan: dict, *, cancelled=None, progress=None) -> None:
        """Apply one exact migration; never select unknown data or run the game."""
        with self._locked(recover_removal=False, allow_compaction=True):
            self._stopped()
            with self.profiles._locked():
                self.profiles._recover_locked(recover_pending=False)
                compact_library.migrate(self, plan, cancelled=cancelled, progress=progress)

    def rollback_compaction(self, plan: dict, *, cancelled=None, progress=None) -> None:
        """Restore the exact migration under both locks; preserve later edits by refusing."""
        with self._locked(recover_removal=False, allow_compaction=True):
            self._stopped()
            with self.profiles._locked():
                self.profiles._recover_locked(recover_pending=False)
                compact_library.rollback(self, plan, cancelled=cancelled, progress=progress)

    def _warm_record(self, record, *, readonly=False, cancelled=None, progress=None):
        # Caller holds the application lock; materialization also excludes game use.
        if compact_library.config(self) is None:
            return
        with self.profiles._locked(nonblocking=readonly):
            self.profiles._require_stopped()
            self.profiles._recover_locked(recover_pending=False)
            compact_library.warm(
                self, record, readonly=readonly, cancelled=cancelled, progress=progress
            )

    def _cool_library(self, *, keep=None):
        if compact_library.config(self) is None:
            return
        with self.profiles._locked():
            self.profiles._require_stopped()
            self.profiles._recover_locked(recover_pending=False)
            compact_library.cool(self, keep=keep)

    def prepare_map(self, record: MapRecord, *, cancelled=None, progress=None):
        """Prepare one selected map without activating it; old working trees go cold."""
        with self._locked():
            self._stopped()
            self._warm_record(record, cancelled=cancelled, progress=progress)
            with self.profiles._locked():
                self.profiles._require_stopped()
                compact_library.cool(self, keep=record, cancelled=cancelled, progress=progress)
        return record

    def _installation_identity(self) -> dict:
        game = self.installation
        return dict(
            schema=1,
            appid=game.appid,
            steam_buildid=game.steam_buildid,
            bundle_id=game.bundle_id,
            bundle_version=game.bundle_version,
            executable_sha256=game.executable_sha256,
            steamapps_root=str(game.steamapps_root),
        )

    def _verify_installation_binding(self) -> None:
        _assert_no_symlink_ancestor(self.installation_binding)
        if not self.installation_binding.is_file():
            raise ApplicationError(
                "Enrolled game build record is missing; profiles retained for inspection"
            )
        try:
            actual = json.loads(self.installation_binding.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ApplicationError("Enrolled game build record cannot be read") from exc
        if actual != self._installation_identity():
            raise ApplicationError(
                "Steam game build or library changed; existing map profiles need revalidation"
            )

    def _stopped(self) -> None:
        self.installation.check_current()
        if self.installation_binding.exists() or self.profiles.state_file.exists():
            self._verify_installation_binding()
        if self.installation.game_running() is not False:
            raise ApplicationError("Quit Railroads before changing profiles or importing maps")

    def _baseline_is_clean(self) -> None:
        _assert_regular_tree(self.installation.profile_root)
        for name in ("CustomAssets", "UserMaps", "Saves"):
            path = self.installation.profile_root / name
            if not path.is_dir() or path.is_symlink():
                raise ApplicationError("The game profile is missing " + name)
        for name in ("CustomAssets", "UserMaps"):
            if any((self.installation.profile_root / name).iterdir()):
                raise ApplicationError(
                    "Remove custom content from the fresh game before initial setup"
                )

    def _copy_baseline(self) -> None:
        if self.baseline.exists():
            _assert_regular_tree(self.baseline)
            baseline_files, baseline_dirs = _tree_manifest(self.baseline)
            live_files, live_dirs = _tree_manifest(self.installation.profile_root)
            if MARKER in live_files:
                self.profiles._marker(self.profiles.live, ORIGINAL)
                del live_files[MARKER]
            if (baseline_files, baseline_dirs) != (live_files, live_dirs):
                raise ApplicationError("Existing clean baseline differs from the game profile")
            return
        if (self.installation.profile_root / MARKER).exists():
            raise ApplicationError("Clean baseline is missing after profile enrollment began")
        _assert_no_symlink_ancestor(self.library)
        self.library.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".baseline-", dir=self.library))
        try:
            shutil.copytree(
                self.installation.profile_root,
                stage,
                dirs_exist_ok=True,
                copy_function=shutil.copy2,
            )
            if _tree_manifest(stage) != _tree_manifest(self.installation.profile_root):
                raise ApplicationError("Clean baseline changed during the independent copy")
            _freeze_tree(stage)
            _publish_exclusive(stage, self.baseline)
        finally:
            if stage.exists():
                _unfreeze_directories(stage)
                shutil.rmtree(stage)

    def setup(self) -> str:
        """Enroll only a stopped, asset-empty game profile after a stock-game check."""
        with self._locked():
            self._stopped()
            if self.profiles.state_file.exists():
                return self.profiles.recover()
            self._baseline_is_clean()
            self._copy_baseline()
            if self.installation_binding.exists():
                self._verify_installation_binding()
            else:
                _atomic_json(self.installation_binding, self._installation_identity())
            self.profiles.enroll_original()
            return ORIGINAL

    def catalogue(self) -> tuple[MapRecord, ...]:
        if not self.catalogue_file.exists():
            return ()
        value = json.loads(self.catalogue_file.read_text(encoding="utf-8"))
        if value.get("schema") != 1 or not isinstance(value.get("maps"), list):
            raise ApplicationError("Map catalogue needs inspection")
        records = tuple(MapRecord.from_json(item) for item in value["maps"])
        if len({item.variant_id for item in records}) != len(records):
            raise ApplicationError("Map catalogue contains duplicate variants")
        return records

    def _preserve_archive(self, source: Path, digest: str) -> Path:
        self.originals.mkdir(parents=True, exist_ok=True)
        target = self.originals / (digest + ".7z")
        _assert_no_symlink_ancestor(target)
        if target.exists():
            if _digest(target) != digest:
                raise ApplicationError("Preserved archive differs from its identity")
            return target
        fd, temporary_name = tempfile.mkstemp(prefix=".archive-", dir=self.originals)
        stage = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as original:
                shutil.copyfileobj(original, output)
                output.flush()
                os.fsync(output.fileno())
            if _digest(stage) != digest or _digest(source) != digest:
                raise ApplicationError("Source archive changed while preserving it")
            stage.chmod(0o400)
            os.link(stage, target)
            _fsync_dir(self.originals)
            return target
        finally:
            stage.unlink(missing_ok=True)
            _fsync_dir(self.originals)

    def download_archive(self, remote: RemoteMap, *, progress=None, cancelled=None) -> Path:
        """Reuse a SHA-256 canonical archive only after size and SHA-1 checks."""
        from .collection import CollectionError, download_map

        if cancelled and cancelled():
            raise CollectionError("Download canceled")
        with self._locked():
            self._stopped()
            _assert_no_symlink_ancestor(self.originals)
            # Catalogue hits come first. Size filters bound fallback reads for local
            # imports and retained archives whose last catalogue row was removed.
            records = self.catalogue()
            preferred = {r.archive_sha256 for r in records if r.archive_sha1 == remote.sha1}
            candidates = sorted(self.originals.glob("*.7z"), key=lambda p: p.stem not in preferred)
            for path in candidates:
                _assert_no_symlink_ancestor(path)
                if (
                    len(path.stem) != 64
                    or any(c not in "0123456789abcdef" for c in path.stem)
                    or not path.is_file()
                ):
                    raise ApplicationError("Canonical archive ownership needs inspection")
                if path.stem not in preferred and path.stat().st_size != remote.size:
                    continue
                if _digest(path) != path.stem:
                    raise ApplicationError("Canonical archive differs from its SHA-256 identity")
                if self._matches_remote(path, remote):
                    if cancelled and cancelled():
                        raise CollectionError("Download canceled")
                    if progress:
                        progress(remote.size, remote.size)
                    return path
                if path.stem in preferred:
                    raise ApplicationError("Canonical archive differs from collection metadata")
        return download_map(
            remote, self.library / "downloads", progress=progress, cancelled=cancelled
        )

    def _discard_committed_download(
        self, source: Path, digest: str, remote: Optional[RemoteMap]
    ) -> None:
        """The application lock and durable catalogue commit precede this deletion."""
        if remote is None or source != self.library / "downloads" / (remote.sha1 + ".7z"):
            return
        _assert_no_symlink_ancestor(source)
        if not source.exists():
            return
        archive = self.originals / (digest + ".7z")
        _assert_no_symlink_ancestor(archive)
        before = source.stat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or _digest(archive) != digest
            or not self._matches_remote(archive, remote)
            or _digest(source) != digest
            or not self._matches_remote(source, remote)
            or self._collection_archive_identity(source.stat())
            != self._collection_archive_identity(before)
        ):
            raise ApplicationError("Committed download identity changed; retained for inspection")
        source.unlink()
        _fsync_dir(source.parent)

    def import_archive(
        self,
        source: Path,
        *,
        original_filename: Optional[str] = None,
        remote: Optional[RemoteMap] = None,
    ) -> MapRecord:
        with self._locked():
            self._stopped()
            self.profiles.recover()
            source = Path(source).expanduser()
            if original_filename is not None:
                if (
                    not original_filename.lower().endswith(".7z")
                    or len(original_filename) > 255
                    or any(char in original_filename for char in ("/", "\\", ":"))
                    or any(ord(char) < 32 or ord(char) == 127 for char in original_filename)
                ):
                    raise ApplicationError("Original archive name is unsafe")
            display_name = original_filename[:-3] if original_filename is not None else source.stem
            inspection = inspect_package(source)
            if remote is not None:
                if original_filename != remote.name or not self._matches_remote(
                    source.resolve(strict=True), remote
                ):
                    raise ApplicationError("Archive does not match its collection source")
            existing = next(
                (
                    item
                    for item in self.catalogue()
                    if item.archive_sha256 == inspection.source_sha256
                    and item.game_executable_sha256 == self.installation.executable_sha256
                    and self._is_original_variant(item)
                ),
                None,
            )
            if existing is not None:
                result = self._record_source(existing, remote) if remote else existing
                self._discard_committed_download(source, inspection.source_sha256, remote)
                return result
            archive = self._preserve_archive(source, inspection.source_sha256)
            if compact_library.config(self) is not None:
                from .archive_references import archive_references

                references = dict(self.cold_storage.references())
                for digest, reference in archive_references(archive).items():
                    references.setdefault(digest, reference)
                _atomic_json(
                    self.library / "cold-storage/archive-references.json",
                    dict(schema=1, references=references),
                )
            self.imports.mkdir(parents=True, exist_ok=True)
            imported = self.imports / uuid.uuid4().hex
            extract_package(archive, imported, inspection.source_sha256)
            self.prepared.mkdir(parents=True, exist_ok=True)
            prepared_name = uuid.uuid4().hex
            prepared_path = self.prepared / prepared_name
            variant = prepare_original_variant(
                self.baseline,
                imported,
                prepared_path,
                inspection.source_sha256,
                self.installation.executable_sha256,
            )
            self._prepare_icon(imported, variant.variant_id)
            profile_id = self.profiles.register_variant(
                variant.variant_id,
                prepared_path,
                saved_games=removal.retained_saves(self, variant.variant_id),
                cold=compact_library.config(self) is not None,
            )
            record = MapRecord(
                display_name,
                inspection.source_sha256,
                variant.variant_id,
                profile_id,
                inspection.scenarios,
                prepared_name,
                self.installation.executable_sha256,
                imported.name,
                original_filename or source.name,
                remote.source_url if remote else "",
                "Internet Archive · verified archive" if remote else "Local archive",
                remote.archive_modified if remote else "",
                datetime.now(timezone.utc).isoformat(),
                remote.sha1 if remote else "",
            )
            records = list(self.catalogue())
            if any(item.variant_id == record.variant_id for item in records):
                raise ApplicationError("Prepared variant identity already exists in the catalogue")
            self._write_import_checks(record)
            records.append(record)
            _atomic_json(
                self.catalogue_file, dict(schema=1, maps=[item.as_json() for item in records])
            )
            self._discard_committed_download(source, inspection.source_sha256, remote)
            self._cool_library()
            return record

    def _prepared_root(self, record: MapRecord) -> Path:
        return removal._leaf(self.prepared, record.prepared_directory, r"[0-9a-f]{32}")

    def _is_original_variant(self, record: MapRecord) -> bool:
        # Editions share an archive; reimport must select the exact original.
        identity = dict(
            schema=1,
            recipe="original",
            archive=record.archive_sha256,
            game=record.game_executable_sha256,
            assets=(
                self.cold_storage.assets_manifest_hash(self._prepared_root(record))
                if self._is_cold(self._prepared_root(record))
                else assets_manifest_hash(self._prepared_root(record))
            ),
        )
        return (
            hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            == record.variant_id
        )

    def inspect_map_directory(self, source: Path) -> dict:
        """Inspect an author's directory without importing or activating it."""
        source = Path(source)
        if source.is_symlink() or not source.is_dir():
            raise ApplicationError("Choose a nonlinked map directory")
        report = inspect_map(source, Path(self.installation.executable).parents[2])
        executable = report["game"]["executable"]
        if not executable or executable["sha256"] != self.installation.executable_sha256:
            raise ApplicationError(
                "Game executable changed; reverify the installation before inspection"
            )
        return report

    def _import_check_inputs(self, record: MapRecord):
        """Bind authored assets, consumed executable/stock and shared analyzer."""
        root = self._prepared_root(record)
        executable = getattr(self.installation, "executable", None)
        game = Path(executable).parents[2] if executable is not None else None
        binding = input_binding(root, game, scopes=("CustomAssets", "UserMaps"))
        return root, game, binding

    def _write_import_checks(self, record: MapRecord) -> dict:
        if record.game_executable_sha256 != self.installation.executable_sha256:
            raise ApplicationError(
                "Import checks require a map prepared for the current game build"
            )
        root, game, binding = self._import_check_inputs(record)
        if (
            binding["executable"]
            and binding["executable"]["sha256"] != self.installation.executable_sha256
        ):
            raise ApplicationError(
                "Game executable changed; reverify the installation before inspection"
            )
        report = inspect_map(root, game, scopes=("CustomAssets", "UserMaps"))
        report.update(
            schema=2,
            variant_id=record.variant_id,
            archive_sha256=record.archive_sha256,
            game_executable_sha256=record.game_executable_sha256,
            assets_sha256=self.profiles._asset_hash(root),
            checked_at=datetime.now(timezone.utc).isoformat(),
            input_binding=binding,
            input_scope="Prepared CustomAssets/UserMaps only; excludes saves, settings and logs. Consumed executable, loose stock bytes/metadata and analyzer are bound. Candidate resources are not native provider resolution.",
        )
        if self._import_check_inputs(record)[2] != binding:
            raise ApplicationError("Import-check inputs changed during the scan; recheck required")
        path = removal._leaf(
            self.library / "import-checks", record.variant_id + ".json", r"[0-9a-f]{64}\.json"
        )
        path.parent.mkdir(exist_ok=True)
        _atomic_json(path, report)
        return report

    def import_checks(self, record: MapRecord, *, refresh: bool = False) -> Optional[dict]:
        with self._locked():
            if record not in self.catalogue():
                raise ApplicationError("Map is no longer in this library")
            path = removal._leaf(
                self.library / "import-checks", record.variant_id + ".json", r"[0-9a-f]{64}\.json"
            )
            if not refresh and self._is_cold(self._prepared_root(record)):
                return None  # Cold reports are retained, not promoted to current scan evidence.
            if refresh:
                if self._is_cold(self._prepared_root(record)):
                    self._stopped()
                    self._warm_record(record)
                self.installation.check_current()
                return self._write_import_checks(record)
            if not path.exists():
                return None
            report = json.loads(path.read_text())
            if (
                report.get("schema") != 2
                or report.get("schema_version") != 1
                or report.get("status") != "completed"
                or report.get("variant_id") != record.variant_id
                or report.get("archive_sha256") != record.archive_sha256
                or report.get("game_executable_sha256") != self.installation.executable_sha256
                or not isinstance(report.get("input_binding"), dict)
                or report["input_binding"] != self._import_check_inputs(record)[2]
            ):
                return None
            return report

    def remove_map(self, record: MapRecord) -> Path:
        """Remove this map and unshared launcher archives; retain exact-variant saves."""
        with self._locked():
            self._stopped()
            self._warm_record(record)
            result = removal.remove(
                self, record, remove_download=compact_library.config(self) is None
            )
            self._cool_library()
            return result

    @staticmethod
    def _matches_remote(path: Path, remote: RemoteMap) -> bool:
        _assert_no_symlink_ancestor(path)
        if not path.is_file() or path.stat().st_size != remote.size:
            return False
        digest = hashlib.sha1()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest() == remote.sha1

    def _record_source(self, record: MapRecord, remote: RemoteMap) -> MapRecord:
        updated = replace(
            record,
            archive_filename=remote.name,
            source_url=remote.source_url,
            source_label="Internet Archive · verified archive",
            archive_modified=remote.archive_modified,
            archive_sha1=remote.sha1,
        )
        records = [
            updated if item.variant_id == record.variant_id else item for item in self.catalogue()
        ]
        _atomic_json(self.catalogue_file, dict(schema=1, maps=[item.as_json() for item in records]))
        return updated

    @staticmethod
    def _collection_archive_identity(info: os.stat_result) -> tuple:
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    @classmethod
    def _collection_archive_sha1(cls, path: Path, identity: tuple) -> str:
        """Hash a stable regular archive without following linked components."""
        path = path.absolute()
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = None
        try:
            for component in path.parts[1:-1]:
                next_directory = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = next_directory
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                fd = None
                before = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(before.st_mode)
                    or cls._collection_archive_identity(before) != identity
                ):
                    raise ApplicationError("Preserved archive changed during source matching")
                digest = hashlib.sha1()
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(chunk)
                if cls._collection_archive_identity(os.fstat(stream.fileno())) != identity:
                    raise ApplicationError("Preserved archive changed during source matching")
                return digest.hexdigest()
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)

    def match_collection_sources(self, remotes: tuple[RemoteMap, ...]) -> int:
        """Match preserved bytes once per archive, then commit the whole batch.

        Unlike the former per-record writes, inspection errors abort all pending
        matches. The existing atomic writer publishes either the old catalogue
        or the complete new one; an error after rename may leave the new one.
        """
        candidates: dict[tuple[int, str], RemoteMap] = {}
        for remote in remotes:
            candidates.setdefault((remote.size, remote.sha1), remote)
        sizes = {size for size, _ in candidates}
        inspected: dict[Path, tuple[tuple, str]] = {}
        matched = 0
        with self._locked():
            records = list(self.catalogue())
            for index, record in enumerate(records):
                if record.source_url and record.archive_sha1:
                    continue
                archive = self.originals / (record.archive_sha256 + ".7z")
                _assert_no_symlink_ancestor(archive)
                try:
                    info = archive.lstat()
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_size not in sizes:
                    continue
                identity = self._collection_archive_identity(info)
                if archive not in inspected:
                    inspected[archive] = (
                        identity,
                        self._collection_archive_sha1(archive, identity),
                    )
                observed, digest = inspected[archive]
                if observed != identity:
                    raise ApplicationError("Preserved archive changed during source matching")
                remote = candidates.get((info.st_size, digest))
                if remote is not None:
                    records[index] = replace(
                        record,
                        archive_filename=remote.name,
                        source_url=remote.source_url,
                        source_label="Internet Archive · verified archive",
                        archive_modified=remote.archive_modified,
                        archive_sha1=remote.sha1,
                    )
                    matched += 1
            if matched:
                for archive, (identity, _) in inspected.items():
                    _assert_no_symlink_ancestor(archive)
                    if self._collection_archive_identity(archive.lstat()) != identity:
                        raise ApplicationError(
                            "Preserved archive changed before source matching commit"
                        )
                _atomic_json(
                    self.catalogue_file, dict(schema=1, maps=[item.as_json() for item in records])
                )
        return matched

    def map_metadata(self, record: MapRecord) -> MapMetadata:
        # Catalogue paths are metadata, never permission to escape the library.
        directory = record.imported_directory
        if not directory or Path(directory).name != directory or directory in (".", ".."):
            return MapMetadata()
        root = self.imports / directory
        if self._is_cold(root):
            from .map_metadata import parse_map_metadata, MAX_MAP_INFO_BYTES

            return parse_map_metadata(
                self.cold_storage.read_file(root, "mapInfo.txt", MAX_MAP_INFO_BYTES)
            )
        return read_map_metadata(root / "mapInfo.txt")

    def scenario_metadata(self, record: MapRecord) -> list[dict]:
        """Keep every authored label attached to its exact catalogue path."""
        rows = []
        for scenario in record.scenarios:
            labels = dict(title=None, subtitle=None)
            try:
                root = self._prepared_root(record)
                if self._is_cold(root):
                    from .map_metadata import parse_scenario_labels

                    labels = parse_scenario_labels(
                        self.cold_storage.read_file(root, relative(scenario), 8 * 1024**2)
                    )
                else:
                    labels = read_scenario_labels(root / relative(scenario))
            except (EvidenceError, OSError, RuntimeError, ValueError):
                pass  # Unreadable presentation metadata must not prevent browsing.
            rows.append(dict(path=scenario, **labels))
        return rows

    def create_compatibility_edition(
        self, record: MapRecord, rules: tuple, *, label: str
    ) -> MapRecord:
        """Build reviewed exact rules as an independent, explicitly labelled edition."""
        with self._locked():
            self._stopped()
            self._warm_record(record)
            result = self._create_compatibility_edition_locked(record, rules, label=label)
            self._cool_library(keep=result)
            return result

    def create_recipe_edition(
        self,
        record: MapRecord,
        recipe_id: str,
        *,
        expected_plan: Optional[dict] = None,
        reuse_only: bool = False,
    ) -> MapRecord:
        """Compile a reviewed recipe, or read one intact cache without mutation."""
        from .recipe_service import plan_recipe_job, resolve_bundled_recipe

        with self._locked(
            recover_removal=expected_plan is None and not reuse_only, nonblocking=reuse_only
        ):
            self._stopped()
            if (expected_plan is not None or reuse_only) and (
                self.profiles.journal.exists() or self.profiles.journal.is_symlink()
            ):
                raise ApplicationError("Pending activation requires its owner; review Play again")
            if not reuse_only:
                self.profiles.recover()
            self._warm_record(record, readonly=reuse_only or expected_plan is not None)
            if record not in self.catalogue() or not self._is_original_variant(record):
                raise ApplicationError("Recipe compilation requires a current original map")
            if (
                not reuse_only
                and expected_plan is None
                and compact_library.config(self) is not None
            ):
                from .recipe_service import _RECIPES, _donor_record

                recipe = _RECIPES.get(recipe_id)
                if recipe is not None and recipe.additions:
                    self._warm_record(_donor_record(self))
            rules, binding = resolve_bundled_recipe(self, record, recipe_id)
            if expected_plan is not None:
                current = plan_recipe_job(self, record, recipe_id, resolved=(rules, binding))
                if current != expected_plan:
                    raise ApplicationError("Compatibility inputs changed; review Play again")
            if reuse_only or (
                expected_plan is not None and compact_library.config(self) is not None
            ):
                return self._cached_recipe_edition_locked(record, rules, binding)
            result = self._create_compatibility_edition_locked(
                record,
                rules,
                label=recipe_id,
                recipe_binding=binding,
            )
            if expected_plan is None:
                self._cool_library(keep=result)
            return result

    def _cached_recipe_edition_locked(self, original, rules, binding):
        """Read one intact current compiled edition; never prepare or recover."""
        from .recipe_identity import expected_patched_context, map_file_context

        source = removal._leaf(self.imports, original.imported_directory, r"[0-9a-f]{32}")
        if assets_manifest_hash(source) != assets_manifest_hash(self._prepared_root(original)):
            raise ApplicationError("Original import and prepared assets differ")
        context = map_file_context(source, hash_cache=self._resource_hash_cache)
        if context["identity"] != binding.source_files_sha256:
            raise ApplicationError("Recipe source context changed; review Play again")
        expected = {
            **binding.as_json(),
            "output_files_sha256": expected_patched_context(context, rules)["identity"],
        }
        matches = []
        for edition in self.catalogue():
            if (
                not edition.recipe_receipt_sha256
                or edition.archive_sha256 != original.archive_sha256
                or edition.game_executable_sha256 != original.game_executable_sha256
                or edition.imported_directory != original.imported_directory
            ):
                continue
            try:
                receipt = self._validate_recipe_context(edition, prepared_output=True)
            except ApplicationError:
                continue
            if (
                receipt["parent_variant"] == original.variant_id
                and receipt["recipe_binding"] == expected
            ):
                matches.append(edition)
        if len(matches) != 1:
            raise ApplicationError(
                "Finite automatic Play requires one intact cached compatibility edition; "
                "prepare or select the edition in Map Details before Play"
            )
        return matches[0]

    def inspect_bank_candidate(
        self, profile_id: str, *, scenario_path: str, industry_path: str, model_path: str
    ):
        """Inspect one original's source bytes without repair or profile recovery."""
        from .recipe_service import inspect_bank_candidate

        with self._locked(recover_removal=False, nonblocking=True):
            self._stopped()
            if self.profiles.journal.exists() or self.profiles.journal.is_symlink():
                raise ApplicationError("Pending activation requires its owner; inspection refused")
            matches = [r for r in self.catalogue() if r.profile_id == profile_id]
            if len(matches) != 1 or not self._is_original_variant(matches[0]):
                raise ApplicationError("Bank inspection requires a unique current original map")
            self._warm_record(matches[0], readonly=True)
            return inspect_bank_candidate(
                self,
                matches[0],
                scenario_path=scenario_path,
                industry_path=industry_path,
                model_path=model_path,
            )

    def plan_recipe_batch(self, requests: list[dict], *, nonblocking: bool = False) -> dict:
        """Return a bounded compilation snapshot, never an execution ticket.

        Request order is preserved. A repeated profile/recipe pair is refused,
        and one refusal never discards or promotes another job's result.
        """
        from .recipe_service import plan_recipe_job
        from .rules import RuleError
        from .resource_identity import ResourceFingerprintError

        if not isinstance(requests, list) or not 1 <= len(requests) <= 32:
            raise ApplicationError("Recipe plan requires 1 through 32 explicit requests")
        for request in requests:
            if (
                not isinstance(request, dict)
                or set(request) != {"profile_id", "recipe_id"}
                or any(
                    not isinstance(value, str)
                    or not 1 <= len(value) <= 128
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)
                    for value in request.values()
                )
            ):
                raise ApplicationError("Recipe request needs only profile_id and recipe_id strings")
        jobs, seen = [], {}
        with self._locked(recover_removal=False, nonblocking=nonblocking):
            records = self.catalogue()
            for index, request in enumerate(requests):
                job = dict(index=index, **request, status="refused")
                pair = (request["profile_id"], request["recipe_id"])
                if pair in seen:
                    job.update(reason="Duplicate request", duplicate_of=seen[pair])
                else:
                    seen[pair] = index
                    try:
                        matches = [r for r in records if r.profile_id == request["profile_id"]]
                        if len(matches) != 1 or not self._is_original_variant(matches[0]):
                            raise ApplicationError(
                                "Recipe planning requires a unique current original map"
                            )
                        self._warm_record(matches[0], readonly=True)
                        source = removal._leaf(
                            self.imports, matches[0].imported_directory, r"[0-9a-f]{32}"
                        )
                        if assets_manifest_hash(source) != assets_manifest_hash(
                            self._prepared_root(matches[0])
                        ):
                            raise ApplicationError("Original import and prepared assets differ")
                        job.update(plan_recipe_job(self, matches[0], request["recipe_id"]))
                        job["status"] = "supported"
                    except (
                        ApplicationError,
                        ActivationError,
                        RuleError,
                        ResourceFingerprintError,
                        OSError,
                        ValueError,
                    ) as exc:
                        job.update(reason=str(exc), error_type=type(exc).__name__)
                jobs.append(job)
        return dict(
            schema=1,
            snapshot_only=True,
            requires_execution_revalidation=True,
            all_supported=all(job["status"] == "supported" for job in jobs),
            jobs=jobs,
        )

    def prepare_recipe_batch(self, requests: list[dict]) -> dict:
        """Prepare explicit independent editions; never recover, activate or retry.

        Planning is a whole-request preflight, not an execution ticket. Each job
        resolves its current original and recipe again under the application lock.
        Earlier publications remain intact when a later job fails.
        """
        from .recipe_service import resolve_bundled_recipe

        plan = self.plan_recipe_batch(requests)
        jobs = [
            dict(
                index=job["index"],
                profile_id=job["profile_id"],
                recipe_id=job["recipe_id"],
                status="not_attempted",
                preflight=job,
            )
            if job["status"] == "supported"
            else dict(job)
            for job in plan["jobs"]
        ]
        result = dict(schema=1, all_prepared=False, partial=False, jobs=jobs)
        if not plan["all_supported"]:
            return result
        index = 0
        try:
            with self._locked(recover_removal=False):
                try:
                    for index, job in enumerate(jobs):
                        self._stopped()
                        self.profiles.recover(recover_pending=False)
                        records = self.catalogue()
                        matches = [r for r in records if r.profile_id == job["profile_id"]]
                        if len(matches) != 1 or not self._is_original_variant(matches[0]):
                            raise ApplicationError(
                                "Recipe preparation requires a unique current original map"
                            )
                        original = matches[0]
                        rules, binding = resolve_bundled_recipe(self, original, job["recipe_id"])
                        edition = self._create_compatibility_edition_locked(
                            original,
                            rules,
                            label=job["recipe_id"],
                            recipe_binding=binding,
                            recover_pending=False,
                        )
                        job.update(edition=edition.as_json(), reused=edition in records)
                        receipt = self._validate_recipe_context(edition, prepared_output=True)
                        job.update(
                            status="prepared",
                            edition=edition.as_json(),
                            receipt=receipt,
                            binding=receipt["recipe_binding"],
                            reused=edition in records,
                        )
                except Exception as exc:
                    jobs[index].update(
                        status="failed", reason=str(exc), error_type=type(exc).__name__
                    )
                # Recheck every published success at the final shared snapshot.
                for job in jobs:
                    if job["status"] != "prepared":
                        continue
                    try:
                        record = next(r for r in self.catalogue() if r.as_json() == job["edition"])
                        job["receipt"] = self._validate_recipe_context(record, prepared_output=True)
                    except Exception as exc:
                        job.update(
                            status="failed",
                            audit_failed=True,
                            reason=str(exc),
                            error_type=type(exc).__name__,
                        )
        except Exception as exc:
            # A failed job may have retained output under the existing single-job
            # transaction. Never claim rollback or retry that publication.
            jobs[index].update(status="failed", reason=str(exc), error_type=type(exc).__name__)
        result["all_prepared"] = all(job["status"] == "prepared" for job in jobs)
        result["partial"] = any("edition" in job for job in jobs) and not result["all_prepared"]
        return result

    def compatibility_recipes(self, record: MapRecord) -> tuple:
        """Return menu candidates; creation performs the full input validation."""
        from .recipe_service import recipe_choices

        if record not in self.catalogue():
            return ()
        return recipe_choices(record, self.installation.executable_sha256)

    def _create_compatibility_edition_locked(
        self,
        record: MapRecord,
        rules: tuple,
        *,
        label: str,
        recipe_binding=None,
        recover_pending: bool = True,
    ) -> MapRecord:
        """Shared preparation transaction; caller must hold the application lock.

        Recipe callers must additionally establish applicability and effective
        base context. This private helper does not select or enable recipes.
        """
        from .rules import CompatibilityRule, RuleError, applicable_rules
        from .variants import prepare_compatibility_variant, prepare_recipe_variant
        from .recipe_identity import RecipePreparationBinding, map_file_context

        if (
            not isinstance(label, str)
            or not label.strip()
            or len(label) > 120
            or any(ord(c) < 32 or ord(c) == 127 for c in label)
        ):
            raise ApplicationError("Compatibility edition needs a short descriptive label")
        rules = tuple(rules)
        if not rules or any(not isinstance(rule, CompatibilityRule) for rule in rules):
            raise ApplicationError("Compatibility edition needs explicit reviewed rules")
        if recipe_binding is not None and not isinstance(recipe_binding, RecipePreparationBinding):
            raise ApplicationError("Compiled edition needs a valid recipe binding")
        self._stopped()
        self.profiles.recover(recover_pending=recover_pending)
        records = list(self.catalogue())
        if (
            record not in records
            or record.game_executable_sha256 != self.installation.executable_sha256
            or not self._is_original_variant(record)
        ):
            raise ApplicationError("Compatibility preparation requires a current original map")
        selected = applicable_rules(rules, record.archive_sha256, record.game_executable_sha256)
        if len(selected) != len(rules):
            raise RuleError("Every selected rule must match this archive and game build")
        self._warm_record(record)
        imported = removal._leaf(self.imports, record.imported_directory, r"[0-9a-f]{32}")
        _assert_regular_tree(imported)
        input_assets = assets_manifest_hash(imported)
        if input_assets != assets_manifest_hash(self._prepared_root(record)):
            raise ApplicationError(
                "Original import and prepared assets differ; retained for inspection"
            )
        prepared_name = uuid.uuid4().hex
        destination = self.prepared / prepared_name
        if recipe_binding is not None:
            if self._stock_file_context()["identity"] != recipe_binding.stock_files_sha256:
                raise ApplicationError(
                    "Recipe stock resource context differs; revalidate preparation"
                )
            variant = prepare_recipe_variant(
                self.baseline,
                imported,
                destination,
                record.archive_sha256,
                record.game_executable_sha256,
                selected,
                recipe_binding,
            )
            if self._stock_file_context()["identity"] != recipe_binding.stock_files_sha256:
                raise ApplicationError(
                    "Recipe stock context changed during preparation; output retained"
                )
        else:
            variant = prepare_compatibility_variant(
                self.baseline,
                imported,
                destination,
                record.archive_sha256,
                record.game_executable_sha256,
                selected,
            )
        if assets_manifest_hash(imported) != input_assets:
            raise ApplicationError(
                "Original assets changed during preparation; output retained for inspection"
            )
        existing = next((item for item in records if item.variant_id == variant.variant_id), None)
        if existing is not None:
            self._warm_record(existing)
            if recipe_binding is not None:
                self._validate_recipe_context(existing)
            _unfreeze_directories(destination)
            shutil.rmtree(destination)
            return existing
        profile_id = self.profiles.register_variant(
            variant.variant_id,
            destination,
            saved_games=removal.retained_saves(self, variant.variant_id),
            recover_pending=recover_pending,
        )
        if recipe_binding is not None:
            profile = (
                self.profiles.live
                if self.profiles._state()["active"] == profile_id
                else self.profiles._profile_path(profile_id)
            )
            if (
                map_file_context(profile)["identity"]
                != variant.recipe_binding["output_files_sha256"]
            ):
                raise ApplicationError(
                    "Registered recipe resource metadata differs; output retained"
                )
        new = replace(
            record,
            name=record.name + " — Experimental " + label.strip(),
            variant_id=variant.variant_id,
            profile_id=profile_id,
            prepared_directory=prepared_name,
            imported_at=datetime.now(timezone.utc).isoformat(),
        )
        # Store original metadata, hashes and rationale, never game/map bytes.
        receipt = dict(
            schema=1,
            parent_variant=record.variant_id,
            variant_id=variant.variant_id,
            assets_sha256=variant.assets_sha256,
            archive_sha256=record.archive_sha256,
            game_executable_sha256=record.game_executable_sha256,
            rules=[
                dict(
                    id=rule.rule_id,
                    version=rule.version,
                    provenance=rule.provenance,
                    reason=rule.reason,
                    files=[
                        dict(path=p.path, before=p.before_sha256, after=p.after_sha256)
                        for p in rule.patches
                    ],
                )
                for rule in selected
            ],
        )
        if variant.recipe_binding is not None:
            receipt.update(schema=2, recipe_binding=variant.recipe_binding)
            new = replace(new, recipe_receipt_sha256=self._recipe_receipt_hash(receipt))
        receipts = self.library / "compatibility-preparations"
        _assert_no_symlink_ancestor(receipts)
        receipts.mkdir(exist_ok=True)
        _atomic_json(receipts / (variant.variant_id + ".json"), receipt)
        self._prepare_icon(imported, variant.variant_id)
        self._write_import_checks(new)
        records.append(new)
        _atomic_json(self.catalogue_file, dict(schema=1, maps=[r.as_json() for r in records]))
        return new

    @staticmethod
    def _recipe_receipt_hash(receipt: dict) -> str:
        return hashlib.sha256(
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "ascii"
            )
        ).hexdigest()

    def _stock_file_context(self) -> dict:
        from .recipe_identity import file_context

        stock = (
            self.installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        )
        return file_context(
            {"installed_assets": stock},
            required_roles=("installed_assets",),
            hash_cache=self._resource_hash_cache,
        )

    def _validate_recipe_context(
        self, record: MapRecord, *, prepared_output=False
    ) -> Optional[dict]:
        """Reject missing/changed receipts or resource context before activation."""
        if not record.recipe_receipt_sha256:
            return
        from .recipe_identity import map_file_context
        from .resource_identity import ResourceFingerprintError

        path = self.library / "compatibility-preparations" / (record.variant_id + ".json")
        _assert_no_symlink_ancestor(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
                    raise ValueError("receipt is not a bounded regular file")
                chunks, size = [], 0
                while size <= 1024 * 1024:
                    chunk = os.read(fd, min(65536, 1024 * 1024 + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                same = lambda st: (
                    st.st_dev,
                    st.st_ino,
                    st.st_mode,
                    st.st_size,
                    st.st_mtime_ns,
                    st.st_ctime_ns,
                )
                if (
                    size != before.st_size
                    or same(before) != same(os.fstat(fd))
                    or same(before) != same(path.lstat())
                ):
                    raise ValueError("receipt changed while reading")
                receipt = json.loads(b"".join(chunks))
            finally:
                os.close(fd)
            if (
                not isinstance(receipt, dict)
                or receipt.get("schema") != 2
                or receipt.get("variant_id") != record.variant_id
                or receipt.get("archive_sha256") != record.archive_sha256
                or receipt.get("game_executable_sha256") != record.game_executable_sha256
                or self._recipe_receipt_hash(receipt) != record.recipe_receipt_sha256
            ):
                raise ValueError("receipt binding differs")
            binding = receipt["recipe_binding"]
            root = (
                self.profiles.live
                if self.profiles._state()["active"] == record.profile_id
                else self.profiles._profile_path(record.profile_id)
            )
            if (
                map_file_context(root, hash_cache=self._resource_hash_cache)["identity"]
                != binding["output_files_sha256"]
                or self._stock_file_context()["identity"] != binding["stock_files_sha256"]
            ):
                raise ValueError("resource context differs")
            if prepared_output:
                originals = [
                    item
                    for item in self.catalogue()
                    if item.variant_id == receipt.get("parent_variant")
                ]
                if (
                    len(originals) != 1
                    or not self._is_original_variant(originals[0])
                    or originals[0].archive_sha256 != record.archive_sha256
                    or originals[0].game_executable_sha256 != record.game_executable_sha256
                    or originals[0].imported_directory != record.imported_directory
                ):
                    raise ValueError("current original binding differs")
                source = removal._leaf(self.imports, record.imported_directory, r"[0-9a-f]{32}")
                if map_file_context(source)["identity"] != binding["source_files_sha256"]:
                    raise ValueError("current source context differs")
                prepared = self._prepared_root(record)
                if (
                    assets_manifest_hash(prepared) != receipt["assets_sha256"]
                    or map_file_context(prepared)["identity"] != binding["output_files_sha256"]
                ):
                    raise ValueError("prepared output differs")
            return receipt
        except (OSError, ValueError, KeyError, TypeError, ResourceFingerprintError) as exc:
            raise ApplicationError(
                "Compiled map resources or receipt changed; revalidate this edition before playing"
            ) from exc

    def create_edition(self, record: MapRecord, *, editor: bool, difficulty: bool) -> MapRecord:
        """Prepare and register an opt-in edition without activating it or copying saves."""
        from .editions import SUPPORTED_GAME
        from .variants import _prepare_variant

        if editor:
            raise ApplicationError(
                "Editor saving needs an isolated export workflow before it can be enabled safely"
            )
        if record.recipe_receipt_sha256:
            raise ApplicationError(
                "Options on a compiled map require a separately reviewed combined recipe"
            )
        with self._locked():
            self._stopped()
            records = list(self.catalogue())
            if record not in records or record.game_executable_sha256 != SUPPORTED_GAME:
                raise ApplicationError(
                    "Edition options require the inspected Steam Mac game build and a current library map"
                )
            if Path(record.prepared_directory).name != record.prepared_directory:
                raise ApplicationError("Invalid prepared profile path")
            self._warm_record(record)
            source = self.prepared / record.prepared_directory
            _assert_no_symlink_ancestor(source)
            prepared_name = uuid.uuid4().hex
            destination = self.prepared / prepared_name
            options = dict(editor=editor, difficulty=difficulty, parent=record.variant_id)
            variant = _prepare_variant(
                source,
                source,
                destination,
                record.archive_sha256,
                record.game_executable_sha256,
                (),
                options,
            )
            existing = next((r for r in records if r.variant_id == variant.variant_id), None)
            if existing:
                # Only this newly created independent output is removed.
                _unfreeze_directories(destination)
                shutil.rmtree(destination)
                return existing
            profile_id = self.profiles.register_variant(
                variant.variant_id,
                destination,
                saved_games=removal.retained_saves(self, variant.variant_id),
            )
            label = " + ".join(
                name
                for name, enabled in (("Editor", editor), ("Custom difficulty", difficulty))
                if enabled
            )
            new = replace(
                record,
                name=record.name + " — Experimental " + label,
                variant_id=variant.variant_id,
                profile_id=profile_id,
                prepared_directory=prepared_name,
                imported_at=datetime.now(timezone.utc).isoformat(),
            )
            self._write_import_checks(new)
            records.append(new)
            _atomic_json(self.catalogue_file, dict(schema=1, maps=[r.as_json() for r in records]))
            self._cool_library(keep=new)
            return new

    def _prepare_icon(self, imported: Path, variant_id: str) -> None:
        source = imported / "mapIcon.jpg"
        if not source.is_file() or source.is_symlink() or source.stat().st_size > 16 * 1024 * 1024:
            return
        self.icons.mkdir(parents=True, exist_ok=True)
        output = self.icons / (variant_id + ".png")
        if output.exists():
            _assert_no_symlink_ancestor(output)
            return
        fd, name = tempfile.mkstemp(prefix=".icon-", suffix=".png", dir=self.icons)
        os.close(fd)
        temporary = Path(name)
        try:
            result = subprocess.run(
                [
                    "/usr/bin/sips",
                    "-s",
                    "format",
                    "png",
                    "-Z",
                    "360",
                    str(source),
                    "--out",
                    str(temporary),
                ],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if result.returncode or not temporary.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                return
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            os.link(temporary, output)
            _fsync_dir(self.icons)
        except (OSError, subprocess.TimeoutExpired):
            pass  # An invalid optional thumbnail does not invalidate the map.
        finally:
            temporary.unlink(missing_ok=True)
            _fsync_dir(self.icons)

    def map_icon(self, record: MapRecord) -> Optional[Path]:
        if len(record.variant_id) != 64 or any(
            char not in "0123456789abcdef" for char in record.variant_id
        ):
            return None
        path = self.icons / (record.variant_id + ".png")
        if any(part.is_symlink() for part in (path, *path.parents)):
            return None
        if path.is_file():
            return path
        return None

    def _resource_snapshot(self, profile: Path) -> dict:
        stock = (
            self.installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        )
        return fingerprint_resources(
            {
                "installed_assets": stock,
                "custom_assets": profile / "CustomAssets",
                "user_maps": profile / "UserMaps",
            },
            hash_cache=self._resource_hash_cache,
        )

    def _legacy_verification_for(
        self, record: MapRecord, *, nonblocking: bool = False
    ) -> Optional[GameplayVerification]:
        """Return gameplay evidence only for the current build and exact live assets."""
        candidates = [
            item
            for item in self.verification_store.records()
            if (item.archive_sha256, item.game_executable_sha256, item.variant_id)
            == (record.archive_sha256, record.game_executable_sha256, record.variant_id)
        ]
        if not candidates:
            return None
        with self._locked(recover_removal=not nonblocking, nonblocking=nonblocking):
            self._verify_installation_binding()
            if self.profiles.journal.exists():
                return None
            state = self.profiles._state()
            root = (
                self.profiles.live
                if state["active"] == record.profile_id
                else self.profiles._profile_path(record.profile_id)
            )
            if self._is_cold(root):
                marker = json.loads(self.cold_storage.read_file(root, MARKER, 64 * 1024))
                if marker.get("profile_id") != record.profile_id:
                    raise ApplicationError("Cold profile identity differs from catalogue")
                return self.verification_store.latest(
                    record.archive_sha256,
                    self.installation.executable_sha256,
                    record.variant_id,
                    marker["assets_sha256"],
                )
            marker = self.profiles._marker(root, record.profile_id)
            latest = self.verification_store.latest(
                record.archive_sha256,
                self.installation.executable_sha256,
                record.variant_id,
                marker["assets_sha256"],
            )
            if latest is None or not latest.resources_sha256:
                # Preserve limited historical warnings without assigning scenarios.
                # Resource metadata alone cannot establish named action coverage.
                return latest
            current = self._resource_snapshot(root)
            return self.verification_store.latest(
                record.archive_sha256,
                self.installation.executable_sha256,
                record.variant_id,
                marker["assets_sha256"],
                resources_sha256=current["identity"],
            )

    def bind_scenario_inventory(self, path: Path, expected_sha256: str) -> str:
        """Bind a coordinator-accepted immutable inventory without importing old facts."""
        return self.scenario_evidence_store.append(bind_inventory(path, expected_sha256))

    def _scenario_context(self, record: MapRecord, options: Optional[dict] = None):
        if record not in self.catalogue():
            raise EvidenceError("Exact complete catalogue edition required")
        self._verify_installation_binding()
        self.installation.check_current()
        if self.profiles.journal.exists():
            raise EvidenceError("Profile recovery prevents current evidence claims")
        state = self.profiles._state()
        root = (
            self.profiles.live
            if state["active"] == record.profile_id
            else self.profiles._profile_path(record.profile_id)
        )
        if self._is_cold(root):
            raise EvidenceError("Prepare this map before binding current gameplay evidence")
        marker = self.profiles._marker(root, record.profile_id)
        resources = self._resource_snapshot(root)
        scenarios = []
        for name in record.scenarios:
            from .scenario_evidence import relative

            relative(name)
            content = read_bytes(root / name, MAX_RECEIPT_BYTES)
            scenarios.append(
                scenario_key(
                    record.archive_sha256,
                    record.variant_id,
                    name,
                    hashlib.sha256(content).hexdigest(),
                )
            )
        context = engine_context(
            self.installation.executable_sha256, marker["assets_sha256"], resources, options
        )
        return scenarios, context, resources, marker

    def prepare_scenario_evidence_build(
        self, record: MapRecord, inventory_id: str, *, options: dict
    ) -> EvidenceRecord:
        """Capture current exact inputs for a future explicit observation runner."""
        with self._locked():
            self._stopped()
            if record not in self.catalogue():
                raise EvidenceError("Map is absent from catalogue")
            scenarios, context, resources, marker = self._scenario_context(record, options)
            inventories = {
                r.id: r for r in self.scenario_evidence_store.records() if r.kind == "inventory"
            }
            if inventory_id not in inventories:
                raise EvidenceError("Accepted inventory record is absent")
            inventory = inventories[inventory_id]
            if any(s not in inventory.payload["scenarios"] for s in scenarios):
                raise EvidenceError("Current scenario bytes differ from accepted inventory")
            build = build_record(
                inventory,
                record.archive_sha256,
                record.variant_id,
                marker["assets_sha256"],
                resources,
                context["game_sha256"],
                options,
            )
            self.scenario_evidence_store.append(build)
            return build

    def record_scenario_action(
        self, observation: EvidenceRecord, interpretation: EvidenceRecord
    ) -> str:
        """Explicit offline runner entry point; observations cannot imply broad coverage."""
        if observation.kind != "observation":
            raise EvidenceError("An exact raw observation is required")
        # Raw facts are a durability barrier before analysis/restoration. Keep
        # them even if validation, analysis or the second append fails. Pending
        # raw facts conservatively block earlier passes in the shared reducer.
        self.scenario_evidence_store.append(observation)
        if (
            interpretation.kind != "interpretation"
            or interpretation.payload["observation"] != observation.id
        ):
            raise EvidenceError("Interpretation must reference the recorded observation")
        self.scenario_evidence_store.append(interpretation)
        return observation.id

    def scenario_status_for(
        self, record: MapRecord, *, options: Optional[dict] = None, nonblocking: bool = False
    ) -> VerificationSummary:
        issues = MapIssueStore(self.library).for_map(record, self.installation.executable_sha256)
        records = self.scenario_evidence_store.records()
        legacy = self._legacy_verification_for(record, nonblocking=nonblocking)
        historical = tuple(
            r
            for r in self.verification_store.records()
            if legacy is not None
            and r.archive_sha256 == legacy.archive_sha256
            and r.variant_id == legacy.variant_id
            and r.game_executable_sha256 == legacy.game_executable_sha256
            and r.assets_sha256 == legacy.assets_sha256
            and (not r.resources_sha256 or r.resources_sha256 == legacy.resources_sha256)
        )
        cold = self.profiles._state()["active"] != record.profile_id and self._is_cold(
            self.profiles._profile_path(record.profile_id)
        )
        if not records or cold:
            scenarios = [
                dict(
                    archive_sha256=record.archive_sha256,
                    edition_id=record.variant_id,
                    scenario_path=name,
                    scenario_sha256="",
                )
                for name in record.scenarios
            ]
            result = reduce_verification((), scenarios, {}, historical=historical)
        else:
            with self._locked(recover_removal=not nonblocking, nonblocking=nonblocking):
                scenarios, context, _, _ = self._scenario_context(record, options)
                result = reduce_verification(records, scenarios, context, historical=historical)
        return replace(result, status="Known issue", local_issues=issues) if issues else result

    def verification_for(
        self, record: MapRecord, *, options: Optional[dict] = None, nonblocking: bool = False
    ):
        """All views share scenario reduction; legacy results remain limited facts."""
        if (
            MapIssueStore(self.library).for_map(record, self.installation.executable_sha256)
            or self.scenario_evidence_store.path.exists()
            or self._legacy_verification_for(record, nonblocking=nonblocking) is not None
        ):
            return self.scenario_status_for(record, options=options, nonblocking=nonblocking)
        return None

    def catalogue_verification(self, *, options: Optional[dict] = None) -> dict:
        return {
            r.variant_id: self.scenario_status_for(r, options=options) for r in self.catalogue()
        }

    def record_gameplay(
        self,
        record: MapRecord,
        observed_at: str,
        reporter: str,
        checks: dict[str, bool],
        scope: str,
        issue: str = "",
    ) -> GameplayVerification:
        """Record an externally observed stopped-game result for the active map."""
        with self._locked():
            self._stopped()
            if record not in self.catalogue() or self.profiles.journal.exists():
                raise ApplicationError("Map is absent or a profile switch needs recovery")
            self._authorize_profile(record.profile_id)
            if self.profiles._state()["active"] != record.profile_id:
                raise ApplicationError("Gameplay result must be recorded for the active map")
            marker = self.profiles._marker(self.profiles.live, record.profile_id)
            resources = self._resource_snapshot(self.profiles.live)
            verification = GameplayVerification(
                record.archive_sha256,
                self.installation.executable_sha256,
                record.variant_id,
                marker["assets_sha256"],
                observed_at,
                reporter,
                checks,
                scope,
                issue,
                resources["identity"],
            )
            receipt = self.library / "verification-resources" / (resources["identity"] + ".json")
            _assert_no_symlink_ancestor(receipt)
            receipt.parent.mkdir(exist_ok=True, mode=0o700)
            _atomic_json(receipt, resources)
            self.verification_store.append(verification)
            return verification

    def active(self) -> str:
        with self._locked():
            self._stopped()
            return self.profiles.active_profile()

    def _authorize_profile(self, profile_id: str) -> None:
        if profile_id == ORIGINAL:
            return
        record = next((item for item in self.catalogue() if item.profile_id == profile_id), None)
        if record is None:
            raise ApplicationError("Choose an imported map")
        if record.game_executable_sha256 != self.installation.executable_sha256:
            raise ApplicationError(
                "This map was prepared for another game build; preserve its saves and revalidate"
            )
        self._validate_recipe_context(record)

    def activate(self, profile_id: str) -> str:
        with self._locked():
            self._stopped()
            self.profiles.recover()
            if profile_id != ORIGINAL:
                record = next((r for r in self.catalogue() if r.profile_id == profile_id), None)
                if record is not None:
                    self._warm_record(record)
            self._authorize_profile(profile_id)
            result = self.profiles.switch(profile_id)
            self._cool_library()
            return result

    def play(
        self,
        profile_id: str,
        launch: Optional[Callable[[], None]] = None,
        *,
        profile_copy: Optional[str] = None,
        mutation_guard=None,
        startup_seconds: float = 30.0,
        cancelled=None,
        progress=None,
    ) -> str:
        """Use the app launch path, optionally on a guarded isolated asset copy.

        The catalogue profile remains the authorization source. A finite owner
        may select a distinct registered copy with identical prepared assets;
        settings and saves stay in that copy. Its supplied launch callback must
        retain bounded child tracking and the final input/cancellation fence.
        The CLI and UI retain their ordinary catalogue-only behavior.
        """
        if mutation_guard is not None and launch is None:
            raise ApplicationError("Guarded play requires an owned launch callback")
        if profile_copy is not None and (
            mutation_guard is None or profile_id == ORIGINAL or profile_copy == profile_id
        ):
            raise ApplicationError("An isolated map copy requires a guarded launch owner")
        if (
            type(startup_seconds) not in (int, float)
            or not math.isfinite(startup_seconds)
            or not 0 < startup_seconds <= 30
        ):
            raise ApplicationError("Startup observation must be finite and at most 30 seconds")
        target_id = profile_id if profile_copy is None else profile_copy
        with self._locked(
            recover_removal=mutation_guard is None, nonblocking=mutation_guard is not None
        ):
            self._stopped()
            if mutation_guard is None:
                self.profiles.recover()
            elif self.profiles.journal.exists() or self.profiles.journal.is_symlink():
                raise ApplicationError("Guarded play refuses pending activation recovery")
            if profile_id != ORIGINAL:
                record = next((r for r in self.catalogue() if r.profile_id == profile_id), None)
                if record is not None:
                    self._warm_record(
                        record,
                        readonly=mutation_guard is not None,
                        cancelled=cancelled,
                        progress=progress,
                    )
            _checkpoint(cancelled)
            self._authorize_profile(profile_id)
            settings_before = settings_after = None
            if profile_id != ORIGINAL:
                records = self.catalogue()
                if profile_copy is not None and any(r.profile_id == profile_copy for r in records):
                    raise ApplicationError("An isolated copy cannot be a catalogue profile")
                record = next(r for r in records if r.profile_id == profile_id)
                root = (
                    self.profiles.live
                    if self.profiles._state()["active"] == target_id
                    else self.profiles._profile_path(target_id)
                )
                if profile_copy is not None:
                    marker = self.profiles._marker(root, target_id)
                    source_root = (
                        self.profiles.live
                        if self.profiles._state()["active"] == profile_id
                        else self.profiles._profile_path(profile_id)
                    )
                    source_marker = self.profiles._marker(source_root, profile_id)
                    prepared = self.prepared / record.prepared_directory
                    _assert_no_symlink_ancestor(prepared)
                    _assert_regular_tree(prepared)
                    if (
                        marker.get("variant_id") != target_id[4:]
                        or marker["assets_sha256"] != source_marker["assets_sha256"]
                        or marker["assets_sha256"] != self.profiles._asset_hash(prepared)
                    ):
                        raise ApplicationError(
                            "Isolated profile assets differ from the selected map"
                        )
                for scenario in record.scenarios:
                    candidate = root / scenario
                    _assert_no_symlink_ancestor(candidate)
                    if not candidate.is_file() or not candidate.resolve().is_relative_to(
                        root.resolve()
                    ):
                        raise ApplicationError("The selected map's scenario file needs inspection")
                settings = root / "Settings.ini"
                _assert_no_symlink_ancestor(settings)
                if not settings.is_file() or settings.stat().st_size > 256 * 1024:
                    raise ApplicationError("The selected map's settings need inspection")
                settings_before = settings.read_bytes()
                settings_after = prepare_settings(settings_before, record.scenarios)
            if launch is None:
                bundle = self.installation.executable.parents[2]
                launch = lambda: subprocess.Popen(["/usr/bin/open", "-a", str(bundle)])

            def configured_launch() -> None:
                _checkpoint(cancelled)
                # Called under both locks, after the complete profile switch and
                # stopped-game check. Frozen prepared inputs and saves stay intact.
                with mutation_guard() if mutation_guard is not None else nullcontext():
                    if mutation_guard is not None:
                        self._stopped()
                    if settings_after is not None and settings_after != settings_before:
                        target = self.profiles.live / "Settings.ini"
                        _assert_no_symlink_ancestor(target)
                        if target.read_bytes() != settings_before:
                            raise ApplicationError(
                                "Map settings changed before launch; retry after inspection"
                            )
                        temporary = None
                        try:
                            with tempfile.NamedTemporaryFile(
                                dir=target.parent, prefix=".launch-settings-", delete=False
                            ) as stream:
                                temporary = Path(stream.name)
                                stream.write(settings_after)
                                stream.flush()
                                os.fsync(stream.fileno())
                            os.chmod(temporary, target.stat().st_mode & 0o777)
                            os.replace(temporary, target)
                            _fsync_dir(target.parent)
                        finally:
                            if temporary is not None and temporary.exists():
                                temporary.unlink()
                if mutation_guard is None:
                    # Both locks remain held, and the game has not been dispatched.
                    self.profiles._require_stopped()
                    compact_library.cool(self, cancelled=cancelled, progress=progress)
                _checkpoint(cancelled)
                # The finite callback owns its final dispatch fence. Do not hold
                # STATE here across its blocking child wait or acquire it twice.
                launch()

            return self.profiles.play(
                target_id,
                configured_launch,
                startup_seconds=startup_seconds,
                mutation_guard=mutation_guard,
            )
