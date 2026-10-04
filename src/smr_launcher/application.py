"""Local application workflow around the verified profile and package services."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid

from .activation import FilesystemProfiles, MARKER, ORIGINAL, _assert_no_symlink_ancestor, _assert_regular_tree, _atomic_json, _canonical_directory_path, _fsync_dir
from .extraction import _freeze_tree, _publish_exclusive, _unfreeze_directories, extract_package
from .packages import _digest, inspect_package
from .steam import SteamInstallation
from .variants import _tree_manifest, prepare_original_variant
from .verification import GameplayVerification, VerificationStore
from .collection import RemoteMap
from .map_metadata import MapMetadata, read_map_metadata


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

    @classmethod
    def from_json(cls, value: dict) -> "MapRecord":
        return cls(
            value["name"], value["archive_sha256"], value["variant_id"],
            value["profile_id"], tuple(value["scenarios"]),
            value["prepared_directory"], value["game_executable_sha256"],
            value.get("imported_directory", ""),
            value.get("archive_filename", ""), value.get("source_url", ""),
            value.get("source_label", "Local archive · origin not recorded"),
            value.get("archive_modified", ""), value.get("imported_at", ""),
        )

    def as_json(self) -> dict:
        return dict(name=self.name, archive_sha256=self.archive_sha256,
                    variant_id=self.variant_id, profile_id=self.profile_id,
                    scenarios=list(self.scenarios), prepared_directory=self.prepared_directory,
                    game_executable_sha256=self.game_executable_sha256,
                    imported_directory=self.imported_directory,
                    archive_filename=self.archive_filename, source_url=self.source_url,
                    source_label=self.source_label, archive_modified=self.archive_modified,
                    imported_at=self.imported_at)



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
        self.catalogue_file = self.library / "catalogue.json"
        self.verification_store = VerificationStore(self.library / "gameplay-verification.json")
        self.installation_binding = self.library / "installation-binding.json"
        self.profiles = FilesystemProfiles(
            installation.profile_root, self.managed,
            ("CustomAssets", "UserMaps"), installation.game_running,
        )

    @classmethod
    def discover(cls, home: Optional[Path] = None, steamapps_root: Optional[Path] = None) -> "LauncherApplication":
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
    def choose_steam_library(cls, steamapps_root: Path, home: Optional[Path] = None) -> "LauncherApplication":
        home = Path.home() if home is None else Path(home)
        app = cls.discover(home, steamapps_root)
        with app._locked():
            if app.installation_binding.exists() or app.profiles.state_file.exists():
                app._verify_installation_binding()
            _atomic_json(app.library / "installation.json", dict(schema=1, steamapps_root=str(app.installation.steamapps_root)))
        return app

    @contextmanager
    def _locked(self) -> Iterator[None]:
        _assert_no_symlink_ancestor(self.library)
        self.library.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.library / ".application.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _installation_identity(self) -> dict:
        game = self.installation
        return dict(schema=1, appid=game.appid, steam_buildid=game.steam_buildid,
                    bundle_id=game.bundle_id, bundle_version=game.bundle_version,
                    executable_sha256=game.executable_sha256,
                    steamapps_root=str(game.steamapps_root))

    def _verify_installation_binding(self) -> None:
        _assert_no_symlink_ancestor(self.installation_binding)
        if not self.installation_binding.is_file():
            raise ApplicationError("Enrolled game build record is missing; profiles retained for inspection")
        try:
            actual = json.loads(self.installation_binding.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ApplicationError("Enrolled game build record cannot be read") from exc
        if actual != self._installation_identity():
            raise ApplicationError("Steam game build or library changed; existing map profiles need revalidation")

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
                raise ApplicationError("Remove custom content from the fresh game before initial setup")

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
            shutil.copytree(self.installation.profile_root, stage, dirs_exist_ok=True, copy_function=shutil.copy2)
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

    def import_archive(self, source: Path, *, original_filename: Optional[str] = None,
                       remote: Optional[RemoteMap] = None) -> MapRecord:
        with self._locked():
            self._stopped()
            self.profiles.recover()
            source = Path(source).expanduser()
            if original_filename is not None:
                if (not original_filename.lower().endswith(".7z")
                        or len(original_filename) > 255
                        or any(char in original_filename for char in ("/", "\\", ":"))
                        or any(ord(char) < 32 or ord(char) == 127 for char in original_filename)):
                    raise ApplicationError("Original archive name is unsafe")
            display_name = original_filename[:-3] if original_filename is not None else source.stem
            inspection = inspect_package(source)
            if remote is not None:
                if original_filename != remote.name or not self._matches_remote(source.resolve(strict=True), remote):
                    raise ApplicationError("Archive does not match its collection source")
            existing = next((item for item in self.catalogue()
                             if item.archive_sha256 == inspection.source_sha256
                             and item.game_executable_sha256 == self.installation.executable_sha256), None)
            if existing is not None:
                return self._record_source(existing, remote) if remote else existing
            archive = self._preserve_archive(source, inspection.source_sha256)
            self.imports.mkdir(parents=True, exist_ok=True)
            imported = self.imports / uuid.uuid4().hex
            extract_package(archive, imported, inspection.source_sha256)
            self.prepared.mkdir(parents=True, exist_ok=True)
            prepared_name = uuid.uuid4().hex
            prepared_path = self.prepared / prepared_name
            variant = prepare_original_variant(
                self.baseline, imported, prepared_path,
                inspection.source_sha256, self.installation.executable_sha256,
            )
            self._prepare_icon(imported, variant.variant_id)
            profile_id = self.profiles.register_variant(variant.variant_id, prepared_path)
            record = MapRecord(display_name, inspection.source_sha256, variant.variant_id,
                               profile_id, inspection.scenarios, prepared_name,
                               self.installation.executable_sha256, imported.name,
                               original_filename or source.name,
                               remote.source_url if remote else "",
                               "Internet Archive · verified archive" if remote else "Local archive",
                               remote.archive_modified if remote else "",
                               datetime.now(timezone.utc).isoformat())
            records = list(self.catalogue())
            if any(item.variant_id == record.variant_id for item in records):
                raise ApplicationError("Prepared variant identity already exists in the catalogue")
            records.append(record)
            _atomic_json(self.catalogue_file, dict(schema=1, maps=[item.as_json() for item in records]))
            return record

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
        updated = replace(record, archive_filename=remote.name, source_url=remote.source_url,
                          source_label="Internet Archive · verified archive",
                          archive_modified=remote.archive_modified)
        records = [updated if item.variant_id == record.variant_id else item for item in self.catalogue()]
        _atomic_json(self.catalogue_file, dict(schema=1, maps=[item.as_json() for item in records]))
        return updated

    def match_collection_sources(self, remotes: tuple[RemoteMap, ...]) -> int:
        """Attach provenance to old imports only after matching preserved bytes."""
        candidates: dict[int, list[RemoteMap]] = {}
        for remote in remotes:
            candidates.setdefault(remote.size, []).append(remote)
        matched = 0
        with self._locked():
            for record in self.catalogue():
                if record.source_url:
                    continue
                archive = self.originals / (record.archive_sha256 + ".7z")
                _assert_no_symlink_ancestor(archive)
                if not archive.is_file():
                    continue
                for remote in candidates.get(archive.stat().st_size, []):
                    if self._matches_remote(archive, remote):
                        self._record_source(record, remote)
                        matched += 1
                        break
        return matched

    def map_metadata(self, record: MapRecord) -> MapMetadata:
        # Catalogue paths are metadata, never permission to escape the library.
        directory = record.imported_directory
        if not directory or Path(directory).name != directory or directory in (".", ".."):
            return MapMetadata()
        return read_map_metadata(self.imports / directory / "mapInfo.txt")

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
                ["/usr/bin/sips", "-s", "format", "png", "-Z", "360", str(source), "--out", str(temporary)],
                capture_output=True, timeout=15, check=False,
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
        if len(record.variant_id) != 64 or any(char not in "0123456789abcdef" for char in record.variant_id):
            return None
        path = self.icons / (record.variant_id + ".png")
        if any(part.is_symlink() for part in (path, *path.parents)):
            return None
        if path.is_file():
            return path
        return None

    def verification_for(self, record: MapRecord) -> Optional[GameplayVerification]:
        """Return gameplay evidence only for the current build and exact live assets."""
        candidates = [item for item in self.verification_store.records()
                      if (item.archive_sha256, item.game_executable_sha256, item.variant_id)
                      == (record.archive_sha256, record.game_executable_sha256, record.variant_id)]
        if not candidates:
            return None
        with self._locked():
            self._verify_installation_binding()
            if self.profiles.journal.exists():
                return None
            state = self.profiles._state()
            root = (self.profiles.live if state["active"] == record.profile_id
                    else self.profiles._profile_path(record.profile_id))
            marker = self.profiles._marker(root, record.profile_id)
            return self.verification_store.latest(
                record.archive_sha256, self.installation.executable_sha256,
                record.variant_id, marker["assets_sha256"])

    def record_gameplay(self, record: MapRecord, observed_at: str, reporter: str,
                        checks: dict[str, bool], scope: str, issue: str = "") -> GameplayVerification:
        """Record an externally observed stopped-game result for the active map."""
        with self._locked():
            self._stopped()
            self._authorize_profile(record.profile_id)
            if record not in self.catalogue() or self.profiles.journal.exists():
                raise ApplicationError("Map is absent or a profile switch needs recovery")
            if self.profiles._state()["active"] != record.profile_id:
                raise ApplicationError("Gameplay result must be recorded for the active map")
            marker = self.profiles._marker(self.profiles.live, record.profile_id)
            verification = GameplayVerification(
                record.archive_sha256, self.installation.executable_sha256,
                record.variant_id, marker["assets_sha256"], observed_at,
                reporter, checks, scope, issue)
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
            raise ApplicationError("This map was prepared for another game build; preserve its saves and revalidate")

    def activate(self, profile_id: str) -> str:
        with self._locked():
            self._stopped()
            self._authorize_profile(profile_id)
            return self.profiles.switch(profile_id)

    def play(self, profile_id: str, launch: Optional[Callable[[], None]] = None) -> str:
        with self._locked():
            self._stopped()
            self._authorize_profile(profile_id)
            if launch is None:
                bundle = self.installation.executable.parents[2]
                launch = lambda: subprocess.Popen(["/usr/bin/open", "-a", str(bundle)])
            return self.profiles.play(profile_id, launch)
