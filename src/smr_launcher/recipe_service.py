"""Bundled, exact-input identity recipes compiled from local user assets.

The first supported context has unique loose industry/depot readers and no FPK
providers. Unknown resolution contexts are rejected. This is an experimental
preparation service; successful compilation grants no gameplay verification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from . import bank_model
from .bank_applicability import assess_bank_applicability, compile_bank_patch
from .activation import _assert_no_symlink_ancestor, _assert_regular_tree
from .compatibility_recipes import COMPILER_VERSION, IdentityEdit, _parse, compile_identity_patch
from .industry_registry import extract_supported_industry_registry
from .recipe_identity import FILE_CONTEXT_ALGORITHM, RecipePreparationBinding, map_file_context
from .rules import (
    AssetAddition,
    MAX_PATCHED_FILE_BYTES,
    PRESERVE_RESOURCE_MTIMES,
    CompatibilityRule,
    RuleError,
)
from . import removal
from .variants import assets_manifest_hash
from .packages import MAX_ARCHIVE_BYTES


@dataclass(frozen=True)
class _IdentityRecipe:
    recipe_id: str
    archive_sha256: str
    game_sha256: str
    assets_sha256: str
    scenario_path: str
    scenario_sha256: str
    industry_path: str
    industry_before: str
    industry_after: str
    stock_industries_sha256: str
    stock_depots_sha256: str
    aliases: tuple[tuple[str, int, str], ...]
    additions: tuple[tuple[str, str, str], ...] = ()
    scenario_after: str = ""
    ownership_edits: tuple[IdentityEdit, ...] = ()
    legacy_bank_path: str = ""


_HILL_COWHIDE_ADDITIONS = (
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/cowhide_car_v1.kfm",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/cowhide_car_v1.kfm",
        "4f23b487e55053d5e22d976269a54675a58d5feb7b8782d5327c1a5b18fd3405",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/cowhide_car_v2.kfm",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/cowhide_car_v2.kfm",
        "cb846d1afcfacf810a2dbe02e5c5ebefa45d55dc55383bd0034e3b17abfddc45",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/cowhide_car_v1.nif",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/cowhide_car_v1.nif",
        "dc59c14efe5941c1d29c86e5e362e091f84a0415180c2fce609a9fd668a1421f",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/cowhide_car_v2.nif",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/cowhide_car_v2.nif",
        "375ea027b6db0faf33989bbb6cb056141274805d5e1e356a8369de4ca2d336fd",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/cowhide_car_dummies_v1.nif",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/cowhide_car_dummies_v1.nif",
        "5f0275c87897b8591d27c3ef0ebe70c7dce95e973955a6e65cfce84b5b0cbdc8",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/cowhide_car_dummies_v2.nif",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/cowhide_car_dummies_v2.nif",
        "52cb5a6ef7bcee80db00d14b1965a745607254731830ac76bdf33d598fc89281",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/cowhide_car_v1_diff.dds",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/cowhide_car_v1_diff.dds",
        "c0ad60472960f700ec09b69f4f5b9621d6b0caeeccffccf46422054d4de3941b",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/cowhide_car_v2_diff.dds",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/cowhide_car_v2_diff.dds",
        "6fc235d4e24b898ddeb76bf005b374c17e37d0a22d6a65ff79e0b44469781982",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/icon_cowhide_16.tga",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/icon_cowhide_16.tga",
        "9a48c7c78e0c2b5f6eef53ae52f25e8a36e0a8501ce872675c31bbb1a17b78fb",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/icon_cowhide_32.tga",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/icon_cowhide_32.tga",
        "970ac20db02f5139b0806d201dc0b7e5c6b1760745fffc608b4da2fedb1c4a7e",
    ),
    (
        "UserMaps/Hill_Valley_Steaks_SAM/warehouse/icon_cowhide_64.tga",
        "UserMaps/Side_To_Side_2_SAM/Warehouse/icon_cowhide_64.tga",
        "0d41a243a904ab6c64bedf5e9c010c1d3da5441e3f487792eb848f78ce826a76",
    ),
)


_RECIPES = {
    # Experimental preparation; player availability is separate from gameplay acceptance.
    # This reproduces the historical three-file experiment, not a diagnosis
    # selected automatically from a crash fingerprint or legacy model header.
    "az-canyon-v02-bank-and-industry-identifiers-v1": _IdentityRecipe(
        recipe_id="az-canyon-v02-bank-and-industry-identifiers-v1",
        archive_sha256="399570b00bb4c3590dacd30b45322b65f72029237ee49ed25be0d4f5c637e5cf",
        game_sha256="981ca7180759d8578babf54cd9a4f76b93db503b33a7f1938c6b1427b95680f0",
        assets_sha256="444f390791371683be8c91ef373b21635dc67bae5eff0e78ecb268c69cfd900f",
        scenario_path="UserMaps/AZCanyonV2/RRT_Scenario_User_AZCanyonV2.xml",
        scenario_sha256="fcd995f3a77cab0f68a627dcd8ae8f2a68daa8a868ce323f821769d1044514d0",
        industry_path="UserMaps/AZCanyonV2/RRT_Industries_AZCanyonV2.xml",
        industry_before="24489221ca8613610acfbbaed1911195ed98a6f0dda3c7fb91df36eabb16564c",
        industry_after="6313b5f778055123b4c5d6c0d5cb47143987eda090f477fb748f609126a2203e",
        stock_industries_sha256="db8210cde6325ad9ffe0b40b53c1d01393e3ef3b9c7a9c5e82aa0599ef5342b6",
        stock_depots_sha256="35c0bd07ed3f38e2ab1bad8ea5adc85499d0d737c6530e6e79414110686f7bd5",
        aliases=(
            ("Steamboat Company", 35, "Wooden Horse Factory"),
            ("Hotel", 33, "Doll Factory"),
            ("Bank", 34, "Bakery"),
            ("Air Force Base", 32, "Toy Soldier Factory"),
        ),
        scenario_after="928417ddb46a98fd67c75c5c44aa5f160a81a49547150bbaf66b1e2fb36de6fa",
        ownership_edits=tuple(
            IdentityEdit(
                "industry-goal",
                old,
                target,
                count,
                (
                    "RRTScenario",
                    "Eras",
                    "Era",
                    "VictoryConditions",
                    "Condition",
                    "OwnList",
                    "OwnListItem",
                    "szObjectName",
                ),
            )
            for old, target, count in (
                ("Bank", "Bakery", 1),
                ("Hotel", "Doll Factory", 2),
                ("Air Force Base", "Toy Soldier Factory", 1),
            )
        ),
        legacy_bank_path="UserMaps/AZCanyonV2/warehouse/bank.nif",
    ),
    "great-lakes-industry-identities-v2": _IdentityRecipe(
        "great-lakes-industry-identities-v2",
        "8dafd43f19f2530ddd4ae60cd025cbbdd05d66b990b68ce78ccc897dae52ac10",
        "981ca7180759d8578babf54cd9a4f76b93db503b33a7f1938c6b1427b95680f0",
        "8c94fdfe7eb06ce4bba039253aea6a49854d871059f036d75ce4de3f9c574d48",
        "UserMaps/GLRT_SAM/RRT_Scenario_User_GLRT.xml",
        "b67643faa27a6e0ad89ed1b3c5e6a8d1b848c651680943a7419b8e917fffb377",
        "UserMaps/GLRT_SAM/RRT_Industries_GLRT.xml",
        "2421fc9870f990ea5f03f88e954940729821effe31018c12f4d4c27ead1950e2",
        "13236029be79143a387b328f17b79644e3f09854011eced197c901cf56cb50a8",
        "db8210cde6325ad9ffe0b40b53c1d01393e3ef3b9c7a9c5e82aa0599ef5342b6",
        "35c0bd07ed3f38e2ab1bad8ea5adc85499d0d737c6530e6e79414110686f7bd5",
        (("Military Barracks", 33, "Doll Factory"), ("Port", 34, "Bakery")),
    ),
    # Diagnostic recipe: the original alias-only trial crashed during train-car
    # precache; the additive edition now has an independent bounded fresh-load
    # pass. Gameplay remains unverified.
    "hill-valley-steaks-tannery-city-v1": _IdentityRecipe(
        "hill-valley-steaks-tannery-city-v1",
        "2e2d21399a6a887f9ead7755c0f504c41367e6838a9af45ca00f0f86baa70597",
        "981ca7180759d8578babf54cd9a4f76b93db503b33a7f1938c6b1427b95680f0",
        "08820b0062c914d4b8e8bfa8e84d6f4d25dd37827595e05dd5ae9b2105cc8b98",
        "UserMaps/Hill_Valley_Steaks_SAM/RRT_Scenario_MP35_Hill Valley Steaks.xml",
        "4557b9d9f006c89e27ac7d4a3edec62915f549afa7a1f426d62fcb199c8f6ad1",
        "UserMaps/Hill_Valley_Steaks_SAM/RRT_Industries_Hill_Valley_Steaks.xml",
        "a275101e31acd089c6cb6335541835e95516171d14bee6168c6d84ed77ed51f8",
        "cde1d4ecf28a0fb93e8e28dc5a7d23499592cbb7b83e0ab6f330abf8bdfe3c28",
        "db8210cde6325ad9ffe0b40b53c1d01393e3ef3b9c7a9c5e82aa0599ef5342b6",
        "35c0bd07ed3f38e2ab1bad8ea5adc85499d0d737c6530e6e79414110686f7bd5",
        (("Tannery", 33, "Doll Factory"),),
        _HILL_COWHIDE_ADDITIONS,
    ),
}

# Algorithm versions are explicit release inputs, independent of source-file
# availability in a frozen app. Bump this contract when compilation/preparation
# semantics change. This digest identifies the contract, not binary code bytes.
RECIPE_SERVICE_VERSION = (
    "reviewed-stock-absent-city-v3-ownership-exact-bank-v1-existing-target-mtime-v1"
)
_HILL_COWHIDE_DONOR_ARCHIVE = "596c27fd554266b22424481670cd20c6c428a602f69570df3949873cd8c179df"


@dataclass(frozen=True)
class RecipeChoice:
    recipe_id: str
    title: str
    description: str


_CHOICES = {
    "az-canyon-v02-bank-and-industry-identifiers-v1": RecipeChoice(
        "az-canyon-v02-bank-and-industry-identifiers-v1",
        "Arizona — experimental repair",
        "Experimental repair for Arizona Gold V2.2. Converts the legacy Bank model "
        "and updates four industry identifiers with their ownership goals. "
        "Steamboat Company uses Wooden Horse Factory; Hotel uses Doll Factory; "
        "Bank uses Bakery; Air Force Base uses Toy Soldier Factory. These names "
        "may appear in menus. Placements, production and objective text are retained.\n\n"
        "Loading, saving and reopening this edition remain unverified. Bank behavior, "
        "Oil/Power Plant and custom events still need testing. Keep this edition's "
        "own saves; it is not a verified crash fix.",
    ),
    "great-lakes-industry-identities-v2": RecipeChoice(
        "great-lakes-industry-identities-v2",
        "Mac industry compatibility v2",
        "Addresses a loading problem by giving two industries identifiers accepted "
        "by the Mac game. Military Barracks uses Doll Factory, and Port uses Bakery; "
        "these names may appear in build menus. Models, production, placement and "
        "objectives are retained.\n\n"
        "An earlier development edition of this repair passed fresh loading, "
        "saving, reopening and reloading. Check Mac Test & Identity for results "
        "recorded against your exact prepared edition. "
        "Trains, cargo, bridges, objectives and extended play still need testing. "
        "This edition remains experimental.",
    ),
    "hill-valley-steaks-tannery-city-v1": RecipeChoice(
        "hill-valley-steaks-tannery-city-v1",
        "Hill Valley train-car compatibility v1",
        "Adds the reviewed Cowhide Car v1/v2 model, dummy, texture and icon family "
        "from the preserved Side to Side 2 import, which must also be in your library. "
        "Tannery uses the Mac game's Doll Factory identifier; that name may appear "
        "in menus. An earlier development edition passed a bounded fresh-load "
        "test and reached the Hill Valley Steaks HUD. Check Mac Test & Identity "
        "for results recorded against your exact prepared edition. Trains, cargo, bridges, "
        "objectives, save/reload and extended play remain unverified; this edition "
        "is experimental and should keep its own saves.",
    ),
}


def recipe_choices(record, game_sha256: str) -> tuple[RecipeChoice, ...]:
    """Cheap menu candidates, never a validation or gameplay result.

    Only exact original identities are offered. Compilation rechecks the actual
    archive, executable, resources and references under the application lock.
    """
    choices = []
    for recipe_id, recipe in _RECIPES.items():
        original = _sha(
            json.dumps(
                dict(
                    schema=1,
                    recipe="original",
                    archive=recipe.archive_sha256,
                    game=recipe.game_sha256,
                    assets=recipe.assets_sha256,
                ),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        if (
            recipe_id in _CHOICES
            and record.archive_sha256 == recipe.archive_sha256
            and record.game_executable_sha256 == recipe.game_sha256 == game_sha256
            and record.variant_id == original
            and recipe.scenario_path in record.scenarios
        ):
            choices.append(_CHOICES[recipe_id])
    return tuple(choices)


def compiler_contract_digest() -> str:
    return _sha(
        json.dumps(
            dict(
                service=RECIPE_SERVICE_VERSION,
                spans=COMPILER_VERSION,
                resource_files=FILE_CONTEXT_ALGORITHM,
                metadata_policy=PRESERVE_RESOURCE_MTIMES,
                preparation_schema=4,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, expected: str, limit: int = 1024 * 1024, *, retain: bool = True) -> bytes:
    """Read bounded regular local bytes without following a link or FIFO."""
    _assert_no_symlink_ancestor(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
            raise RuleError("Recipe input is not a bounded regular file")
        pieces, size, digest = [], 0, hashlib.sha256()
        while size <= limit:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - size))
            if not chunk:
                break
            digest.update(chunk)
            if retain:
                pieces.append(chunk)
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
            raise RuleError("Recipe input changed while reading")
    finally:
        os.close(fd)
    data = b"".join(pieces)
    if digest.hexdigest() != expected:
        raise RuleError("Recipe input hash differs")
    return data


def _providers(stock_context: dict, source_context: dict) -> dict:
    """Index already bounded resource snapshots; reject opaque FPK providers."""
    found = {}
    for context in (stock_context, source_context):
        for root in context["roots"]:
            for file in root["files"]:
                basename = file["path"].rsplit("/", 1)[-1].casefold()
                if basename.endswith(".fpk"):
                    raise RuleError(
                        "This recipe requires reviewed loose readers; packed providers need separate review"
                    )
                found.setdefault(basename, []).append((root["role"], file))
    return found


def resolve_bundled_recipe(app, record, recipe_id: str):
    """Resolve a trusted recipe ID under the caller's single application lock."""
    if not isinstance(recipe_id, str) or recipe_id not in _RECIPES:
        raise RuleError("Unknown compatibility recipe")
    return _resolve(app, record, _RECIPES[recipe_id])


