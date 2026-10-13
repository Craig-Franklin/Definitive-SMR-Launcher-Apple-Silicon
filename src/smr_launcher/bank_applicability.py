"""Source-only applicability for the exact legacy bank conversion family.

Candidate means matching content and direct loose XML references, never a native
provider winner or permission to prepare/launch a full map. The existing exact
recipe remains responsible for preparation admission. No map names select this
family, and no source assessment in this slice establishes full-map eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from . import bank_model
from .compatibility_recipes import _parse
from .industry_registry import SUPPORTED_GAME_SHA256
from .recipe_identity import FILE_CONTEXT_ALGORITHM
from .rules import RuleError, TokenPatch


FAMILY_ID = "exact-legacy-bank-v1"
_UNKNOWNS = (
    "native-provider-selection",
    "complete-scenario-dependency-context",
    "render-collision-feature-gameplay-save-reopen",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii"))


def _files(context: dict) -> dict:
    """Accept only canonical file contexts from the existing identity API."""
    payload = {key: value for key, value in context.items() if key != "identity"}
    if (
        context.get("schema") != 1
        or context.get("algorithm") != FILE_CONTEXT_ALGORITHM
        or context.get("identity") != _digest(payload)
    ):
        raise RuleError("Bank assessment needs an unchanged file context")
    found = {}
    for root in context["roots"]:
        for file in root["files"]:
            key = (root["role"], file["path"])
            if key in found:
                raise RuleError("Bank assessment has duplicate context entries")
            found[key] = file
    return found


def _source_file(files: dict, path: str, data: bytes) -> None:
    parts = path.split("/")
    if (
        len(parts) < 2
        or parts[0] not in ("UserMaps", "CustomAssets")
        or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts)
    ):
        raise RuleError("Bank assessment path is outside map resources")
    role = {"UserMaps": "user_maps", "CustomAssets": "custom_assets"}[parts[0]]
    entry = files.get((role, "/".join(parts[1:])))
    if (
        type(data) is not bytes
        or entry is None
        or entry["sha256"] != _sha(data)
        or entry["size"] != len(data)
    ):
        raise RuleError("Bank assessment bytes differ from source context")


@dataclass(frozen=True)
class BankApplicability:
    """An affected source scenario and refusal reasons, with complete context ID.

    All statuses retain unknowns. Even source-candidate is insufficient for a
    new full-map recipe or runtime admission; callers must not convert it to GO.
    """

    status: str
    scenario_path: str
    scenario_sha256: str
    model_path: str
    binding_sha256: str
    exclusions: tuple[str, ...]
    unknowns: tuple[str, ...]
    family_id: str = FAMILY_ID


def assess_bank_applicability(
    *,
    archive_sha256: str,
    game_sha256: str,
    source_context: dict,
    stock_context: dict,
    scenario_path: str,
    scenario_data: bytes,
    industry_path: str,
    industry_data: bytes,
    model_path: str,
    model_data: bytes,
) -> BankApplicability:
    """Assess supplied snapshots without I/O, conversion or provider inference.

    The service measures and rechecks these contexts under its application lock.
    SHA bindings identify supplied evidence, not its authority or engine meaning.
    Only one direct root industry reference and one direct industry model field
    are supported. Case-only matches remain explicit unknowns.
    """
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (archive_sha256, game_sha256)
    ):
        raise RuleError("Bank assessment needs exact archive and game SHA-256 identities")
    source, stock = _files(source_context), _files(stock_context)
    for path, data in (
        (scenario_path, scenario_data),
        (industry_path, industry_data),
        (model_path, model_data),
    ):
        _source_file(source, path, data)
    exclusions, unknowns = [], list(_UNKNOWNS)
    if game_sha256 != SUPPORTED_GAME_SHA256:
        exclusions.append("unsupported-game-build")
    if len(model_data) != bank_model.SOURCE_SIZE or _sha(model_data) != bank_model.SOURCE_SHA256:
        exclusions.append("unsupported-bank-content")
    try:
        scenario, industries = _parse(scenario_data), _parse(industry_data)
    except RuleError:
        exclusions.append("unsupported-reference-xml")
        scenario, industries = [], []
    reference_paths = (
        (scenario, ("RRTScenario", "IndustriesXMLFile"), industry_path),
        (industries, ("RRTIndustries", "Industries", "RRTIndustry", "szModel"), model_path),
    )
    for nodes, field_path, target in reference_paths:
        basename = target.rsplit("/", 1)[-1]
        fields = [n for n in nodes if n.path == field_path]
        matches = [n for n in fields if n.raw_value.casefold() == basename.casefold()]
        if (
            len(matches) != 1
            or matches[0].children
            or field_path[-1] == "IndustriesXMLFile"
            and len(fields) != 1
            or field_path[-1] == "szModel"
            and (
                sum(n.path == ("RRTIndustries", "Industries") for n in nodes) != 1
                or sum(c.path[-1] == "szModel" for c in matches[0].parent.children) != 1
            )
        ):
            exclusions.append("unsupported-direct-" + field_path[-1])
        elif matches[0].raw_value != basename:
            unknowns.append("reference-case-" + field_path[-1])
    providers = list(source.items()) + list(stock.items())
    if any(path.casefold().endswith(".fpk") for (_role, path), _file in providers):
        unknowns.append("packed-providers")
    for path in (scenario_path, industry_path, model_path):
        basename = path.rsplit("/", 1)[-1].casefold()
        if sum(p.rsplit("/", 1)[-1].casefold() == basename for (_r, p), _f in providers) != 1:
            unknowns.append("ambiguous-loose-" + path.rsplit("/", 1)[-1])
    binding = _digest(
        dict(
            family=FAMILY_ID,
            archive=archive_sha256,
            game=game_sha256,
            source=source_context["identity"],
            stock=stock_context["identity"],
            scenario=scenario_path,
            industry=industry_path,
            model=model_path,
            source_contract=(
                bank_model.SOURCE_SHA256,
                bank_model.SOURCE_SIZE,
                bank_model.SOURCE_VERSION,
            ),
            output_contract=(
                bank_model.OUTPUT_SHA256,
                bank_model.OUTPUT_SIZE,
                bank_model.OUTPUT_VERSION,
            ),
        )
    )
    status = "source-candidate"
    if exclusions:
        status = "excluded"
    elif len(unknowns) > len(_UNKNOWNS):
        status = "unknown"
    return BankApplicability(
        status,
        scenario_path,
        _sha(scenario_data),
        model_path,
        binding,
        tuple(exclusions),
        tuple(unknowns),
    )


def compile_bank_patch(*, path: str, data: bytes) -> TokenPatch:
    """Compile the exact sealed byte edit; caller owns context admission.

    Reuses the converter's content/contract cache, never caches applicability.
    This pure mechanical operation alone does not qualify a map for repair.
    """
    try:
        converted = bank_model.convert_legacy_bank(data)
    except ValueError as exc:
        raise RuleError("Recipe bank conversion refused") from exc
    return TokenPatch(path, bank_model.SOURCE_SHA256, bank_model.OUTPUT_SHA256, data, converted)
