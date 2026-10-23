"""Exact-input map observations with qualified evidence and explicit coverage.

This module does not import launcher application services, activate content,
repair files or infer runtime acceptance from static observations.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import os
import re
import stat
import struct
import uuid
from xml.etree.ElementTree import TreeBuilder
from xml.parsers import expat

from . import inspection_io as acquisition
from .industry_registry import IndustryRegistryError, extract_supported_industry_registry
from .model_checks import HEADER_BYTES, nif_header

MAX_FILE_BYTES = 512 * 1024**2
MAX_XML_BYTES = 8 * 1024**2
MAX_XML_TOTAL_BYTES = 128 * 1024**2
MAX_CANDIDATE_EDGES = 100000
MAX_REPORT_RECORDS = 100000
GAME_UUID = "F07E6445-A31F-35B5-B2E5-7C1943E4947F"
GAME_SHA256 = "981ca7180759d8578babf54cd9a4f76b93db503b33a7f1938c6b1427b95680f0"
VERSION = "1.1.0"
DEFAULTS = {
    "IndustriesXMLFile": "RRT_Industries.xml",
    "CityXMLFile": "RRT_Cities.xml",
    "EventsXMLFile": "RRT_Events.xml",
}
EVIDENCE = {
    "F002": (
        ["100edfe70", "10025c050", "10025c1c0"],
        "Native parser differs from the inspection parser profile; equivalence unresolved.",
    ),
    "ICF04": (
        ["100ed12d0", "100ed4a80"],
        "Provider traversal and conditional catalog replacement; candidate lookup is not native resolution.",
    ),
    "ICF13": (
        ["1018ce910", "100d890a0", "100d89c00"],
        "Authored independent findings and one-key native control; exact map runtime eligibility remains conditional.",
    ),
    "ICF14": (
        ["100e19680", "100d37a00", "100e17eb5"],
        "City-key lookup and owner assignment; native fixture fault and corrected loading control.",
    ),
    "ICF11": (
        ["100defdc0", "100def330", "100defab0"],
        "Conditional effect-vector cleanup/append and execute/save consumers; retained state required.",
    ),
    "ICF02": (
        ["100fe4600", "100fe485a", "100f8bfa0"],
        "Legacy collision construction depends on block state; a model header alone is insufficient.",
    ),
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def analyzer_identity():
    sources = (
        "map_inspection.py",
        "inspection_io.py",
        "map_inspect.py",
        "industry_registry.py",
        "model_checks.py",
        "resource_identity.py",
        "packages.py",
        "extraction.py",
    )
    source_hash = acquisition.content_digest(
        {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in sources
        }
    )
    return dict(version=VERSION, source_sha256=source_hash)


def empty_report():
    return dict(
        schema_version=1,
        analyzer=analyzer_identity(),
        status="refused",
        error=None,
        input=None,
        game=None,
        scenarios=[],
        findings=[],
        coverage=[],
        resource_resolution=dict(
            profile="ascii_loose_basename_candidates_v1",
            native_selection="unproven",
            explicit_reference_counts={},
            limits=[
                "Counts describe explicit scenario fields, not missing-resource defects.",
                "Defaults, packed providers, merge behavior and provider precedence remain separate questions.",
            ],
        ),
        provenance=dict(
            research=[
                dict(
                    finding_id=key,
                    module="main-x86_64",
                    build_sha256=GAME_SHA256,
                    uuid=GAME_UUID,
                    static_addresses=value[0],
                    basis=value[1],
                )
                for key, value in EVIDENCE.items()
            ],
            local_evidence_status="not_bound",
            limits=[
                "Authored evidence references, not a live corpus verification or semantic-proof certificate.",
                "Native rules apply only to the exact supported build; unresolved preconditions remain visible.",
            ],
        ),
        preservation=dict(input_unchanged=None, game_unchanged=None, stock_unchanged=None),
        observation=dict(
            started_utc=_now(), finished_utc=None, archive_snapshot=None, archive=None
        ),
        runtime_acceptance="not_tested",
    )


def report_text(report):
    """Shared plain-text presentation for CLI and launcher import checks."""
    lines = [
        f"Inspection: {report['status']}; runtime_acceptance: {report['runtime_acceptance']}",
        "Static inspection does not establish compatibility, a verified repair or gameplay acceptance.",
    ]
    if report.get("checked_at"):
        lines.append("Checked: " + report["checked_at"])
    if report.get("input"):
        lines.extend(
            [
                "Input: " + report["input"]["location"],
                "Input identity: " + report["input"]["content_identity"],
            ]
        )
    if report.get("game"):
        lines.append("Build: " + report["game"]["build_status"])
        if report["game"]["executable"]:
            executable = report["game"]["executable"]
            lines.append(
                "Executable: " + executable["sha256"] + "; UUID: " + str(executable["uuid"])
            )
    if report.get("error"):
        lines.append(report["error"]["type"] + ": " + report["error"]["message"])
    if report["observation"].get("archive_snapshot"):
        lines.append("Archive snapshot retained: " + report["observation"]["archive_snapshot"])
    reasons = report.get("resource_resolution", {}).get("explicit_reference_counts", {})
    if reasons:
        lines.append(
            "Explicit resource references (native selection unproven): "
            + ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
        )
    for finding in report["findings"]:
        where = finding["location"]
        lines.append(
            f"[{finding['category']}] {where['scope']}:{where['path']} {where.get('locator') or ''}: {finding['summary']}"
        )
        lines.append(
            "Rule: "
            + finding["rule_id"]
            + "; evidence: "
            + (", ".join(finding["evidence_ids"]) or "mechanical/profile observation")
        )
        if finding["observed"].get("reason"):
            lines.append("Reason: " + finding["observed"]["reason"])
        if finding["observed"].get("parser"):
            diagnostic = finding["observed"]["parser"]
            lines.append(
                "Parser: "
                + diagnostic["reason"]
                + "; line="
                + str(diagnostic.get("line"))
                + "; column="
                + str(diagnostic.get("column"))
                + "; engine acceptance unresolved"
            )
        lines.extend("Precondition: " + value for value in finding["preconditions"])
        lines.extend("Limit: " + value for value in finding["limits"])
    lines.append(
        "Coverage: " + ", ".join(f"{row['domain']}={row['status']}" for row in report["coverage"])
    )
    lines.append(
        "Evidence binding: "
        + report["provenance"]["local_evidence_status"]
        + "; authored build/module/address references are in the structured report."
    )
    if report.get("input_scope"):
        lines.append("Input scope: " + report["input_scope"])
    return "\n".join(lines)


class XMLProfileError(ValueError):
    """A supported inspection boundary, not an engine-invalid verdict."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def xml_diagnostic(exc, data):
    """Describe parser refusal without accepting rewritten/decoded fallback input."""
    result = dict(reason="xml_syntax_profile", engine_acceptance="unresolved")
    if isinstance(exc, XMLProfileError):
        result["reason"] = exc.reason
        return result
    if isinstance(exc, expat.ExpatError):
        result.update(code=exc.code, line=exc.lineno, column=exc.offset)
        if exc.code == expat.errors.codes[expat.errors.XML_ERROR_NO_ELEMENTS]:
            result["reason"] = "incomplete_or_empty_document"
    elif isinstance(exc, LookupError):
        result["reason"] = "encoding_profile"
    if data is None:
        return result
    declared = re.search(rb'<\?xml\b[^>]*\bencoding\s*=\s*[\'"]([^\'"]+)[\'"]', data[:512], re.I)
    encoding = declared.group(1).decode("ascii", "replace") if declared else None
    result["declared_encoding"] = encoding
    # Only classify UTF-8 bytes when the declaration/default is unambiguous.
    # A failed decode supplies evidence; it never becomes replacement parser input.
    wide_signature = data.startswith((b"\xff\xfe", b"\xfe\xff", b"\0\0\xfe\xff", b"\0<", b"<\0"))
    if not wide_signature and (encoding is None or encoding.lower() in ("utf-8", "utf8")):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            result.update(
                reason="invalid_utf8_bytes",
                invalid_byte_offset=error.start,
                invalid_byte_line=data[: error.start].count(b"\n") + 1,
            )
    return result