def inspect_bank_candidate(app, record, *, scenario_path: str, industry_path: str, model_path: str):
    """Read a source-only family assessment under the caller's application lock.

    This never offers or resolves a new recipe. It verifies the declared archive
    and current executable, measures complete map/stock contexts and rechecks
    them after bounded reads. Provider selection and full-map eligibility remain
    unknown even when the source predicate matches.
    """
    if (
        scenario_path not in record.scenarios
        or record.game_executable_sha256 != app.installation.executable_sha256
    ):
        raise RuleError("Bank candidate scenario or recorded build differs")
    archive = removal._leaf(app.originals, record.archive_sha256 + ".7z", r"[0-9a-f]{64}\.7z")
    _read(archive, record.archive_sha256, MAX_ARCHIVE_BYTES, retain=False)
    _read(
        app.installation.executable,
        app.installation.executable_sha256,
        512 * 1024 * 1024,
        retain=False,
    )
    source = removal._leaf(app.imports, record.imported_directory, r"[0-9a-f]{32}")
    source_context, stock_context = map_file_context(source), app._stock_file_context()
    files = {
        {"user_maps": "UserMaps", "custom_assets": "CustomAssets"}[root["role"]]
        + "/"
        + file["path"]: file
        for root in source_context["roots"]
        for file in root["files"]
    }
    data = []
    for path, limit in (
        (scenario_path, 1024 * 1024),
        (industry_path, 1024 * 1024),
        (model_path, bank_model.SOURCE_SIZE),
    ):
        if path not in files:
            raise RuleError("Bank candidate input is absent from source context")
        data.append(_read(source / path, files[path]["sha256"], limit))
    assessment = assess_bank_applicability(
        archive_sha256=record.archive_sha256,
        game_sha256=app.installation.executable_sha256,
        source_context=source_context,
        stock_context=stock_context,
        scenario_path=scenario_path,
        scenario_data=data[0],
        industry_path=industry_path,
        industry_data=data[1],
        model_path=model_path,
        model_data=data[2],
    )
    if (
        map_file_context(source)["identity"] != source_context["identity"]
        or app._stock_file_context()["identity"] != stock_context["identity"]
    ):
        raise RuleError("Bank candidate resource context changed during inspection")
    return assessment


