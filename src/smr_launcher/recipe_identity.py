"""File metadata identities for newly compiled recipe editions only.

Directory mtimes, absolute paths and inode numbers are deliberately excluded
from preparation identity. The existing, stricter runtime resource fingerprint
is unchanged. These hashes do not establish effective VFS resource selection.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from .activation import _assert_no_symlink_ancestor
from .resource_identity import fingerprint_resources, ResourceFingerprintError
from .rules import PRESERVE_RESOURCE_MTIMES, RuleError


FILE_CONTEXT_ALGORITHM = "sha256-resource-files-content-mtime-ns-v1"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii")).hexdigest()


def file_context(roots: dict, *, required_roles=(), hash_cache=None) -> dict:
    """Safely read resource files and return a location-independent binding."""
    snapshot = fingerprint_resources(roots, required_roles=required_roles, hash_cache=hash_cache)
    roles = {root["role"]: [entry for entry in root["entries"] if entry["kind"] == "file"]
             for root in snapshot["roots"]}
    return _context(roles)


def _context(roles: dict) -> dict:
    payload = {"schema": 1, "algorithm": FILE_CONTEXT_ALGORITHM,
               "roots": [{"role": role, "files": sorted(roles[role], key=lambda entry: entry["path"])}
                         for role in sorted(roles)]}
    return {**payload, "identity": _digest(payload)}


def expected_patched_context(source: dict, rules: tuple) -> dict:
    """Derive exact output file facts while retaining every original mtime.

    Only accepts a freshly measured map context from this module. File hashes
    and lengths change according to the exact byte patches; no file may appear,
    disappear or change metadata incidentally during copying/preparation.
    """
    roles = {root["role"]: {file["path"]: dict(file) for file in root["files"]}
             for root in source["roots"]}
    seen = set()
    for rule in rules:
        for patch in rule.patches:
            install_root, relative = patch.path.split("/", 1)
            role = {"CustomAssets": "custom_assets", "UserMaps": "user_maps"}[install_root]
            if (role, relative) in seen:
                raise RuleError("Recipe context patches overlap")
            seen.add((role, relative))
            entry = roles.get(role, {}).get(relative)
            if entry is None or entry["sha256"] != patch.before_sha256:
                raise RuleError("Recipe context patch preimage differs")
            entry["sha256"] = patch.after_sha256
            entry["size"] += len(patch.new) - len(patch.old)
            if entry["size"] < 0:
                raise RuleError("Recipe context patch length is invalid")
        for addition in getattr(rule, "additions", ()):
            install_root, relative = addition.path.split("/", 1)
            role = {"CustomAssets": "custom_assets", "UserMaps": "user_maps"}[install_root]
            if (role, relative) in seen:
                raise RuleError("Recipe context additions overlap")
            seen.add((role, relative))
            files = roles.setdefault(role, {})
            if relative in files:
                if files[relative]["sha256"] != addition.after_sha256:
                    raise RuleError("Recipe context addition target exists")
                continue
            files[relative] = {"path": relative, "kind": "file", "size": len(addition.data),
                               "mtime_ns": addition.mtime_ns, "sha256": addition.after_sha256}
    return _context({role: [files[path] for path in sorted(files)] for role, files in roles.items()})


def map_file_context(root: Path, *, hash_cache=None) -> dict:
    """Bind map assets only; an absent CustomAssets is a canonical empty role.

    UserMaps must exist. Saves/settings are never part of recipe resource IDs.
    The input root itself is checked around enumeration so a newly created or
    replaced asset root cannot silently escape this snapshot.
    """
    root = Path(root)
    _assert_no_symlink_ancestor(root)
    before = root.stat()
    if not root.is_dir():
        raise ResourceFingerprintError("Map resource parent must be a directory")
    roles = {"user_maps": root / "UserMaps"}
    custom = root / "CustomAssets"
    if custom.exists() or custom.is_symlink():
        roles["custom_assets"] = custom
    snapshot = fingerprint_resources(roles, required_roles=("user_maps",), hash_cache=hash_cache)
    after = root.stat()
    identity = lambda st: (st.st_dev, st.st_ino, st.st_mode, st.st_mtime_ns, st.st_ctime_ns)
    if identity(before) != identity(after):
        raise ResourceFingerprintError("Map resource parent changed during fingerprint")
    _assert_no_symlink_ancestor(root)
    entries = {role: [] for role in ("custom_assets", "user_maps")}
    for item in snapshot["roots"]:
        entries[item["role"]] = [entry for entry in item["entries"] if entry["kind"] == "file"]
    return _context(entries)


@dataclass(frozen=True)
class RecipePreparationBinding:
    """Reviewed compiler/context identities supplied by the recipe service.

    Construction alone does not validate stock/VFS assumptions. The outer
    service must resolve those before preparation and recheck before Play.
    """

    recipe_id: str
    recipe_sha256: str
    compiler_sha256: str
    source_files_sha256: str
    stock_files_sha256: str

    def __post_init__(self):
        if not isinstance(self.recipe_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,119}", self.recipe_id):
            raise RuleError("Recipe binding needs a stable recipe ID")
        for value in (self.recipe_sha256, self.compiler_sha256,
                      self.source_files_sha256, self.stock_files_sha256):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise RuleError("Recipe context binding needs exact SHA-256 identities")

    def as_json(self) -> dict:
        return dict(schema=1, recipe_id=self.recipe_id, recipe_sha256=self.recipe_sha256,
                    compiler_sha256=self.compiler_sha256, source_files_sha256=self.source_files_sha256,
                    stock_files_sha256=self.stock_files_sha256, file_context_algorithm=FILE_CONTEXT_ALGORITHM,
                    metadata_policy=PRESERVE_RESOURCE_MTIMES)