def parse_document(data):
    """Ordered parsed character data, not lossless lexical XML reconstruction."""
    if len(data) > MAX_XML_BYTES:
        raise XMLProfileError("document_byte_budget", "XML exceeds the inspection byte budget")
    parser = expat.ParserCreate()
    builder = TreeBuilder()
    count = depth = 0

    def start(name, attrs):
        nonlocal count, depth
        count += 1
        depth += 1
        if count > 100000 or depth > 64:
            raise XMLProfileError(
                "node_depth_budget", "XML exceeds the inspection node/depth budget"
            )
        if ":" in name or any(key == "xmlns" or key.startswith("xmlns:") for key in attrs):
            raise XMLProfileError(
                "namespace_profile", "XML namespace handling is outside this inspection profile"
            )
        builder.start(name, attrs)

    def end(name):
        nonlocal depth
        builder.end(name)
        depth -= 1

    def reject(*args):
        raise XMLProfileError(
            "declaration_profile", "DTD/entity declarations are outside this inspection profile"
        )

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.Parse(data, True)
    return builder.close()


def _text(node, field):
    children = node.findall(field)
    return children[0].text if len(children) == 1 else None


def _mach_identity(data):
    architecture, image_uuid = "unknown", None
    if len(data) < 32 or data[:4] != b"\xcf\xfa\xed\xfe":
        return architecture, image_uuid
    cpu = struct.unpack_from("<I", data, 4)[0]
    architecture = {0x01000007: "x86_64", 0x0100000C: "arm64"}.get(cpu, hex(cpu))
    count, size = struct.unpack_from("<II", data, 16)
    if size > len(data) - 32 or count > size // 8:
        return architecture, image_uuid
    position = 32
    for _ in range(count):
        if position + 8 > 32 + size:
            break
        command, length = struct.unpack_from("<II", data, position)
        if length < 8 or position + length > 32 + size:
            break
        if command == 0x1B and length == 24:
            image_uuid = str(uuid.UUID(bytes=data[position + 8 : position + 24])).upper()
        position += length
    return architecture, image_uuid


