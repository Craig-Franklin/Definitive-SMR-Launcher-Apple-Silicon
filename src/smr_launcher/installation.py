"""Install this signed launcher into the user's Applications without replacement."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from . import APP_VERSION
from .activation import ActivationError, _assert_no_symlink_ancestor
from .extraction import _publish_exclusive
from .packages import PackageError
from .updates import (BUNDLE_NAME, UpdateError, current_app_bundle,
                      trusted_team_from_bundle, verify_publisher)


class InstallationError(RuntimeError):
    """Installation stopped without replacing an existing app."""


def installation_needed(bundle: Path, home: Path = Path.home()) -> bool:
    """Applications installations already have a permanent launch location."""
    bundle = Path(bundle).expanduser().absolute()
    home = Path(home).expanduser().absolute()
    _assert_no_symlink_ancestor(bundle)
    _assert_no_symlink_ancestor(home)
    return bundle.parent not in (Path("/Applications"), home / "Applications")


def _manifest(bundle: Path) -> tuple[dict, set[tuple[int, int]]]:
    """Hash regular files, preserve internal links, and reject external payloads."""
    result = {}
    inodes = set()
    root = bundle.resolve(strict=True)

    def walk_error(error: OSError) -> None:
        raise error

    for directory, directories, files in os.walk(bundle, followlinks=False, onerror=walk_error):
        for name in directories + files:
            path = Path(directory) / name
            relative = path.relative_to(bundle).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise InstallationError("App contains an invalid link") from exc
                if Path(target).is_absolute() or not resolved.is_relative_to(root):
                    raise InstallationError("App contains a link outside its bundle")
                result[relative] = ("link", target)
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = ("directory",)
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                result[relative] = ("file", digest.hexdigest())
                inodes.add((info.st_dev, info.st_ino))
            else:
                raise InstallationError("App contains an unsupported filesystem object")
    return result, inodes


def install_to_user_applications(source: Path, home: Path = Path.home()) -> Path:
    """Copy this exact running app independently; never replace or launch an app."""
    stage = None
    try:
        source = Path(source).expanduser().absolute()
        home = Path(home).expanduser().absolute()
        _assert_no_symlink_ancestor(source)
        _assert_no_symlink_ancestor(home)
        if source != current_app_bundle().absolute() or source.name != BUNDLE_NAME:
            raise InstallationError("Installation requires the exact running launcher app")
        if not home.is_dir():
            raise InstallationError("The home folder is unavailable")
        destination = home / "Applications" / BUNDLE_NAME
        _assert_no_symlink_ancestor(destination)
        if destination.exists():
            raise InstallationError("A launcher already exists in your Applications folder. Open it and use App Updates.")
        team = trusted_team_from_bundle()
        verify_publisher(source, team, APP_VERSION)
        original, source_inodes = _manifest(source)
        destination.parent.mkdir(mode=0o755, exist_ok=True)
        _assert_no_symlink_ancestor(destination)
        stage = Path(tempfile.mkdtemp(prefix=".smr-install-", dir=destination.parent))
        candidate = stage / BUNDLE_NAME
        copied = subprocess.run(["/usr/bin/ditto", str(source), str(candidate)],
                                capture_output=True, text=True, timeout=180)
        if copied.returncode:
            raise InstallationError("Could not copy the launcher to your Applications folder")
        verify_publisher(candidate, team, APP_VERSION)
        manifest, copied_inodes = _manifest(candidate)
        if (manifest != original or source_inodes & copied_inodes
                or _manifest(source)[0] != original):
            raise InstallationError("The copied launcher differs from its source")
        _assert_no_symlink_ancestor(destination)
        _publish_exclusive(candidate, destination)
        return destination
    except InstallationError:
        raise
    except (ActivationError, UpdateError, PackageError, OSError, subprocess.SubprocessError) as exc:
        raise InstallationError(str(exc)) from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage)