def _resolve(app, record, recipe: _IdentityRecipe):
    if (
        record.archive_sha256 != recipe.archive_sha256
        or record.game_executable_sha256 != recipe.game_sha256
        or app.installation.executable_sha256 != recipe.game_sha256
        or recipe.scenario_path not in record.scenarios
    ):
        raise RuleError("Recipe does not apply to this archive, scenario or game")
    archive = removal._leaf(app.originals, record.archive_sha256 + ".7z", r"[0-9a-f]{64}\.7z")
    _read(archive, recipe.archive_sha256, MAX_ARCHIVE_BYTES, retain=False)
    source = removal._leaf(app.imports, record.imported_directory, r"[0-9a-f]{32}")
    _assert_regular_tree(source)
    if assets_manifest_hash(source) != recipe.assets_sha256:
        raise RuleError("Recipe original asset inventory differs")
    source_context = map_file_context(source)
    stock_context = app._stock_file_context()
    providers = _providers(stock_context, source_context)
    registry = extract_supported_industry_registry(
        _read(app.installation.executable, recipe.game_sha256, limit=512 * 1024 * 1024)
    )
    stock_root = (
        app.installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets"
    )

    def unique(name, role, expected):
        candidates = providers.get(name.casefold(), [])
        if (
            len(candidates) != 1
            or candidates[0][0] != role
            or candidates[0][1]["sha256"] != expected
        ):
            raise RuleError(
                "Recipe reader basename is missing, ambiguous or has unexpected content"
            )
        return candidates[0][1]["path"]

    industry_stock_path = unique(
        "rrt_industries.xml", "installed_assets", recipe.stock_industries_sha256
    )
    depot_stock_path = unique("rrt_depots.xml", "installed_assets", recipe.stock_depots_sha256)
    for path, expected in (
        (recipe.scenario_path, recipe.scenario_sha256),
        (recipe.industry_path, recipe.industry_before),
    ):
        install_root, relative = path.split("/", 1)
        role = {"UserMaps": "user_maps", "CustomAssets": "custom_assets"}[install_root]
        if unique(path.rsplit("/", 1)[-1], role, expected) != relative:
            raise RuleError("Recipe reader path differs")
    scenario_data = _read(source / recipe.scenario_path, recipe.scenario_sha256)
    scenario = _parse(scenario_data)
    refs = [n for n in scenario if n.path[-1] == "IndustriesXMLFile"]
    if len(refs) != 1 or refs[0].raw_value != recipe.industry_path.rsplit("/", 1)[-1]:
        raise RuleError("Recipe scenario industry reader differs")
    stock_industries = _parse(
        _read(stock_root / industry_stock_path, recipe.stock_industries_sha256)
    )
    stock_depots = _parse(_read(stock_root / depot_stock_path, recipe.stock_depots_sha256))
    source_data = _read(source / recipe.industry_path, recipe.industry_before)
    industry_nodes = _parse(source_data)
    key_path = ("RRTIndustries", "Industries", "RRTIndustry", "szName")
    base_names = {n.raw_value for n in stock_industries if n.path == key_path}
    base_annexes = {n.raw_value for n in stock_depots if n.path == ("RRTDepots", "Depot", "szName")}
    map_names = {n.raw_value for n in industry_nodes if n.path == key_path}
    aliases = {}
    for old, slot, target in recipe.aliases:
        if (
            type(slot) is not int
            or slot < 0
            or slot >= len(registry)
            or registry[slot] != target
            or old in registry
            or old not in map_names
            or target in map_names
            or old in base_names
            or target in base_names
            or old + " Annex" in base_annexes
            or target + " Annex" in base_annexes
            or old in aliases
            or target in aliases.values()
        ):
            raise RuleError("Recipe registry slot, collision or stock-absent precondition differs")
        aliases[old] = target
        definition = next(
            n.parent for n in industry_nodes if n.path == key_path and n.raw_value == old
        )
        for field, value in (("bInCity", "1"), ("bIsMountainous", "0")):
            values = [n for n in definition.children if n.path[-1] == field]
            if len(values) != 1 or values[0].children or values[0].raw_value != value:
                raise RuleError(
                    "Initial identity recipe requires explicit reviewed city/mountain fields"
                )
    final_names = (map_names - aliases.keys()) | set(aliases.values())
    if not final_names <= set(registry) or len(final_names) > len(registry):
        raise RuleError("Recipe does not yield distinct registered identities for all definitions")
    # A complete asset hash gates every file. Additionally classify all XML
    # occurrences outside the industry document. Only explicitly declared
    # scenario ownership references may change; other occurrences still refuse.
    relevant = set(aliases) | set(aliases.values())
    if bool(recipe.ownership_edits) != bool(recipe.scenario_after) or any(
        edit.kind != "industry-goal" or aliases.get(edit.source) != edit.target
        for edit in recipe.ownership_edits
    ):
        raise RuleError("Recipe ownership edits do not match the declared industry aliases")
    for root in source_context["roots"]:
        prefix = {"custom_assets": "CustomAssets", "user_maps": "UserMaps"}[root["role"]]
        for file in root["files"]:
            path = prefix + "/" + file["path"]
            if not path.casefold().endswith(".xml") or path == recipe.industry_path:
                continue
            for node in _parse(_read(source / path, file["sha256"])):
                declared_goal = path == recipe.scenario_path and any(
                    node.path == edit.path and node.raw_value == edit.source
                    for edit in recipe.ownership_edits
                )
                if (
                    not declared_goal
                    and (
                        node.raw_value in relevant
                        or node.path[-2:] == ("OwnListItem", "szObjectName")
                        and node.raw_value.casefold() in {x.casefold() for x in relevant}
                    )
                    or any(v in relevant for v in node.attributes.values())
                ):
                    raise RuleError(
                        "Recipe has an unreviewed identity occurrence in another XML file"
                    )
    patch = compile_identity_patch(
        path=recipe.industry_path,
        data=source_data,
        before_sha256=recipe.industry_before,
        after_sha256=recipe.industry_after,
        edits=tuple(IdentityEdit("industry", old, target) for old, target in aliases.items()),
    )
    patches = [patch]
    if recipe.ownership_edits:
        patches.append(
            compile_identity_patch(
                path=recipe.scenario_path,
                data=scenario_data,
                before_sha256=recipe.scenario_sha256,
                after_sha256=recipe.scenario_after,
                edits=recipe.ownership_edits,
            )
        )
    if recipe.legacy_bank_path:
        install_root, relative = recipe.legacy_bank_path.split("/", 1)
        role = {"UserMaps": "user_maps", "CustomAssets": "custom_assets"}[install_root]
        if unique(relative.rsplit("/", 1)[-1], role, bank_model.SOURCE_SHA256) != relative:
            raise RuleError("Recipe bank model provider path differs")
        original = _read(
            source / recipe.legacy_bank_path, bank_model.SOURCE_SHA256, bank_model.SOURCE_SIZE
        )
        patches.append(compile_bank_patch(path=recipe.legacy_bank_path, data=original))
    additions = []
    if recipe.additions:
        donor_record = _donor_record(app)
        donor = removal._leaf(app.imports, donor_record.imported_directory, r"[0-9a-f]{32}")
        _assert_regular_tree(donor)
        source_files = {
            {"custom_assets": "CustomAssets", "user_maps": "UserMaps"}[root["role"]]
            + "/"
            + file["path"]: file
            for root in source_context["roots"]
            for file in root["files"]
        }
        for destination_path, donor_path, expected in recipe.additions:
            donor_file = donor / donor_path
            if donor_file.is_symlink() or not donor_file.is_file():
                raise RuleError("Reviewed Cowhide donor asset is missing: " + donor_path)
            data = _read(donor_file, expected, MAX_PATCHED_FILE_BYTES)
            mtime_ns = donor_file.stat().st_mtime_ns
            existing = source_files.get(destination_path)
            if existing is not None:
                if existing["sha256"] != expected or existing["size"] != len(data):
                    raise RuleError(
                        "Recipe addition target has different content: " + destination_path
                    )
                # Bind the observed target metadata; apply_rules still rejects later drift.
                mtime_ns = existing["mtime_ns"]
            elif (source / destination_path).exists() or (source / destination_path).is_symlink():
                raise RuleError("Recipe addition target is not a regular file: " + destination_path)
            additions.append(AssetAddition(destination_path, expected, data, mtime_ns))
    if (
        map_file_context(source)["identity"] != source_context["identity"]
        or app._stock_file_context()["identity"] != stock_context["identity"]
    ):
        raise RuleError("Recipe resource context changed during compilation")
    binding = RecipePreparationBinding(
        recipe.recipe_id,
        _sha(json.dumps(asdict(recipe), sort_keys=True, separators=(",", ":")).encode()),
        compiler_contract_digest(),
        source_context["identity"],
        stock_context["identity"],
    )
    rule = CompatibilityRule(
        recipe.recipe_id,
        2,
        "Reviewed fixed-registry and exact original-input comparison",
        (
            "Experimental exact bank serialization and coupled industry ownership identities. "
            "Existing dependency concerns remain; rendering, collision and gameplay need separate validation."
            if recipe.legacy_bank_path
            else "Experimental distinct industry identities. Authored asset payload is preserved; internal aliases may remain visible. Gameplay must be tested separately."
        ),
        recipe.archive_sha256,
        recipe.game_sha256,
        tuple(patches),
        tuple(additions),
    )
    return (rule,), binding