class _Inspection:
    def __init__(self, report, source, stock, registry):
        self.report, self.source, self.stock, self.registry = report, source, stock, registry
        self.snapshots = {"map": source}
        if stock is not None:
            self.snapshots["stock"] = stock
        self.index = defaultdict(list)
        self.documents = {}
        self.xml_bytes = self.queries = self.candidate_edges = self.records = 0
        non_ascii_names = 0
        for scope, snapshot in self.snapshots.items():
            for row in snapshot.files:
                name = Path(row["path"]).name
                if name.isascii():
                    self.index[name.lower()].append(dict(scope=scope, **row))
                else:
                    non_ascii_names += 1
        if non_ascii_names:
            self.add(
                "resource_name_profile",
                "unimplemented_check",
                "Non-ASCII resource basename comparison is outside this profile",
                observed=dict(files=non_ascii_names),
                evidence=["ICF04"],
            )

    def charge_records(self, count=1):
        self.records += count
        if self.records > MAX_REPORT_RECORDS:
            raise acquisition.InspectionError("Inspection report record budget exceeded")

    def add(
        self,
        rule,
        category,
        summary,
        *,
        scope="map",
        path=".",
        locator=None,
        observed=None,
        evidence=(),
        preconditions=(),
        limits=(),
    ):
        self.charge_records()
        snapshot = self.snapshots.get(scope)
        source = snapshot.by_path.get(path, {}) if snapshot else {}
        row = dict(
            rule_id=rule,
            revision=1,
            category=category,
            severity="warning" if category == "compatibility_risk" else "info",
            summary=summary,
            location=dict(scope=scope, path=path, locator=locator, sha256=source.get("sha256")),
            observed=observed or {},
            evidence_ids=list(evidence),
            preconditions=list(preconditions),
            limits=list(limits),
        )
        row["id"] = acquisition.content_digest(row)
        self.report["findings"].append(row)

    def candidates(self, name):
        self.queries += 1
        if self.queries > 10000:
            raise acquisition.InspectionError("Resource reference query budget exceeded")
        if not name or not name.isascii() or name != name.strip():
            return []
        basename = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        matches = self.index.get(basename, [])
        self.candidate_edges += len(matches)
        if self.candidate_edges > MAX_CANDIDATE_EDGES:
            raise acquisition.InspectionError("Resource candidate edge budget exceeded")
        return [{key: row[key] for key in ("scope", "path", "sha256")} for row in matches]

    def document(self, candidate):
        key = candidate["scope"], candidate["path"]
        if key not in self.documents:
            snapshot = self.snapshots[key[0]]
            row = snapshot.by_path[key[1]]
            self.xml_bytes += row["size"]
            if self.xml_bytes > MAX_XML_TOTAL_BYTES:
                raise acquisition.InspectionError("Total analyzed XML byte budget exceeded")
            # Acquisition failures must propagate as operational refusals, not
            # be mistaken for a parser-profile limitation.
            data = snapshot.read(key[1], MAX_XML_BYTES) if row["size"] <= MAX_XML_BYTES else None
            try:
                if data is None:
                    raise XMLProfileError(
                        "document_byte_budget", "XML exceeds the inspection document byte budget"
                    )
                self.documents[key] = parse_document(data)
            except (ValueError, expat.ExpatError, LookupError) as exc:
                self.documents[key] = None
                self.add(
                    "xml_parser",
                    "unimplemented_check",
                    "Inspection XML parser cannot assess this document",
                    scope=key[0],
                    path=key[1],
                    observed=dict(error=str(exc), parser=xml_diagnostic(exc, data)),
                    evidence=["F002"],
                    limits=["Tool refusal is not an engine-invalid verdict."],
                )
        return self.documents[key]

    def unique_document(self, name):
        candidates = self.candidates(name)
        if len(candidates) != 1:
            return None, None
        return candidates[0], self.document(candidates[0])

    def scenario(self, row):
        self.charge_records()
        path = row["path"]
        root = self.document(dict(scope="map", path=path))
        result = dict(
            path=path,
            sha256=row["sha256"],
            parse_status="parsed" if root is not None else "unsupported",
            reference_fields=[],
            observations={},
        )
        self.report["scenarios"].append(result)
        if root is None:
            return
        if root.tag != "RRTScenario":
            result["parse_status"] = "unsupported"
            self.add(
                "scenario_root",
                "unimplemented_check",
                "Scenario root is outside the supported inspection profile",
                path=path,
                observed=dict(root=root.tag),
            )
            return
        fields = [(i, child) for i, child in enumerate(root) if child.tag.endswith("XMLFile")]
        counts = Counter(child.tag for _, child in fields)
        bindings = {}
        for index, child in fields:
            self.charge_records()
            text = child.text
            candidates = self.candidates(text)
            selection = (
                "empty"
                if not text
                else "missing"
                if not candidates
                else "unique_candidate"
                if len(candidates) == 1
                else "unresolved"
            )
            if counts[child.tag] > 1 or (
                text is not None and (text != text.strip() or not text.isascii())
            ):
                selection = "unresolved"
            reason = (
                "duplicate_field"
                if counts[child.tag] > 1
                else "empty_reference"
                if not text
                else "unsupported_reference_text"
                if text != text.strip() or not text.isascii()
                else "ambiguous_loose_candidates"
                if len(candidates) > 1
                else "no_loose_candidate"
                if not candidates
                else "unique_loose_candidate"
            )
            reasons = self.report["resource_resolution"]["explicit_reference_counts"]
            reasons[reason] = reasons.get(reason, 0) + 1
            reference = dict(
                field=child.tag,
                occurrence=index,
                text=text,
                locator=f"/{index}",
                candidates=candidates,
                selection=selection,
                reason=reason,
                native_selection="unproven",
            )
            result["reference_fields"].append(reference)
            if selection == "unique_candidate":
                bindings[child.tag] = (candidates[0], self.document(candidates[0]))
            else:
                bindings[child.tag] = (None, None)
                self.add(
                    "resource_candidates",
                    "missing_input",
                    "Resource selection is unresolved within loose candidate coverage",
                    path=path,
                    locator=reference["locator"],
                    observed=reference,
                    evidence=["ICF04"],
                    limits=[
                        "All matching map and stock candidates are retained; native selection is not emulated."
                    ],
                )
        for tag, default in DEFAULTS.items():
            if tag not in bindings:
                bindings[tag] = self.unique_document(default)
                if bindings[tag][0] is None:
                    self.add(
                        "default_resource_candidates",
                        "missing_input",
                        "Default resource has no unique loose candidate",
                        path=path,
                        observed=dict(field=tag, reference=default),
                        evidence=["ICF04"],
                    )
        if self.registry is None:
            return
        industry, industries = bindings["IndustriesXMLFile"]
        _, cities = bindings["CityXMLFile"]
        if industry and industries is not None and industries.tag == "RRTIndustries":
            self.industries(result, industry, industries, cities)
        selected, events = bindings["EventsXMLFile"]
        base, base_events = self.unique_document("RRT_Events.xml")
        if base and base_events is not None:
            self.events(result, base, base_events, selected, events)

    def industries(self, scenario, candidate, root, cities):
        rows = []
        city_names = (
            [[n.text for n in city.findall("szName")] for city in cities.iter("City")]
            if cities is not None and cities.tag == "RRTCities"
            else None
        )
        city_keys = (
            [names[0] for names in city_names if len(names) == 1]
            if city_names is not None
            else None
        )
        uncertain_city_keys = city_names is not None and any(
            len(names) != 1 or not names[0] for names in city_names
        )
        for index, node in enumerate(root.findall("./Industries/RRTIndustry")):
            names = [n.text for n in node.findall("szName")]
            key = names[0] if len(names) == 1 else None
            placements = [
                dict(
                    in_city=[c.text for c in n.findall("InCity")],
                    locator=f"/Industries/{index}/Locations/{i}",
                )
                for i, n in enumerate(node.findall("./Locations/Location"))
            ]
            self.charge_records(1 + len(placements))
            row = dict(
                occurrence=index,
                keys=names,
                city_flags=[n.text for n in node.findall("bInCity")],
                models=[n.text for n in node.findall("szModel")],
                placements=placements,
            )
            rows.append(row)
            if not key:
                self.add(
                    "industry_definition_key",
                    "unimplemented_check",
                    "Industry definition has an empty or duplicate key occurrence",
                    scope=candidate["scope"],
                    path=candidate["path"],
                    locator=f"/Industries/{index}",
                    observed=dict(keys=names),
                    limits=["Native duplicate-key and missing-key behavior is not simulated."],
                )
            if key and key not in self.registry:
                self.add(
                    "industry_registry",
                    "compatibility_risk" if placements else "heuristic_lead",
                    "Placed industry key is outside the supported registry"
                    if placements
                    else "Unused industry definition key is outside the supported registry",
                    scope=candidate["scope"],
                    path=candidate["path"],
                    locator=f"/Industries/{index}",
                    observed=dict(scenario=scenario["path"], **row),
                    evidence=["ICF13"],
                    preconditions=[
                        "Definition survives actual merge/pruning and resolves to this candidate.",
                        "Placed actor is industry-kind1 and has the animation entity pointer required by grouping.",
                    ],
                    limits=[
                        "No exact runtime ordinal, model construction, guaranteed crash, alias or save-equivalence claim."
                    ],
                )
            if row["city_flags"] != ["1"]:
                continue
            for placement in placements:
                values = placement["in_city"]
                if len(values) != 1:
                    continue
                city = values[0]
                if city and uncertain_city_keys:
                    self.charge_records(sum(len(names) + 1 for names in city_names))
                    self.add(
                        "city_definition_keys",
                        "unimplemented_check",
                        "City identities contain missing, empty or duplicate key occurrences",
                        scope=candidate["scope"],
                        path=candidate["path"],
                        locator=placement["locator"],
                        observed=dict(in_city=city, candidate_city_key_occurrences=city_names),
                        evidence=["ICF14"],
                        limits=[
                            "Unmatched ownership is withheld because native key selection is unresolved."
                        ],
                    )
                    continue
                if (city and not city.isascii()) or (
                    city_keys and any(n and not n.isascii() for n in city_keys)
                ):
                    self.add(
                        "city_comparison",
                        "unimplemented_check",
                        "City comparison contains non-ASCII text; locale behavior is unresolved",
                        scope=candidate["scope"],
                        path=candidate["path"],
                        locator=placement["locator"],
                        evidence=["ICF14"],
                    )
                    continue
                if not city or (
                    city_keys is not None
                    and city.lower() not in [n.lower() for n in city_keys if n]
                ):
                    self.charge_records(len(city_keys or []))
                    self.add(
                        "city_owner",
                        "compatibility_risk",
                        "City-owned placement has an empty or unmatched owning-city candidate",
                        scope=candidate["scope"],
                        path=candidate["path"],
                        locator=placement["locator"],
                        observed=dict(industry=key, in_city=city, candidate_city_keys=city_keys),
                        evidence=["ICF14"],
                        preconditions=[
                            "Explicit city flag1 remains effective after native definition loading.",
                            "The inspected city definitions and placement are the live selected inputs.",
                        ],
                        limits=[
                            "ASCII city-key comparison only; nearest city is not owning city.",
                            "Inherited flags, pruning, provider selection and runtime ownership are not proved.",
                        ],
                    )
        scenario["observations"]["industry_definitions"] = rows

    def events(self, scenario, base, base_root, selected, selected_root):
        if base_root.tag != "RRTEvents":
            return
        vectors = defaultdict(int)
        observations = []
        stages = [(base, base_root)]
        if (
            selected
            and selected_root is not None
            and (selected["scope"], selected["path"]) != (base["scope"], base["path"])
            and selected_root.tag == "RRTEvents"
        ):
            stages.append((selected, selected_root))
        for candidate, root in stages:
            for index, event in enumerate(root.findall("Event")):
                title = _text(event, "szTitle")
                containers = event.findall("Effects")
                children = containers[0].findall("EventEffect") if len(containers) == 1 else []
                kinds = [_text(child, "szType") for child in children]
                self.charge_records(1 + len(kinds))
                recognized = sum(kind in ("Stock", "Good") for kind in kinds)
                observations.append(
                    dict(
                        scope=candidate["scope"],
                        path=candidate["path"],
                        occurrence=index,
                        title=title,
                        sha256=candidate["sha256"],
                        titles=[n.text for n in event.findall("szTitle")],
                        effects_containers=len(containers),
                        event_effects=len(children),
                        types=kinds,
                    )
                )
                if len(containers) > 1 or len(event.findall("szTitle")) != 1:
                    self.add(
                        "event_duplicate_fields",
                        "unimplemented_check",
                        "Duplicate or missing event fields require native selector evidence",
                        scope=candidate["scope"],
                        path=candidate["path"],
                        locator=f"/Event/{index}",
                        evidence=["ICF11"],
                    )
                if title is None:
                    continue
                if children and vectors[title]:
                    self.add(
                        "event_effect_replacement",
                        "compatibility_risk",
                        "EventEffect child revisits an event with a modeled nonempty effect range",
                        scope=candidate["scope"],
                        path=candidate["path"],
                        locator=f"/Event/{index}",
                        observed=dict(scenario=scenario["path"], title=title, selected_types=kinds),
                        evidence=["ICF11"],
                        preconditions=[
                            "Recognized Stock/Good construction and previous nonempty range match the actual native inputs.",
                            "The event remains retained after definition loading and no other path replaces its state.",
                        ],
                        limits=[
                            "Conditional static projection; no native vector capture or repair-equivalence claim.",
                            "A selected unknown effect type can enter cleanup without appending a replacement.",
                        ],
                    )
                vectors[title] += recognized
                if children and not recognized:
                    self.add(
                        "event_effect_type",
                        "unimplemented_check",
                        "Effect type is outside modeled Stock/Good construction",
                        scope=candidate["scope"],
                        path=candidate["path"],
                        locator=f"/Event/{index}",
                        observed=dict(types=kinds),
                        evidence=["ICF11"],
                        limits=[
                            "Unknown-only base types do not establish an inherited nonempty vector."
                        ],
                    )
        scenario["observations"]["event_definitions"] = observations

    def models(self):
        for row in self.source.files:
            if Path(row["path"]).suffix.lower() != ".nif":
                continue
            result = nif_header(self.source.read(row["path"], MAX_FILE_BYTES, keep=HEADER_BYTES))
            if self.registry is None:
                result.pop("legacy_collision_path", None)
                self.add(
                    "model_header",
                    "unimplemented_check",
                    "Mechanical model header observed; native collision assessment requires the supported build",
                    path=row["path"],
                    observed=result,
                    limits=[
                        "Header format/version only; no build-specific collision interpretation."
                    ],
                )
                continue
            if result["status"] != "recognized" or result.get("legacy_collision_path"):
                self.add(
                    "model_header",
                    "heuristic_lead",
                    "Model header requires scoped review; collision validity is not established",
                    path=row["path"],
                    observed=result,
                    evidence=["ICF02"],
                    limits=[
                        "Header only; block flags, bounds, construction, animation and native resource selection are unimplemented.",
                        "Old version alone does not establish invalidity or a crash.",
                    ],
                )


