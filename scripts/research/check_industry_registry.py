#!/usr/bin/env python3
"""Read-only industry identity audit; prints JSON, never launches or repairs maps.

The identity table is read from the user's own exact supported executable.
No game identity table, assets or reconstructed game code are distributed here.
An outside-table identity is a static risk, not proof of a crash. A clean audit
does not establish that a map loads, plays or saves correctly.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import xml.etree.ElementTree as ET

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))
from smr_launcher.industry_registry import (  # noqa: E402
    INDUSTRY_TABLE_ADDRESS as TABLE_ADDRESS,
    INDUSTRY_TABLE_COUNT as TABLE_COUNT,
    SUPPORTED_GAME_SHA256 as GAME_SHA256,
    IndustryRegistryError as AuditError,
    _extract_registry,
)

MAX_XML_BYTES = 16 * 1024 * 1024
MAX_TREE_ENTRIES = 100_000


def extract_registry(data: bytes, expected_hash: str = GAME_SHA256,
                     address: int = TABLE_ADDRESS, count: int = TABLE_COUNT) -> tuple[str, ...]:
    """Backwards-compatible research wrapper around the runtime Mach-O reader."""
    return _extract_registry(data, expected_hash, address, count)


def read_xml(path: Path) -> tuple[ET.Element, str]:
    flags = os.O_RDONLY
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise AuditError("Safe no-follow, nonblocking XML reads are unavailable")
    flags |= os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AuditError("XML could not be opened as a regular file") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_XML_BYTES:
            raise AuditError("XML must be a regular file of at most 16 MiB")
        chunks = []
        remaining = MAX_XML_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        current_path = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise AuditError("XML changed or could not be read safely") from error
    finally:
        os.close(descriptor)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_size,
                             item.st_mtime_ns, item.st_ctime_ns)
    if (not stat.S_ISREG(current_path.st_mode) or len(data) != before.st_size
            or len(data) > MAX_XML_BYTES
            or identity(before) != identity(after) or identity(after) != identity(current_path)):
        raise AuditError("XML changed, had a short read, or is not a regular file")
    declarations = data.replace(b"\0", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise AuditError("DTD/entity declarations are outside this audit's supported XML subset")
    return ET.fromstring(data), hashlib.sha256(data).hexdigest()


def xml_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise AuditError("Map root must be a regular directory")
    found = []
    entries = 0

    def scan_error(error):
        raise AuditError(f"XML tree traversal failed: {error}") from error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
        base = Path(directory)
        dirs.sort()
        files.sort()
        for name in dirs:
            entries += 1
            if entries > MAX_TREE_ENTRIES:
                raise AuditError("XML tree exceeds the bounded entry limit")
            path = base / name
            try:
                info = path.lstat()
                mode = info.st_mode
            except OSError as error:
                raise AuditError(f"XML tree entry could not be inspected: {path}") from error
            if stat.S_ISLNK(mode):
                raise AuditError(f"XML tree contains a symlink directory: {path}")
            if not stat.S_ISDIR(mode):
                raise AuditError(f"XML tree contains a non-directory traversal entry: {path}")
        for name in files:
            entries += 1
            if entries > MAX_TREE_ENTRIES:
                raise AuditError("XML tree exceeds the bounded entry limit")
            path = base / name
            try:
                info = path.lstat()
                mode = info.st_mode
            except OSError as error:
                raise AuditError(f"XML tree file entry could not be inspected: {path}") from error
            if stat.S_ISLNK(mode):
                raise AuditError(f"XML tree contains a symlink entry: {path}")
            if not name.casefold().endswith(".xml"):
                continue
            if not stat.S_ISREG(mode):
                raise AuditError(f"XML path is not a regular file: {path}")
            if info.st_size > MAX_XML_BYTES:
                raise AuditError(f"XML exceeds the 16 MiB limit: {path}")
            found.append(path)
    return found


def audit_map(root: Path, registry: tuple[str, ...], scenarios: list[str] | None = None,
              stock_xml: Path | None = None) -> dict:
    root = Path(root).absolute()
    files = xml_files(root)
    by_name = defaultdict(list)
    for path in files:
        by_name[path.name.casefold()].append(path)
    if scenarios is None:
        selected = [p for p in files if p.name.lower().startswith("rrt_scenario")]
    else:
        selected = []
        for name in scenarios:
            candidate = root / name
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):
                raise AuditError("Scenario path escapes the map root")
            selected.append(candidate)
    known = set(registry)
    rows = []
    for scenario in selected:
        row = {"scenario": str(scenario.relative_to(root)), "status": "unresolved"}
        try:
            tree, row["scenario_sha256"] = read_xml(scenario)
            reference = (tree.findtext(".//IndustriesXMLFile") or "").strip()
            row["reference"] = reference
            if not reference:
                raise AuditError("No explicit IndustriesXMLFile reference")
            basename = reference.replace("\\", "/").rsplit("/", 1)[-1].casefold()
            candidates = by_name[basename]
            nearby = [p for p in candidates if p.parent == scenario.parent]
            if len(nearby) == 1:
                industry_file = nearby[0]
                resolution = "same scenario directory, case-insensitive filename"
            elif len(candidates) == 1:
                industry_file = candidates[0]
                resolution = "unique package filename; engine priority not simulated"
            elif candidates:
                row["candidates"] = [str(p.relative_to(root)) for p in candidates]
                raise AuditError("Ambiguous industry XML filename")
            elif stock_xml is not None:
                fallback = [p for p in stock_xml.iterdir() if p.name.casefold() == basename]
                if len(fallback) != 1:
                    raise AuditError("No unique loose stock XML fallback")
                industry_file = fallback[0]
                resolution = "loose stock fallback"
            else:
                raise AuditError("Not found as loose map XML; stock/FPK fallback not inspected")
            industry_tree, digest = read_xml(industry_file)
            definitions = []
            for item in industry_tree.findall("./Industries/RRTIndustry"):
                name = item.findtext("szName") or ""
                locations = item.findall("./Locations/Location")
                definitions.append({"name": name, "in_registry": name in known,
                                    "has_surrounding_whitespace": name != name.strip(),
                                    "explicit_locations": len(locations),
                                    "locations_in_city": sum(bool((p.findtext("InCity") or "").strip())
                                                             for p in locations)})
            if not definitions:
                raise AuditError("No RRTIndustries/Industries/RRTIndustry definitions")
            outside = [item for item in definitions if not item["in_registry"]]
            counts = Counter(item["name"] for item in definitions)
            row.update(resolution=resolution, industry_file=str(industry_file), industry_sha256=digest,
                       definition_count=len(definitions), outside_registry=outside,
                       duplicate_names=[name for name, count in counts.items() if count > 1],
                       status="potential missing industry index" if outside else "no outside-registry identity detected")
        except (OSError, ValueError, ET.ParseError) as error:
            row["error"] = str(error)
        rows.append(row)
    return {"root": str(root), "scenarios": rows, "scenario_count": len(rows),
            "potential_risk": any(row.get("outside_registry") for row in rows),
            "explicit_placement_risk": any(item["explicit_locations"] > 0 for row in rows
                                            for item in row.get("outside_registry", [])),
            "unresolved": not rows or any(row["status"] == "unresolved" for row in rows)}


def discover_installed_library() -> tuple[Path, Path, Path]:
    """Reuse discovery only; never enroll, lock, repair or activate a profile."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from smr_launcher.application import LauncherApplication
    app = LauncherApplication.discover()
    binary = app.installation.executable
    stock = binary.parents[3] / "SMRailroadsData/assets/xml"
    return binary, app.library, stock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-binary", type=Path)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--map-root", type=Path, help="One imported or prepared map directory")
    scope.add_argument("--library", type=Path, help="Launcher library containing catalogue.json")
    scope.add_argument("--installed-library", action="store_true",
                       help="Discover the installed Steam game, launcher library and stock XML automatically")
    parser.add_argument("--library-source", choices=("imports", "prepared"), default="imports")
    parser.add_argument("--stock-xml", type=Path, help="Optional installed SMRailroadsData/assets/xml directory")
    args = parser.parse_args()
    if args.installed_library and (args.game_binary or args.stock_xml):
        parser.error("--installed-library discovers its binary and stock XML; omit explicit paths")
    if not args.installed_library and not args.game_binary:
        parser.error("--game-binary is required with --map-root or --library")
    try:
        if args.installed_library:
            args.game_binary, args.library, args.stock_xml = discover_installed_library()
        if args.game_binary.stat().st_size > 256 * 1024 * 1024:
            raise AuditError("Executable exceeds the supported audit size")
        data = args.game_binary.read_bytes()
        registry = extract_registry(data)
        results = []
        if args.map_root:
            results.append(audit_map(args.map_root, registry, stock_xml=args.stock_xml))
        else:
            catalogue = json.loads((args.library / "catalogue.json").read_text())
            for entry in catalogue["maps"]:
                directory = entry["imported_directory" if args.library_source == "imports" else "prepared_directory"]
                if not directory or Path(directory).name != directory or directory in (".", ".."):
                    raise AuditError("Invalid catalogue directory")
                result = audit_map(args.library / args.library_source / directory, registry,
                                   entry.get("scenarios"), args.stock_xml)
                result.update(name=entry["name"], variant_id=entry["variant_id"], directory=directory)
                results.append(result)
        unique = {row.get("directory", row["root"]): row for row in results}
        report = {"schema": 1, "game_sha256": hashlib.sha256(data).hexdigest(),
                  "registry_entries": len(registry), "source": args.library_source if args.library else "map-root",
                  "runtime_checks": 0, "limitations": [
                      "Static identity membership only; no game launch, animation inspection or map repair.",
                      "A risk flag does not prove a crash; no flag does not verify loading, gameplay or saves.",
                      "Exact XML string comparison; engine case and whitespace handling remain unverified.",
                      "Loose XML only; ambiguous files and embedded FPK dependencies remain unresolved."],
                  "summary": {"variants": len(results), "unique_directories": len(unique),
                              "unique_with_risk": sum(row["potential_risk"] for row in unique.values()),
                              "unique_with_placement_risk": sum(row["explicit_placement_risk"] for row in unique.values()),
                              "unique_unresolved": sum(row["unresolved"] for row in unique.values())},
                  "maps": results}
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, struct.error) as error:
        print("Industry audit failed: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
