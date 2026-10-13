"""Bounded static diagnostics. A clean report is never proof of gameplay."""

from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from .model_checks import inspect_models

MAX_XML_BYTES = 8 * 1024 * 1024
MAX_TOTAL_XML = 64 * 1024 * 1024
MAX_XML_FILES = 1000
IMPORT_CHECKS_VERSION = "loose-xml-and-model-headers-v3"


def inspect_map(root: Path, stock_names: set[str] | None = None) -> dict:
    files = [
        p for name in ("CustomAssets", "UserMaps") for p in (root / name).rglob("*") if p.is_file()
    ]
    names = {p.name.casefold() for p in files} | (stock_names or set())
    compact = {re.sub(r"\s+", "", name): name for name in names}
    warnings = []
    checked = 0
    total = 0
    examined = 0
    incomplete_reasons = []
    for path in files:
        if path.suffix.casefold() != ".xml":
            continue
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if checked >= MAX_XML_FILES or total + size > MAX_TOTAL_XML:
            if checked >= MAX_XML_FILES:
                incomplete_reasons.append("xml_file_limit")
            if total + size > MAX_TOTAL_XML:
                incomplete_reasons.append("xml_total_bytes_limit")
            warnings.append("XML scan limit reached; remaining files were not checked.")
            break
        if size > MAX_XML_BYTES:
            incomplete_reasons.append("xml_size_limit")
            warnings.append(relative + ": XML exceeds the scan size limit.")
            continue
        checked += 1
        total += size
        with path.open("rb") as stream:
            data = stream.read(size + 1)
        if len(data) != size:
            raise OSError("XML file size changed during inspection: " + relative)
        if (
            b"<!DOCTYPE" in data.replace(b"\0", b"").upper()
            or b"<!ENTITY" in data.replace(b"\0", b"").upper()
        ):
            incomplete_reasons.append("xml_declaration_refused")
            warnings.append(relative + ": XML declarations require manual inspection.")
            continue
        try:
            document = ET.fromstring(data)
        except (ET.ParseError, ValueError) as exc:
            incomplete_reasons.append("xml_parse_failed")
            warnings.append(relative + ": XML could not be parsed (" + str(exc)[:120] + ").")
            continue
        for node in document.iter():
            # Only explicit XML filenames: model names and packed asset lookups
            # have different resolution rules and cannot be proved here.
            for value in [node.text or "", *node.attrib.values()]:
                value = value.strip().replace("\\", "/")
                if not re.fullmatch(r"[^<>\r\n]{1,240}\.xml", value, re.IGNORECASE):
                    continue
                filename = value.rsplit("/", 1)[-1].casefold()
                if filename not in names:
                    near = compact.get(re.sub(r"\s+", "", filename))
                    detail = (
                        ("possible filename mismatch with " + near)
                        if near
                        else "reference not found among loose map/game XML files; it may be packed or unavailable"
                    )
                    warnings.append(relative + ": " + value + " — " + detail + ".")
        if len(warnings) >= 200:
            incomplete_reasons.append("xml_finding_limit")
            warnings = warnings[:200] + ["Further findings omitted; inspect the package manually."]
            break
        examined += 1
    models = inspect_models(root)
    for finding in models["findings"][:200]:
        detail = (
            "legacy NIF "
            + finding["version"]
            + " uses a model-loading path implicated in a Mac crash; conversion review required (header-only risk flag)"
            if finding.get("legacy_collision_path")
            else finding["reason"]
        )
        warnings.append(finding["path"] + ": " + detail + ".")
    if not models["complete"]:
        warnings.append(
            "Model scan incomplete: a scan limit, unreadable entry or skipped link prevented full header inspection."
        )
    return dict(
        schema=1,
        xml_files_checked=checked,
        xml_scan=dict(
            complete=not incomplete_reasons,
            files_discovered=sum(p.suffix.casefold() == ".xml" for p in files),
            files_examined=examined,
            incomplete_reasons=list(dict.fromkeys(incomplete_reasons)),
            scope="Complete means all discovered loose XML examined successfully within the existing scan limits only; native dependency resolution, compatibility and gameplay remain unknown.",
        ),
        model_headers=models,
        warnings=list(dict.fromkeys(warnings)),
        limitations="Static checks do not run the game, inspect packed FPK contents, or prove loading, train/bridge behavior, performance or save/reload reliability.",
    )
