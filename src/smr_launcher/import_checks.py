"""Bounded static diagnostics. A clean report is never proof of gameplay."""
from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET

MAX_XML_BYTES = 8 * 1024 * 1024
MAX_TOTAL_XML = 64 * 1024 * 1024
MAX_XML_FILES = 1000


def inspect_map(root: Path, stock_names: set[str] | None = None) -> dict:
    files = [p for name in ("CustomAssets", "UserMaps") for p in (root / name).rglob("*") if p.is_file()]
    names = {p.name.casefold() for p in files} | (stock_names or set())
    compact = {re.sub(r"\s+", "", name): name for name in names}
    warnings = []
    checked = 0
    total = 0
    for path in files:
        if path.suffix.casefold() != ".xml":
            continue
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if checked >= MAX_XML_FILES or total + size > MAX_TOTAL_XML:
            warnings.append("XML scan limit reached; remaining files were not checked.")
            break
        if size > MAX_XML_BYTES:
            warnings.append(relative + ": XML exceeds the scan size limit.")
            continue
        checked += 1
        total += size
        data = path.read_bytes()
        if b"<!DOCTYPE" in data.replace(b"\0", b"").upper() or b"<!ENTITY" in data.replace(b"\0", b"").upper():
            warnings.append(relative + ": XML declarations require manual inspection.")
            continue
        try:
            document = ET.fromstring(data)
        except (ET.ParseError, ValueError) as exc:
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
                    detail = ("possible filename mismatch with " + near) if near else "reference not found among loose map/game XML files; it may be packed or unavailable"
                    warnings.append(relative + ": " + value + " — " + detail + ".")
        if len(warnings) >= 200:
            warnings = warnings[:200] + ["Further findings omitted; inspect the package manually."]
            break
    return dict(schema=1, xml_files_checked=checked, warnings=list(dict.fromkeys(warnings)),
                limitations="Static checks do not run the game, inspect packed FPK contents, or prove loading, train/bridge behavior, performance or save/reload reliability.")
