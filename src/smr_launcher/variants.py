"""Deterministic original-map profile preparation from independent inputs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple
import hashlib
import json
import os
import shutil
import tempfile

from .activation import ActivationError, _assert_regular_tree, _make_writable_tree
from .extraction import _freeze_tree, _fsync_directory, _private_parent, _publish_exclusive, _unfreeze_directories
from .packages import PackageError, _digest
from .rules import CompatibilityRule, RuleError, apply_rules
from .recipe_identity import RecipePreparationBinding, map_file_context, expected_patched_context
from .rules import PRESERVE_RESOURCE_MTIMES


PREPARATION_SCHEMA = 1


@dataclass(frozen=True)
class PreparedVariant:
    variant_id: str
    archive_sha256: str
    game_executable_sha256: str
    assets_sha256: str
    files: Dict[str, str]
    directories: Tuple[str, ...]
    rules: Tuple[str, ...] = ()
    recipe_binding: dict | None = None


def _tree_manifest(root: Path) -> Tuple[Dict[str, str], Tuple[str, ...]]:
    files: Dict[str, str] = {}
    directories = []
    for base, child_dirs, names in os.walk(root, followlinks=False, onerror=lambda error: (_ for _ in ()).throw(error)):
        here = Path(base)
        for name in child_dirs:
            directories.append((here / name).relative_to(root).as_posix())
        for name in names:
            file = here / name
            files[file.relative_to(root).as_posix()] = _digest(file)
    return dict(sorted(files.items())), tuple(sorted(directories))


def assets_manifest_hash(root: Path) -> str:
    assets_files = {}
    assets_directories = []
    for name in ("CustomAssets", "UserMaps"):
        files, directories = _tree_manifest(root / name)
        assets_files.update({name + "/" + path: digest for path, digest in files.items()})
        assets_directories.extend(name + "/" + path for path in directories)
    assets_record = json.dumps({"files": dict(sorted(assets_files.items())), "directories": sorted(assets_directories)}, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(assets_record).hexdigest()


def _prepare_variant(
    clean_profile: Path,
    imported_package: Path,
    destination: Path,
    archive_sha256: str,
    game_executable_sha256: str,
    rules: Tuple[CompatibilityRule, ...],
    edition_options: dict | None = None,
    recipe_binding: RecipePreparationBinding | None = None,
) -> PreparedVariant:
    """Build a frozen map generation without copying the stock profile's saves."""
    if any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value) for value in (archive_sha256, game_executable_sha256)):
        raise ValueError("Archive and game identities must be SHA-256 digests")
    clean_profile = Path(clean_profile).expanduser()
    imported_package = Path(imported_package).expanduser()
    destination = Path(destination).expanduser()
    if clean_profile.is_symlink() or imported_package.is_symlink():
        raise PackageError("Prepared input is a link")
    _assert_regular_tree(clean_profile)
    _assert_regular_tree(imported_package)
    source_context = None
    if recipe_binding is not None:
        if not isinstance(recipe_binding, RecipePreparationBinding) or not rules or edition_options:
            raise RuleError("Compiled recipe preparation needs an exclusive validated binding and rules")
        source_context = map_file_context(imported_package)
        if source_context["identity"] != recipe_binding.source_files_sha256:
            raise RuleError("Recipe source resource context differs")
    for name in ("CustomAssets", "UserMaps", "Saves"):
        if not (clean_profile / name).is_dir():
            raise PackageError("Clean profile is missing " + name)
    if not (imported_package / "UserMaps").is_dir():
        raise PackageError("Imported package has no UserMaps")
    if any((imported_package / name).is_file() for name in ("CustomAssets", "UserMaps")):
        raise PackageError("Install roots must be directories")
    source_roots = (clean_profile.resolve(), imported_package.resolve())
    destination = destination.parent.resolve() / destination.name
    if any(destination == source or destination in source.parents or source in destination.parents for source in source_roots):
        raise PackageError("Prepared output overlaps a source")
    parent = _private_parent(destination)
    stage = Path(tempfile.mkdtemp(prefix=".smr-variant-", dir=parent))
    try:
        for child in clean_profile.iterdir():
            if child.name in ("CustomAssets", "UserMaps", "Saves"):
                continue
            output = stage / child.name
            if child.is_dir():
                shutil.copytree(child, output, copy_function=shutil.copy2)
            else:
                shutil.copy2(child, output)
        for name in ("CustomAssets", "UserMaps"):
            source = imported_package / name
            if source.is_dir():
                shutil.copytree(source, stage / name, copy_function=shutil.copy2)
            else:
                (stage / name).mkdir()
        (stage / "Saves").mkdir()
        _assert_regular_tree(stage)
        if rules or edition_options:
            _make_writable_tree(stage)
        applied = apply_rules(stage, archive_sha256, game_executable_sha256, rules,
                              metadata_policy=PRESERVE_RESOURCE_MTIMES if recipe_binding else None) if rules else None
        edition = None
        if edition_options:
            from .editions import apply_options
            edition = apply_options(stage, edition_options)
        assets_sha256 = assets_manifest_hash(stage)
        identity_fields = {"schema": PREPARATION_SCHEMA, "recipe": "original",
                           "archive": archive_sha256, "game": game_executable_sha256,
                           "assets": assets_sha256}
        if applied is not None:
            identity_fields.update(schema=2, recipe="compatibility",
                                   rules=applied.fingerprint)
        if edition is not None:
            identity_fields.update(schema=3, recipe="experimental-options", options=edition)
        recipe_receipt = None
        if recipe_binding is not None:
            if map_file_context(imported_package)["identity"] != recipe_binding.source_files_sha256:
                raise RuleError("Recipe source resource context changed during preparation")
            output_context = map_file_context(stage)["identity"]
            if output_context != expected_patched_context(source_context, rules)["identity"]:
                raise RuleError("Prepared recipe files or timestamps differ from declared transformation")
            recipe_receipt = {**recipe_binding.as_json(), "output_files_sha256": output_context}
            identity_fields.update(schema=4, recipe="compiled-compatibility", context=recipe_receipt)
        identity = json.dumps(identity_fields, sort_keys=True, separators=(",", ":")).encode()
        variant_id = hashlib.sha256(identity).hexdigest()
        expected_files, expected_directories = _tree_manifest(stage)
        _freeze_tree(stage)
        readback_files, readback_directories = _tree_manifest(stage)
        if (readback_files, readback_directories) != (expected_files, expected_directories):
            raise PackageError("Prepared variant failed readback")
        if recipe_receipt and map_file_context(stage)["identity"] != recipe_receipt["output_files_sha256"]:
            raise PackageError("Prepared recipe resource metadata failed readback")
        _publish_exclusive(stage, destination)
        return PreparedVariant(variant_id, archive_sha256, game_executable_sha256, assets_sha256,
                               expected_files, expected_directories,
                               applied.rule_keys if applied else (), recipe_receipt)
    finally:
        if stage.exists():
            _unfreeze_directories(stage)
            shutil.rmtree(stage)