def input_binding(source, game, *, scopes=None):
    """Fresh mechanical admission for cached reports; no native selection claim."""
    analyzer = analyzer_identity()
    source = acquisition.absolute(source)
    game = acquisition.absolute(game) if game is not None else None
    snapshot = acquisition.inventory(source, scopes=scopes, **inventory_limits())
    executable = game / "Contents/MacOS/Sid Meiers Railroads" if game else None
    stock_path = game.parent / "SMRailroadsData/assets" if game else None
    executable_facts = None
    if executable is not None and os.path.lexists(executable):
        _, executable_facts = acquisition.read_file(executable, MAX_FILE_BYTES, keep=0)
    stock = (
        acquisition.inventory(stock_path, **inventory_limits())
        if stock_path is not None and os.path.lexists(stock_path)
        else None
    )
    # Recheck earlier captures after later dependencies were inventoried. This
    # fences ordinary concurrent changes, not an atomic multi-root snapshot.
    snapshot.verify()
    if executable_facts:
        acquisition.read_file(executable, MAX_FILE_BYTES, expected=executable_facts, keep=0)
    elif executable is not None and os.path.lexists(executable):
        raise acquisition.InspectionError("Game executable appeared during report admission")
    if stock is not None:
        stock.verify()
    elif stock_path is not None and os.path.lexists(stock_path):
        raise acquisition.InspectionError("Stock assets appeared during report admission")
    if analyzer_identity() != analyzer:
        raise acquisition.InspectionError("Analyzer changed during report admission")
    return dict(
        analyzer=analyzer,
        source=str(source),
        scopes=list(scopes) if scopes is not None else None,
        map_content=snapshot.identity,
        map_observation=acquisition.content_digest(snapshot.entries),
        game=str(game) if game is not None else None,
        executable=dict(
            size=executable_facts["size"],
            sha256=executable_facts["sha256"],
            observation=acquisition.content_digest(executable_facts),
        )
        if executable_facts
        else None,
        stock_content=stock.identity if stock else None,
        stock_observation=acquisition.content_digest(stock.entries) if stock else None,
    )


