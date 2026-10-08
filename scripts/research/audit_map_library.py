#!/usr/bin/env python3
"""Read-only, per-scenario compatibility research report; never repairs or launches.

Uses loose XML candidates and reconstructed loader rules for one hash-gated game.
Virtual filesystem precedence, packed assets and runtime behavior remain unknown.
All output is local research evidence, not a compatibility certification.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_industry_registry import (AuditError, discover_installed_library,
                                     extract_registry, read_xml, xml_files)

ANALYZER_VERSION = "1.1.0-research"
KINDS = {
    "goods": ("GoodsXMLFile", "RRT_Goods.xml", "./Good"),
    "industries": ("IndustriesXMLFile", "RRT_Industries.xml", "./Industries/RRTIndustry"),
    "cars": ("TrainCarsXMLFile", "RRT_TrainCars.xml", "./RRTGoodCars/TrainCar"),
    "tenders": ("TrainCarsXMLFile", "RRT_TrainCars.xml", "./RRTTenderCars/TenderCar"),
    "depots": ("DepotsXMLFile", "RRT_Depots.xml", "./Depot"),
    "bridges": ("BridgeXMLFile", "RRT_Bridges.xml", "./Bridge"),
}


def value(node, key):
    return node.findtext(key) or ""


def root_reference_occurrences(tree):
    """Ordered direct-child XML references, retaining empty and exact text.

    Multiplicity is documentary evidence only: native duplicate selection is
    unknown even when every occurrence has the same text.
    """
    return [{"field": node.tag, "root_child_index": index, "text": node.text}
            for index, node in enumerate(tree) if node.tag.endswith("XMLFile")]


def engine_bool(text):
    lowered = text.strip().casefold()
    if lowered in ("true", "false"):
        return lowered == "true"
    integer = re.match(r"^[+-]?\d+", lowered)
    return int(integer[0]) != 0 if integer else False


def merge_records(base, scenario):
    """Named records: scenario with entries prunes absent base; scalars inherit.

    This helper represents top-level fields only. Vector child replacement is
    handled explicitly by consumers; empty vector containers retain base data.
    """
    before = {value(item, "szName"): item for item in base}
    after = {value(item, "szName"): item for item in scenario}
    names = list(after) if after else list(before)
    return {name: (before.get(name), after.get(name)) for name in names}


def field(pair, key):
    base, override = pair
    node = override.find(key) if override is not None else None
    if node is not None:
        return node.text or ""
    return value(base, key) if base is not None else ""


def sequence(pair, path):
    base, override = pair
    nodes = override.findall(path) if override is not None else []
    return nodes or (base.findall(path) if base is not None else [])


def goods_registry(base, scenario):
    """Goods numeric registry uses base names filtered by nonempty scenario list."""
    names = {value(item, "szName") for item in base}
    selected = {value(item, "szName") for item in scenario}
    return names & selected if selected else names


class Scanner:
    def __init__(self, stock: Path, registry, bridge_registry=None):
        self.stock = stock
        self.registry = set(registry)
        self.bridge_registry = set(bridge_registry) if bridge_registry is not None else None
        self.cache = {}
        self.stock_names = defaultdict(list)
        # Other stock scenarios' overrides are not candidates for the global file.
        for p in sorted(stock.iterdir()):
            if p.is_file() and not p.is_symlink() and p.suffix.casefold() == ".xml":
                self.stock_names[p.name.casefold()].append(p)

    def document(self, path):
        if path not in self.cache:
            try:
                tree, digest = read_xml(path)
                self.cache[path] = (tree, digest, None)
            except (OSError, ValueError, ET.ParseError) as exc:
                self.cache[path] = (None, None, str(exc))
        return self.cache[path]

    def scan(self, root, scenarios):
        files = xml_files(root)
        index = defaultdict(list)
        findings = []
        dependencies = {}

        def add(code, **detail):
            findings.append({"code": code, **detail})

        for path in files:
            index[path.name.casefold()].append(path)
            tree, digest, error = self.document(path)
            relative = str(path.relative_to(root))
            dependencies[relative] = digest
            if error:
                add("xml_unreadable", file=relative, error=error)
            if tree is not None:
                for node in tree.iter():
                    if not list(node) and (node.text or "").strip().lower() in (
                            "nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"):
                        add("nonfinite_literal_review", file=relative, field=node.tag, value=node.text)
        for name, paths in index.items():
            if len(paths) > 1:
                add("duplicate_xml_basename", name=name,
                    files=[str(p.relative_to(root)) for p in paths])

        def resolve(name, scenario, **context):
            basename = name.replace("\\", "/").rsplit("/", 1)[-1].casefold()
            candidates = index.get(basename, [])
            # Deliberately do not guess engine priority among duplicate names.
            source = "unique loose map candidate"
            if not candidates:
                candidates = self.stock_names.get(basename, [])
                source = "unique loose stock candidate"
            if len(candidates) != 1:
                compact = "".join(basename.split())
                near = [str(p.relative_to(root)) for n, paths in index.items()
                        if "".join(n.split()) == compact for p in paths]
                add("xml_reference_unresolved", scenario=scenario, reference=name,
                    candidates=[str(p) for p in candidates], whitespace_candidates=near,
                    **context)
                return None
            p = candidates[0]
            tree, digest, error = self.document(p)
            dependencies[str(p)] = digest
            if error:
                add("xml_dependency_unreadable", scenario=scenario, reference=name,
                    error=error, **context)
            return {"path": str(p), "sha256": digest, "resolution": source, "tree": tree}

        scenario_rows = []
        for relative in scenarios:
            path = root / relative
            if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
                raise AuditError("Scenario escapes map root")
            tree, digest, error = self.document(path)
            row = {"scenario": relative, "sha256": digest, "resources": {}}
            scenario_rows.append(row)
            if error:
                add("scenario_unreadable", scenario=relative, error=error)
                continue
            if tree.tag != "RRTScenario":
                add("unexpected_scenario_root", scenario=relative, root=tree.tag)
                continue
            occurrences = root_reference_occurrences(tree)
            row["reference_fields"] = occurrences
            grouped = defaultdict(list)
            for occurrence in occurrences:
                grouped[occurrence["field"]].append(occurrence)
            selected = {}
            selections = row["reference_selection"] = {}
            for tag, items in grouped.items():
                ambiguous = len(items) > 1
                status = "UNKNOWN" if ambiguous else "unique"
                context = ({"field": tag, "selection": "UNKNOWN", "documentary_only": True,
                            "meaning": "Loose candidate observation; native duplicate consumption is unknown"}
                           if ambiguous else {})
                if ambiguous:
                    add("duplicate_root_xml_reference", scenario=relative, field=tag,
                        count=len(items), occurrences=items, selection="UNKNOWN",
                        meaning="Native duplicate consumption is unknown; dependent projections withheld")
                bindings = []
                for item in items:
                    text = item["text"]
                    doc = None
                    if text and text.strip():
                        if text != text.strip():
                            add("xml_reference_whitespace_review", scenario=relative,
                                field=tag, reference=text,
                                **{k: v for k, v in context.items() if k != "field"})
                        doc = resolve(text.strip(), relative, **context)
                    bindings.append({**item, "binding": {k: v for k, v in doc.items()
                                                          if k != "tree"} if doc else None})
                    if not ambiguous:
                        selected[tag] = doc
                selections[tag] = {"status": status, "documentary_candidates": bindings,
                                   "policy": "Unique loose candidate binding is not native provider/selection proof"}
            records = {}
            for kind, (tag, default, selector) in KINDS.items():
                base_doc = resolve(default, relative)
                if len(grouped.get(tag, [])) > 1:
                    row["resources"][kind] = {
                        "base": {k: v for k, v in base_doc.items() if k != "tree"} if base_doc else None,
                        "scenario": None, "unresolved": True, "selection": "UNKNOWN",
                        "reason": "Duplicate root field; native consumption unknown; no override chosen"}
                    records[kind] = None
                    continue
                ref = (grouped[tag][0]["text"] or "").strip() if tag in grouped else ""
                override_doc = selected.get(tag) if ref and ref.casefold() != default.casefold() else None
                expected_root = {"goods": "RRTGoods", "industries": "RRTIndustries",
                                 "cars": "RRTTrainCars", "tenders": "RRTTrainCars",
                                 "depots": "RRTDepots", "bridges": "RRTBridges"}[kind]
                wrong_root = False
                for doc in (base_doc, override_doc):
                    if doc and doc["tree"] is not None and doc["tree"].tag != expected_root:
                        wrong_root = True
                        add("unexpected_dependency_root", scenario=relative, kind=kind,
                            file=doc["path"], expected=expected_root, actual=doc["tree"].tag)
                unresolved = (base_doc is None or base_doc["tree"] is None or
                              wrong_root or
                              (ref and ref.casefold() != default.casefold() and
                               (override_doc is None or override_doc["tree"] is None)))
                row["resources"][kind] = {"base": {k: v for k, v in base_doc.items() if k != "tree"} if base_doc else None,
                                           "scenario": {k: v for k, v in override_doc.items() if k != "tree"} if override_doc else None,
                                           "unresolved": bool(unresolved)}
                if unresolved:
                    records[kind] = None
                    continue
                base = base_doc["tree"].findall(selector)
                other = override_doc["tree"].findall(selector) if override_doc else []
                ambiguous_keys = False
                for label, items in (("base", base), ("scenario", other)):
                    counts = Counter(value(n, "szName") for n in items)
                    for name, count in counts.items():
                        if not name or count > 1:
                            ambiguous_keys = True
                            add("definition_key_review", scenario=relative, kind=kind,
                                source=label, name=name, count=count)
                if ambiguous_keys:
                    records[kind] = None
                    row["resources"][kind]["unresolved"] = True
                    row["resources"][kind]["reason"] = "Duplicate or empty keys; same-file fieldwise merge not simulated"
                    continue
                if kind == "goods":
                    records[kind] = goods_registry(base, other)
                    for name in sorted({value(n, "szName") for n in other} - {value(n, "szName") for n in base}):
                        add("scenario_good_not_in_base_registry", scenario=relative, name=name)
                else:
                    records[kind] = merge_records(base, other)
            industries, goods, cars, depots = (records[k] for k in ("industries", "goods", "cars", "depots"))
            if depots is not None and "Terminal" not in depots:
                add("standard_terminal_not_defined", scenario=relative)
            if industries is not None:
                names = set(industries)
                unsupported = names - self.registry
                row["industry_capacity"] = {"definitions": len(names), "unsupported": len(unsupported),
                                             "unused_table_names": len(self.registry - names),
                                             "injective_mapping_possible_by_count_only": len(names) <= len(self.registry)}
                if len(names) > len(self.registry):
                    add("industry_alias_capacity_exceeded", scenario=relative, count=len(names), capacity=len(self.registry))
                for name, pair in industries.items():
                    locations = sequence(pair, "./Locations/Location")
                    if name in unsupported:
                        add("industry_outside_registry", scenario=relative, name=name,
                            placements=len(locations), model=field(pair, "szModel"))
                    for resource in sequence(pair, "./Production/Resource"):
                        for tag in ("Input", "Output"):
                            good = value(resource, tag)
                            if not good:
                                add("empty_production_reference_review", scenario=relative, industry=name, field=tag)
                            elif good.casefold() != "none" and goods is not None and good not in goods:
                                add("production_good_not_registered", scenario=relative, industry=name, field=tag, good=good)
                    # New industry constructor defaults bInCity to false.
                    city_flag = engine_bool(field(pair, "bInCity"))
                    if not city_flag and depots is not None:
                        annex = name if " Annex" in name else name + " Annex"
                        if annex not in depots:
                            add("rural_annex_not_defined", scenario=relative, industry=name, annex=annex)
            if goods is not None and cars is not None:
                by_good = defaultdict(list)
                for name, pair in cars.items():
                    good = field(pair, "szGood")
                    by_good[good.casefold()].append(name)
                    if good and good not in goods:
                        case_matches = sorted(g for g in goods if g.casefold() == good.casefold())
                        add("car_good_case_review" if case_matches else "car_good_not_registered",
                            scenario=relative, car=name, good=good, casefold_matches=case_matches)
                for good, names in by_good.items():
                    if len(names) > 1:
                        add("duplicate_car_good_review", scenario=relative, good=good, cars=names)
                for good in sorted(goods):
                    if good.casefold() not in by_good:
                        add("registered_good_without_car", scenario=relative, good=good)
            # Numeric bridge anomalies are review leads, not established engine limits.
            bridges = records["bridges"]
            if bridges is not None:
                stock_tree, _, _ = self.document(self.stock / "rrt_bridges.xml")
                stock_bridges = {value(n, "szName"): n for n in stock_tree.findall("./Bridge")} if stock_tree is not None else {}
                for name, pair in bridges.items():
                    if self.bridge_registry is not None and name not in self.bridge_registry:
                        add("bridge_outside_registry", scenario=relative, bridge=name)
                        continue
                    for lower, upper in (("nDepthStart", "nDepthEnd"), ("nSpanStart", "nSpanEnd"), ("nAngleStart", "nAngleEnd")):
                        try:
                            lo, hi = float(field(pair, lower)), float(field(pair, upper))
                        except ValueError:
                            continue
                        if lo > hi:
                            add("bridge_reversed_range", scenario=relative, bridge=name, lower=lower, upper=upper)
                    stock_node = stock_bridges.get(name)
                    if stock_node is not None:
                        try:
                            custom, stock_value = float(field(pair, "nSpanEnd")), float(value(stock_node, "nSpanEnd"))
                            if stock_value > 0 and custom >= stock_value * 10:
                                add("bridge_span_outlier_review", scenario=relative, bridge=name, value=custom, stock_value=stock_value)
                        except ValueError:
                            pass
            if goods is not None:
                for node in tree.findall(".//GoodsListItem/szGood"):
                    if node.text and node.text not in goods:
                        add("goal_good_reference_review", scenario=relative, good=node.text,
                            casefold_match=any(node.text.casefold() == g.casefold() for g in goods))
        # No FPK decoding: its presence explicitly reduces resource coverage.
        packed = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.suffix.casefold() == ".fpk")
        if packed:
            add("packed_assets_uninspected", files=packed)
        # Avoid repeated findings from shared default documents.
        unique_findings = list({json.dumps(f, sort_keys=True): f for f in findings}.values())
        return {"scenarios": scenario_rows, "xml_files": len(files), "xml_hashes": dependencies,
                "packed_files": len(packed), "findings": unique_findings,
                "categories": sorted({f["code"] for f in unique_findings})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-library", required=True, action="store_true")
    args = parser.parse_args()
    start = time.monotonic()
    binary, library, stock = discover_installed_library()
    binary_data = binary.read_bytes()
    registry = extract_registry(binary_data)
    bridge_registry = extract_registry(binary_data, address=0x1018CFE00, count=8)
    scanner = Scanner(stock, registry, bridge_registry)
    catalogue = json.loads((library / "catalogue.json").read_text())
    grouped = defaultdict(list)
    for row in catalogue["maps"]:
        grouped[row["imported_directory"]].append(row)
    maps = []
    for directory, entries in sorted(grouped.items()):
        if Path(directory).name != directory or directory in (".", ".."):
            raise AuditError("Invalid import directory")
        scenarios = sorted({s for entry in entries for s in entry.get("scenarios", [])})
        result = scanner.scan(library / "imports" / directory, scenarios)
        result.update(name=entries[0]["name"], directory=directory,
                      archive_sha256=entries[0]["archive_sha256"],
                      variant_ids=[e["variant_id"] for e in entries])
        maps.append(result)
    counts = Counter(code for row in maps for code in row["categories"])
    report = {"schema": 1, "analyzer_version": ANALYZER_VERSION,
              "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "xml_scanner_sha256": hashlib.sha256(
                  Path(__file__).with_name("check_industry_registry.py").read_bytes()).hexdigest(),
              "registry_reader_sha256": hashlib.sha256(
                  (Path(__file__).resolve().parents[2] / "src/smr_launcher/industry_registry.py").read_bytes()).hexdigest(),
              "created_at": datetime.now(timezone.utc).isoformat(),
              "game_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "elapsed_seconds": round(time.monotonic() - start, 3),
              "summary": {"unique_imports": len(maps), "catalogue_variants": len(catalogue["maps"]),
                          "scenarios": sum(len(r["scenarios"]) for r in maps),
                          "xml_files": sum(r["xml_files"] for r in maps),
                          "maps_by_finding": dict(sorted(counts.items()))},
              "limitations": [
                  "Read-only static research; no runtime pass or repair is implied.",
                  "Loose map basename then stock is a candidate resolver; engine VFS priority is not established.",
                  "Duplicate filenames are unresolved; packed FPK contents are not indexed.",
                  "Duplicate root XMLFile fields retain ordered occurrences; native selection is UNKNOWN and dependent projections are withheld.",
                  "Exact identity comparison except case-insensitive car goods lookup; engine normalization is incompletely mapped.",
                  "Scalar empty-value semantics and bridge overlay semantics are provisional; outliers are review leads.",
                  "Models, animation graphs, terrain, runtime-generated objects and saved-game references are not validated.",
              ], "maps": maps}
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("Map audit failed: " + str(error), file=sys.stderr)
        raise SystemExit(2)