def prepare_original_variant(
    clean_profile: Path, imported_package: Path, destination: Path,
    archive_sha256: str, game_executable_sha256: str,
) -> PreparedVariant:
    """Prepare the exact imported original with no compatibility rule."""
    return _prepare_variant(clean_profile, imported_package, destination,
                            archive_sha256, game_executable_sha256, ())


def prepare_compatibility_variant(
    clean_profile: Path, imported_package: Path, destination: Path,
    archive_sha256: str, game_executable_sha256: str,
    rules: Tuple[CompatibilityRule, ...],
) -> PreparedVariant:
    """Prepare a separately identified edition with explicit, exact rules."""
    if not rules:
        raise RuleError("A compatibility edition needs at least one explicit rule")
    return _prepare_variant(clean_profile, imported_package, destination,
                            archive_sha256, game_executable_sha256, tuple(rules))


def prepare_recipe_variant(
    clean_profile: Path, imported_package: Path, destination: Path,
    archive_sha256: str, game_executable_sha256: str,
    rules: Tuple[CompatibilityRule, ...], binding: RecipePreparationBinding,
) -> PreparedVariant:
    """Prepare a separately identified, timestamp-preserving compiled edition.

    This low-level API assumes the caller has established effective base context.
    It verifies source metadata and records output metadata; it neither registers
    saves nor awards verification nor enables a recipe in the product.
    """
    if not isinstance(binding, RecipePreparationBinding):
        raise RuleError("Compiled recipe needs a preparation binding")
    return _prepare_variant(clean_profile, imported_package, destination,
                            archive_sha256, game_executable_sha256, tuple(rules),
                            recipe_binding=binding)