def inventory_limits():
    return dict(
        max_entries=100000, max_total_bytes=8 * 1024**3, max_file_bytes=MAX_FILE_BYTES, max_depth=64
    )


def inspect_map(source, game, *, scenario=None, workspace_parent=None, scopes=None):
    report = empty_report()
    try:
        return _inspect_map(
            report,
            source,
            game,
            scenario=scenario,
            workspace_parent=workspace_parent,
            scopes=scopes,
        )
    except Exception as exc:
        if getattr(exc, "archive_snapshot", None):
            report["observation"]["archive_snapshot"] = exc.archive_snapshot
        report["status"] = "refused"
        report["error"] = dict(type=type(exc).__name__, message=str(exc))
        report["observation"]["finished_utc"] = _now()
        raise acquisition.InspectionError(str(exc), report=report) from exc


def _inspect_map(report, source, game, *, scenario=None, workspace_parent=None, scopes=None):
    source = acquisition.absolute(source)
    game = acquisition.absolute(game) if game is not None else None
    limits = inventory_limits()
    mode = os.lstat(source).st_mode
    archive_facts = None
    snapshot_root = source
    if stat.S_ISREG(mode):
        if scopes is not None:
            raise acquisition.InspectionError("Scoped inspection requires an extracted directory")
        snapshot_root, archive_facts, workspace, archive = acquisition.archive_snapshot(
            source, [source, game.parent] if game else [source], workspace_parent
        )
        report["observation"]["archive_snapshot"] = str(workspace)
        report["observation"]["archive"] = archive
    elif not stat.S_ISDIR(mode):
        raise acquisition.InspectionError(
            "Map input must be a nonlinked directory or regular archive"
        )
    snapshot = acquisition.inventory(snapshot_root, scopes=scopes, **limits)
    report["input"] = dict(
        kind="archive" if archive_facts else "directory",
        location=str(source),
        content_identity=snapshot.identity,
        files=snapshot.files,
        source_sha256=archive_facts["sha256"] if archive_facts else None,
        scopes=list(scopes) if scopes is not None else None,
    )
    executable = game / "Contents/MacOS/Sid Meiers Railroads" if game else None
    game_facts = None
    registry = None
    report["game"] = dict(
        location=str(game) if game else None,
        executable=None,
        build_status="missing",
        stock_identity=None,
    )
    if executable is not None and os.path.lexists(executable):
        data, game_facts = acquisition.read_file(executable, MAX_FILE_BYTES)
        architecture, image_uuid = _mach_identity(data)
        report["game"]["executable"] = dict(
            path=str(executable),
            size=game_facts["size"],
            sha256=game_facts["sha256"],
            uuid=image_uuid,
            architecture=architecture,
        )
        report["game"]["build_status"] = "unsupported"
        try:
            names = extract_supported_industry_registry(data)
            if architecture == "x86_64" and image_uuid == GAME_UUID:
                registry = names
                report["game"]["build_status"] = "supported"
        except IndustryRegistryError:
            pass
        del data
    stock_path = game.parent / "SMRailroadsData/assets" if game else None
    stock = (
        acquisition.inventory(stock_path, **limits)
        if stock_path is not None and os.path.lexists(stock_path)
        else None
    )
    if stock is not None:
        report["game"]["stock_identity"] = stock.identity
    inspection = _Inspection(report, snapshot, stock, registry)
    if registry is None:
        inspection.add(
            "build_binding",
            "missing_input",
            "Native rules require the exact supported executable and UUID",
            observed=dict(build_status=report["game"]["build_status"]),
        )
    if stock is None:
        inspection.add(
            "stock_input",
            "missing_input",
            "Selected installation stock assets are unavailable",
            observed=dict(expected_path=str(stock_path) if stock_path else None),
        )
    scenarios = [
        row
        for row in snapshot.files
        if Path(row["path"]).name.lower().startswith("rrt_scenario")
        and Path(row["path"]).suffix.lower() == ".xml"
    ]
    if scenario is not None:
        scenarios = [row for row in scenarios if row["path"] == scenario]
        if not scenarios:
            raise acquisition.InspectionError(
                "Selected scenario is not an exact inventoried scenario path"
            )
    if len(scenarios) > 1000:
        raise acquisition.InspectionError("Scenario count exceeds inspection budget")
    if not scenarios:
        inspection.add(
            "scenario_input", "missing_input", "No filename scenario candidates were found"
        )
    for row in scenarios:
        inspection.scenario(row)
    inspection.models()
    report["coverage"] = [
        dict(
            domain="input_identity",
            status="checked",
            reason="Every supplied regular file hashed within explicit budgets; content and preservation identities are separate.",
        ),
        dict(
            domain="scenario_xml",
            status="partial",
            reason="Ordered untrimmed parsed character data; not lexical or native parser equivalence. Filename candidates do not prove engine discovery.",
        ),
        dict(
            domain="resource_selection",
            status="partial",
            reason="All loose basename candidates; native provider/FILETIME/full-path/packed selection unresolved.",
        ),
        dict(
            domain="industry_registry",
            status="partial" if registry is not None else "unsupported",
            reason="Direct definition/placement observations only; no general merge, animation eligibility or final ordinal model.",
        ),
        dict(
            domain="city_ownership",
            status="partial" if registry is not None else "unsupported",
            reason="Explicit city flag1 and ASCII city-key candidates only; inherited/default flags and live ownership unresolved.",
        ),
        dict(
            domain="event_vectors",
            status="partial" if registry is not None else "unsupported",
            reason="Modeled recognized effects and EventEffect cleanup leads; retained native vector state unverified.",
        ),
        dict(
            domain="model_headers",
            status="partial",
            reason="Map-local NIF headers only; native legacy-path leads withheld for unknown builds. Supported-build legacy flag is a heuristic, not collision-path proof.",
        ),
    ]
    for domain in (
        "packed_assets",
        "factory_merges",
        "terrain_textures_animation",
        "objective_evaluators",
        "event_scheduler",
        "save_references",
    ):
        report["coverage"].append(
            dict(
                domain=domain,
                status="not_implemented",
                reason="No adequate native-equivalent check in this CLI slice.",
            )
        )
        inspection.add(domain, "unimplemented_check", "Coverage not implemented: " + domain)
    snapshot.verify()
    if archive_facts:
        acquisition.read_file(source, 2 * 1024**3, expected=archive_facts, keep=0)
    if game_facts:
        acquisition.read_file(executable, MAX_FILE_BYTES, expected=game_facts, keep=0)
    elif executable is not None and os.path.lexists(executable):
        raise acquisition.InspectionError(
            "Previously missing game executable appeared during inspection"
        )
    if stock is not None:
        stock.verify()
    elif stock_path is not None and os.path.lexists(stock_path):
        raise acquisition.InspectionError(
            "Previously missing stock assets appeared during inspection"
        )
    report["preservation"] = dict(
        input_unchanged=True,
        game_unchanged=True if game is not None else None,
        stock_unchanged=True if stock is not None else None,
    )
    report["status"] = "completed"
    report["observation"]["finished_utc"] = _now()
    return report