def _donor_record(app):
    """Reuse the existing conditional donor selection, including its fallback."""
    donor_records = [
        item
        for item in app.catalogue()
        if item.archive_sha256 == _HILL_COWHIDE_DONOR_ARCHIVE
        and item.variant_id == item.profile_id.replace("map-", "", 1)
    ]
    if len(donor_records) != 1:
        donor_records = [
            item
            for item in app.catalogue()
            if item.archive_sha256 == _HILL_COWHIDE_DONOR_ARCHIVE
            and item.name == "Side_to_Side_2_v1_00"
        ]
    if len(donor_records) != 1:
        raise RuleError("Reviewed Cowhide donor import is unavailable")
    return donor_records[0]


def plan_recipe_job(app, record, recipe_id: str, *, resolved=None) -> dict:
    """Project the existing resolver result without retaining payload bytes.

    Caller holds the application lock. Donor archive identity is a catalogue
    claim, as in the existing resolver; only donor member bytes are verified.
    """
    # Preparation can compare the review with the exact rules it will consume,
    # without resolving a second, potentially different resource snapshot.
    rules, binding = resolved or resolve_bundled_recipe(app, record, recipe_id)
    recipe = _RECIPES[recipe_id]
    changes = []
    for rule in rules:
        for patch in rule.patches:
            changes.append(
                dict(
                    kind="patch",
                    path=patch.path,
                    before_sha256=patch.before_sha256,
                    after_sha256=patch.after_sha256,
                )
            )
        for addition in rule.additions:
            donor_path = next(
                source
                for target, source, digest in recipe.additions
                if target == addition.path and digest == addition.after_sha256
            )
            changes.append(
                dict(
                    kind="addition",
                    path=addition.path,
                    donor_path=donor_path,
                    after_sha256=addition.after_sha256,
                    size=len(addition.data),
                    mtime_ns=addition.mtime_ns,
                )
            )
    donor = _donor_record(app) if recipe.additions else None
    return dict(
        original_variant_id=record.variant_id,
        archive_sha256=record.archive_sha256,
        game_executable_sha256=record.game_executable_sha256,
        imported_directory=record.imported_directory,
        binding=binding.as_json(),
        donor=(
            dict(
                archive_sha256=donor.archive_sha256,
                variant_id=donor.variant_id,
                profile_id=donor.profile_id,
                imported_directory=donor.imported_directory,
                archive_bytes_verified=False,
                member_bytes_verified=True,
            )
            if donor
            else None
        ),
        changes=sorted(changes, key=lambda change: (change["path"], change["kind"])),
    )
