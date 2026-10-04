"""Exact, local binding to the observed Steam Mac Railroads installation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import ctypes
import ctypes.util
import hashlib
import plistlib
import re
import sys


class BindingError(RuntimeError):
    """The current game installation differs from the enrolled installation."""


def _sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            result.update(chunk)
    return result.hexdigest()


def _acf_value(document: str, key: str) -> str:
    match = re.search(r'"' + re.escape(key) + r'"\s*"([^"\r\n]+)"', document)
    if not match:
        raise BindingError("Steam manifest lacks " + key)
    return match.group(1)


@dataclass(frozen=True)
class SteamInstallation:
    appid: str
    steam_buildid: str
    bundle_id: str
    bundle_version: str
    executable_sha256: str
    executable: Path
    support_root: Path
    profile_root: Path
    beta_keys: tuple[str, ...]
    steamapps_root: Path
    home: Path

    @classmethod
    def discover(cls, home: Path, steamapps_root: Optional[Path] = None,
                 allow_running_state: bool = False) -> "SteamInstallation":
        if sys.platform != "darwin":
            raise BindingError("Railroads Steam binding requires macOS")
        home = Path(home).expanduser()
        steam = (home / "Library/Application Support/Steam/steamapps"
                 if steamapps_root is None else Path(steamapps_root).expanduser())
        steam = steam.absolute()
        if any(part.is_symlink() for part in (steam, *steam.parents)):
            raise BindingError("Steam library path contains a link")
        manifest = steam / "appmanifest_7600.acf"
        game = steam / "common/Sid Meier's Railroads/Sid Meiers Railroads.app"
        executable = game / "Contents/MacOS/Sid Meiers Railroads"
        support = home / "Library/Application Support/Feral Interactive/Sid Meier's Railroads!"
        profile = support / "VFS/User/AppData/Roaming/My Games/WinDeveloper"
        if any(path.is_symlink() for path in (manifest, game, executable, support, profile)):
            raise BindingError("Linked Steam game or profile path needs inspection")
        if not manifest.is_file() or not executable.is_file() or not profile.is_dir():
            raise BindingError("Installed Steam game or observed support profile is missing")
        document = manifest.read_text(encoding="utf-8")
        appid = _acf_value(document, "appid")
        state = _acf_value(document, "StateFlags")
        buildid = _acf_value(document, "buildid")
        beta_keys = tuple(re.findall(r'"BetaKey"\s*"([^"\r\n]+)"', document))
        try:
            observed_state = int(state)
        except ValueError as exc:
            raise BindingError("Steam app 7600 has invalid install state") from exc
        permitted_states = (4, 68) if allow_running_state else (4,)
        if appid != "7600" or observed_state not in permitted_states or any(key != "public" for key in beta_keys):
            raise BindingError("Steam app 7600 is not ready on the public branch")
        info = plistlib.loads((game / "Contents/Info.plist").read_bytes())
        bundle_id = info.get("CFBundleIdentifier")
        version = info.get("CFBundleVersion")
        if bundle_id != "com.feralinteractive.railroads" or not isinstance(version, str):
            raise BindingError("Installed Mac bundle identity is unexpected")
        return cls(appid, buildid, bundle_id, version, _sha256(executable), executable,
                   support, profile, beta_keys, steam, home)

    def check_current(self) -> None:
        current = self.discover(self.home, self.steamapps_root)
        if current != self:
            raise BindingError("Steam game build or support binding changed; reverify before switching")

    def game_running(self) -> Optional[bool]:
        """Return False only after successful macOS process enumeration."""
        try:
            current = self.discover(self.home, self.steamapps_root, allow_running_state=True)
            if current != self:
                return None
            location = ctypes.util.find_library("proc")
            if not location:
                return None
            lib = ctypes.CDLL(location)
            lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.proc_listallpids.restype = ctypes.c_int
            lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            lib.proc_pidpath.restype = ctypes.c_int
            lib.proc_name.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            lib.proc_name.restype = ctypes.c_int
            estimate = lib.proc_listallpids(None, 0)
            if estimate <= 0:
                return None
            capacity = max(1024, estimate * 2)
            pids = (ctypes.c_int * capacity)()
            count = lib.proc_listallpids(pids, ctypes.sizeof(pids))
            if count <= 0 or count >= capacity:
                return None
            expected = str(self.executable.resolve())
            uncertain_game_name = False
            for pid in pids[:count]:
                if pid <= 0:
                    continue
                path_buffer = ctypes.create_string_buffer(4096)
                length = lib.proc_pidpath(pid, path_buffer, len(path_buffer))
                name_buffer = ctypes.create_string_buffer(1024)
                name_length = lib.proc_name(pid, name_buffer, len(name_buffer))
                name = name_buffer.value.decode("utf-8", "replace") if name_length > 0 else ""
                if length > 0:
                    actual = path_buffer.value.decode("utf-8", "replace")
                    if actual == expected or name.startswith("Sid Meiers Rail"):
                        return True
                elif name.startswith("Sid Meiers Rail"):
                    uncertain_game_name = True
            return None if uncertain_game_name else False
        except (OSError, ValueError, BindingError):
            return None
