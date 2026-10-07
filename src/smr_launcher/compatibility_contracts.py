"""Bounded conditional ASCII industry projections, with no runtime integration.

The caller supplies reviewed contract/table identities and explicit assumptions.
They identify a supplied conditional context; this module authenticates no build,
provider, observation options, or expectation authority. Match means only equality
of this projection under those assumptions. All eight unresolved domains remain.
The independent reader preserves ordered text and duplicate elements. Its strict
malformed/unsupported/budget policies are checker policies, not engine verdicts.
"""

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import re
from typing import Optional


SCHEMA = "conditional-industry-projection-v1"
STRING_FIELDS = (
    "szModel", "szSecondaryModel", "szGrowKFM", "szTerrainmap",
    "szSoundScape", "szAuctionImage", "szReportImage",
)
UNKNOWNS = (
    "U_XML", "U_STRINGS", "U_NUMERIC", "U_DEFAULT_LISTS", "U_LOCALE",
    "U_CONTAINERS", "U_PROVIDER", "U_RUNTIME",
)


class Disposition(Enum):
    CONDITIONAL_MATCH = "conditional-match"
    CONDITIONAL_MISMATCH = "conditional-mismatch"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


class ReaderState(Enum):
    VALID = "valid"
    INVALID = "invalid"
    ABSENT = "absent"


@dataclass(frozen=True)
class Sha256:
    value: str


def digest(data: bytes) -> Sha256:
    """Hash exact bytes; no decoding, trimming or path resolution."""
    if type(data) is not bytes:
        raise TypeError("digest requires bytes")
    return Sha256(hashlib.sha256(data).hexdigest())


def table_digest(names: tuple) -> Sha256:
    """Ordered-content identity: compact ASCII JSON array, without a newline."""
    if type(names) is not tuple or any(type(n) is not str for n in names):
        raise TypeError("table names require a tuple of strings")
    return digest(json.dumps(names, ensure_ascii=True, separators=(",", ":")).encode("ascii"))


@dataclass(frozen=True)
class TableIdentity:
    build: Sha256
    receipt: Sha256
    content: Sha256


@dataclass(frozen=True)
class ReviewedTable:
    identity: TableIdentity
    names: tuple


@dataclass(frozen=True)
class ContractIdentity:
    schema: str
    version: str
    build: Sha256
    contract: Sha256
    field_set: Sha256
    table: TableIdentity
    table_size: int
    string_fields: tuple = STRING_FIELDS


@dataclass(frozen=True)
class Assumptions:
    historical_provenance: Optional[bool] = None
    distinct_readers: Optional[bool] = None
    ordinary_ascii_strings: Optional[bool] = None
    successful_containers: Optional[bool] = None
    stipulated_ordered_nodes: Optional[bool] = None
    unknown_new_defaults: Optional[bool] = None


@dataclass(frozen=True)
class ProjectionContext:
    identity: Sha256
    contract: ContractIdentity
    table: TableIdentity
    assumptions: Assumptions


@dataclass(frozen=True)
class ReaderIdentity:
    state: ReaderState
    content: Optional[Sha256]


@dataclass(frozen=True)
class ByteReader:
    """Separate immutable reader inputs; invalid bytes are identified but not read.

    Absent requires payload/content None. Valid requires bytes. Invalid allows
    bytes (including malformed bytes) or None: supplied validity is a condition,
    distinct from the independent parser's rejection of a valid-marked stream.
    """
    identity: ReaderIdentity
    payload: Optional[bytes]


@dataclass(frozen=True)
class InputIdentity:
    context: ProjectionContext
    base: ReaderIdentity
    scenario: ReaderIdentity


@dataclass(frozen=True)
class ExpectedProjection:
    identity: InputIdentity
    value: dict


@dataclass(frozen=True)
class Limits:
    bytes_per_reader: int = 1_048_576
    depth: int = 32
    nodes: int = 20_000
    records: int = 2_048
    string_bytes: int = 4_096
    total_string_bytes: int = 1_048_576
    table_entries: int = 256


@dataclass(frozen=True)
class ComparisonResult:
    disposition: Disposition
    reason: str
    identity: Optional[InputIdentity] = None
    projection: Optional[dict] = None
    unknowns: tuple = UNKNOWNS


class _Reject(Exception):
    def __init__(self, disposition, reason):
        self.disposition = disposition
        self.reason = reason


def _invalid(reason):
    raise _Reject(Disposition.INVALID, reason)


def _unsupported(reason):
    raise _Reject(Disposition.UNSUPPORTED, reason)


def _hash(value):
    if (type(value) is not Sha256 or type(value.value) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value.value) is None):
        _invalid("malformed SHA-256 identity")


def _ascii(value, limit, *, empty=True):
    if type(value) is not str:
        _invalid("string type required")
    if len(value) > limit:
        _unsupported("string budget exceeded")
    if not empty and not value:
        _invalid("empty identity string")
    if any(ord(c) > 127 or (ord(c) < 32 and c not in "\t\r\n") for c in value):
        _unsupported("outside literal ASCII string domain")


def _table_identity(value):
    if type(value) is not TableIdentity:
        _invalid("typed table identity required")
    for f in fields(value):
        _hash(getattr(value, f.name))


def _limits(value):
    if type(value) is not Limits:
        _invalid("typed limits required")
    caps = Limits()
    for f in fields(value):
        number = getattr(value, f.name)
        if type(number) is not int or not 1 <= number <= getattr(caps, f.name):
            _invalid("limits must be positive integers within hard caps")


def _contract(value, limits):
    if type(value) is not ContractIdentity:
        _invalid("typed contract identity required")
    if type(value.schema) is not str or value.schema != SCHEMA:
        _invalid("contract schema mismatch")
    _ascii(value.version, limits.string_bytes, empty=False)
    for name in ("build", "contract", "field_set"):
        _hash(getattr(value, name))
    _table_identity(value.table)
    if value.table.build != value.build:
        _invalid("contract/table build mismatch")
    if (type(value.table_size) is not int
            or not 1 <= value.table_size <= limits.table_entries):
        _invalid("invalid contract table bound")
    if (type(value.string_fields) is not tuple or value.string_fields != STRING_FIELDS
            or any(type(f) is not str for f in value.string_fields)):
        _invalid("contract field schema mismatch")


def _reader(reader, limits):
    if type(reader) is not ByteReader or type(reader.identity) is not ReaderIdentity:
        _invalid("typed reader required")
    ident = reader.identity
    if type(ident.state) is not ReaderState:
        _invalid("typed reader state required")
    if reader.payload is None:
        if ident.content is not None or ident.state is ReaderState.VALID:
            _invalid("missing reader bytes or digest")
    else:
        if type(reader.payload) is not bytes or ident.state is ReaderState.ABSENT:
            _invalid("reader bytes/state mismatch")
        if len(reader.payload) > limits.bytes_per_reader:
            _unsupported("reader byte budget exceeded")
        _hash(ident.content)
        if digest(reader.payload) != ident.content:
            _invalid("reader byte digest mismatch")


def _binding(base, scenario, contract, table, context, expected, limits):
    _limits(limits)
    _contract(contract, limits)
    if type(table) is not ReviewedTable:
        _invalid("typed reviewed table required")
    _table_identity(table.identity)
    if table.identity != contract.table:
        _invalid("reviewed table/contract identity mismatch")
    if type(table.names) is not tuple or len(table.names) != contract.table_size:
        _invalid("table cardinality mismatch")
    for name in table.names:
        _ascii(name, limits.string_bytes, empty=False)
    if len(set(table.names)) != len(table.names):
        _invalid("duplicate table key")
    if table_digest(table.names) != table.identity.content:
        _invalid("ordered table digest mismatch")
    if type(context) is not ProjectionContext:
        _invalid("typed context required")
    _hash(context.identity)
    _contract(context.contract, limits)
    _table_identity(context.table)
    if context.contract != contract or context.table != table.identity:
        _invalid("context identity mismatch")
    if type(context.assumptions) is not Assumptions:
        _invalid("typed assumptions required")
    missing = False
    for f in fields(context.assumptions):
        value = getattr(context.assumptions, f.name)
        if value is not None and type(value) is not bool:
            _invalid("assumptions require bool or None")
        missing = missing or value is not True
    _reader(base, limits)
    _reader(scenario, limits)
    if base is scenario:
        _invalid("base and scenario must be separate reader objects")
    if base.identity.state is not ReaderState.VALID:
        _unsupported("base reader must be valid in this contract")
    identity = InputIdentity(context, base.identity, scenario.identity)
    if type(expected) is not ExpectedProjection or type(expected.identity) is not InputIdentity:
        _invalid("typed expectation binding required")
    # Validate even a differently bound expectation rather than trusting Python's
    # permissive bool/int equality or untyped containers at this boundary.
    e = expected.identity
    if type(e.context) is not ProjectionContext:
        _invalid("malformed expectation context")
    _hash(e.context.identity)
    _contract(e.context.contract, limits)
    _table_identity(e.context.table)
    if type(e.context.assumptions) is not Assumptions:
        _invalid("malformed expectation assumptions")
    for f in fields(e.context.assumptions):
        v = getattr(e.context.assumptions, f.name)
        if v is not None and type(v) is not bool:
            _invalid("malformed expectation assumption type")
    for r in (e.base, e.scenario):
        if type(r) is not ReaderIdentity or type(r.state) is not ReaderState:
            _invalid("malformed expectation reader identity")
        if r.content is not None:
            _hash(r.content)
        if (r.state is ReaderState.ABSENT and r.content is not None
                or r.state is ReaderState.VALID and r.content is None):
            _invalid("malformed expectation reader binding")
    if e != identity:
        _invalid("expectation is bound to different inputs")
    if missing:
        _unsupported("conditional assumptions missing or false")
    return identity


@dataclass
class _Node:
    kind: str
    value: str
    children: list


_TAG = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_SPACE = " \t\r\n"


def _parse(payload, limits):
    """Small independent reader for literal ASCII elements and retained text.

    No engine decoder, XML library, converter, normalizer or registry is used.
    CR/LF, spaces and tabs are preserved literally, without XML normalization.
    Each maximal text segment is one stipulated text node.
    """
    if any(b > 127 or (b < 32 and b not in (9, 10, 13)) for b in payload):
        _unsupported("non-ASCII or forbidden control byte")
    text = payload.decode("ascii")
    if any(token in text for token in ("&", "<!", "<?")):
        _unsupported("entities, declarations, comments or instructions")
    document = _Node("document", "", [])
    stack = [document]
    nodes = 1
    strings = 0
    pos = 0
    roots = 0

    def append(kind, value):
        nonlocal nodes, strings
        nodes += 1
        strings += len(value)
        if nodes > limits.nodes:
            _unsupported("node budget exceeded")
        if len(value) > limits.string_bytes or strings > limits.total_string_bytes:
            _unsupported("string budget exceeded")
        node = _Node(kind, value, [])
        stack[-1].children.append(node)
        return node

    while pos < len(text):
        if text[pos] != "<":
            end = text.find("<", pos)
            if end < 0:
                end = len(text)
            value = text[pos:end]
            if "]]>" in value:
                _invalid("malformed XML: CDATA close delimiter in character data")
            if len(stack) == 1 and value.strip(_SPACE):
                _invalid("malformed XML: text outside document element")
            append("text", value)
            pos = end
            continue
        end = text.find(">", pos + 1)
        if end < 0:
            _invalid("malformed XML: truncated tag")
        token = text[pos + 1:end]
        closing = token.startswith("/")
        if closing:
            token = token[1:]
        self_closing = not closing and token.endswith("/")
        if self_closing:
            token = token[:-1]
        name = token.rstrip(_SPACE)
        if ":" in name or "=" in name or any(c in name for c in _SPACE):
            _unsupported("attributes, namespaces or extended tag syntax")
        if not _TAG.fullmatch(name):
            _invalid("malformed XML: invalid literal tag")
        if closing:
            if len(stack) == 1 or stack[-1].value != name:
                _invalid("malformed XML: mismatched closing tag")
            stack.pop()
        else:
            if len(stack) == 1:
                roots += 1
                if roots > 1:
                    _invalid("malformed XML: multiple document elements")
            node = append("element", name)
            if len(stack) > limits.depth:
                _unsupported("depth budget exceeded")
            if not self_closing:
                stack.append(node)
        pos = end + 1
    if len(stack) != 1 or roots != 1:
        _invalid("malformed XML: missing or unclosed document element")
    _domain(document)
    return document


def _domain(document):
    """Reject fields/constructs outside the narrow structural schema everywhere.

    Wrapper is a structural direct-lookup decoy. Other is an intervening list
    entry, whose presence and text remain in the stipulated child vector.
    """
    scalar = set(STRING_FIELDS) | {"szName", "Input", "Output", "InCity", "szDisplayName"}
    allowed = {
        "document": {"RRTIndustries"},
        "RRTIndustries": {"Industries"},
        "Industries": {"RRTIndustry"},
        "RRTIndustry": set(STRING_FIELDS) | {"szName", "Production", "DisplayNames", "Locations", "Wrapper"},
        "Wrapper": set(STRING_FIELDS) | {"szName", "Wrapper"},
        "Production": {"Resource", "Other"},
        "Resource": {"Input", "Output"},
        "DisplayNames": {"szDisplayName", "Other"},
        "Locations": {"Location", "Other"},
        "Location": {"InCity"},
    }
    pending = [(document, "")]
    while pending:
        node, parent = pending.pop()
        if node.kind == "text":
            continue
        if node.value in scalar:
            if any(c.kind != "text" for c in node.children):
                _unsupported("nested scalar content")
            continue
        if node.value == "Other":
            permitted = {"Input", "Output"} if parent == "Production" else {"InCity"} if parent == "Locations" else set()
        else:
            permitted = allowed.get(node.value if node.kind == "element" else "document", set())
        for child in node.children:
            if child.kind == "element" and child.value not in permitted:
                _unsupported("field or structural element outside contract")
            pending.append((child, node.value))
        if node.value == "Industries":
            found = False
            for child in node.children:
                if child.kind == "element":
                    found = True
                elif child.value.strip(_SPACE) or found:
                    _unsupported("uncovered record-level text suffix")


def _first(node, name):
    if node is not None:
        for child in node.children:
            if child.kind == "element" and child.value == name:
                return child
    return None


def _value(node):
    return "".join(c.value for c in node.children if c.kind == "text")


def _named(node, name, default):
    selected = _first(node, name)
    return default if selected is None else _value(selected)


def _unknown():
    return {"unknown": "new-record-default"}


def _records(document):
    root = _first(document, "RRTIndustries")
    container = _first(root, "Industries")
    start = _first(container, "RRTIndustry")
    return [] if start is None else container.children[container.children.index(start):]


def _load(records, nodes, origin, limits, count):
    for node in nodes:
        count += 1
        if count > limits.records:
            _unsupported("record budget exceeded")
        key = _named(node, "szName", "")
        if key not in records:
            records[key] = {
                "key": key, "constructed_in": origin, "scenario_touched": False,
                "strings": {f: _unknown() for f in STRING_FIELDS},
                "production": _unknown(), "display_names": _unknown(), "locations": _unknown(),
            }
        record = records[key]
        if origin == "scenario":
            record["scenario_touched"] = True
        for f in STRING_FIELDS:
            record["strings"][f] = _named(node, f, record["strings"][f])
        for container_name, entry, destination in (
                ("Production", "Resource", "production"),
                ("DisplayNames", "szDisplayName", "display_names"),
                ("Locations", "Location", "locations")):
            container = _first(node, container_name)
            first = _first(container, entry)
            if first is None:
                continue
            result = []
            for item in container.children[container.children.index(first):]:
                if destination == "display_names":
                    if item.kind != "element" or item.value != entry:
                        break
                    result.append(_value(item))
                elif destination == "production":
                    result.append({"Input": _named(item, "Input", ""), "Output": _named(item, "Output", "")})
                else:
                    result.append(_named(item, "InCity", ""))
            record[destination] = result
    return count


def _projection(base, scenario, table, limits):
    base_nodes = _records(_parse(base.payload, limits))
    scenario_nodes = []
    if scenario.identity.state is ReaderState.VALID:
        scenario_nodes = _records(_parse(scenario.payload, limits))
    records = {}
    count = _load(records, base_nodes, "base", limits, 0)
    _load(records, scenario_nodes, "scenario", limits, count)
    pruned = []
    if scenario_nodes:
        for name in table.names:
            if name in records and not records[name]["scenario_touched"]:
                pruned.append(name)
                del records[name]
    names = [name for name in table.names if name in records]
    return {
        "records": list(records.values()),
        "accepted_ids": [{"key": name, "id": i} for i, name in enumerate(names)],
        "pruned": pruned,
    }


def _expected(value, limits):
    """Strict bounded projection schema, preserving UNKNOWN_DEFAULT vs empty."""
    # Bound all caller-owned containers before schema validation/equality.
    pending = [(value, 0)]
    nodes = strings = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > limits.nodes or depth > limits.depth:
            _unsupported("expectation node/depth budget exceeded")
        if type(item) is str:
            _ascii(item, limits.string_bytes)
            strings += len(item)
            if strings > limits.total_string_bytes:
                _unsupported("expectation string budget exceeded")
        elif type(item) in (list, dict):
            if len(item) > limits.nodes or len(pending) + len(item) > limits.nodes:
                _unsupported("expectation container budget exceeded")
            if type(item) is dict:
                for k, v in item.items():
                    if type(k) is not str:
                        _invalid("expectation keys must be strings")
                    pending.append((k, depth + 1))
                    pending.append((v, depth + 1))
            else:
                pending.extend((v, depth + 1) for v in item)
        elif type(item) not in (int, bool):
            _invalid("unsupported expectation value type")

    def shape(obj, keys):
        if type(obj) is not dict or set(obj) != set(keys):
            _invalid("expectation schema mismatch")

    def unknown(obj):
        return type(obj) is dict and obj == _unknown()

    def string(obj):
        if type(obj) is not str:
            _invalid("expectation string required")

    shape(value, ("records", "accepted_ids", "pruned"))
    for key in value:
        if type(value[key]) is not list:
            _invalid("expectation list required")
    if len(value["records"]) > limits.records:
        _unsupported("expectation record budget exceeded")
    record_keys = set()
    for record in value["records"]:
        shape(record, ("key", "constructed_in", "scenario_touched", "strings", "production", "display_names", "locations"))
        string(record["key"])
        if record["key"] in record_keys:
            _invalid("duplicate expectation record")
        record_keys.add(record["key"])
        if type(record["constructed_in"]) is not str or record["constructed_in"] not in ("base", "scenario"):
            _invalid("expectation origin mismatch")
        if type(record["scenario_touched"]) is not bool:
            _invalid("expectation touched flag must be bool")
        shape(record["strings"], STRING_FIELDS)
        for v in record["strings"].values():
            if not unknown(v):
                string(v)
        for key in ("production", "display_names", "locations"):
            v = record[key]
            if unknown(v):
                continue
            if type(v) is not list:
                _invalid("expectation projected list required")
            for item in v:
                if key == "production":
                    shape(item, ("Input", "Output"))
                    string(item["Input"])
                    string(item["Output"])
                else:
                    string(item)
    accepted = set()
    for i, row in enumerate(value["accepted_ids"]):
        shape(row, ("key", "id"))
        string(row["key"])
        if type(row["id"]) is not int or row["id"] != i or row["key"] in accepted:
            _invalid("expectation dense identity schema mismatch")
        accepted.add(row["key"])
    pruned = set()
    for key in value["pruned"]:
        string(key)
        if key in pruned:
            _invalid("duplicate expectation pruned key")
        pruned.add(key)


def compare_projection(*, base: ByteReader, scenario: ByteReader,
                       contract: ContractIdentity, table: ReviewedTable,
                       context: ProjectionContext, expected: ExpectedProjection,
                       limits: Limits = Limits()) -> ComparisonResult:
    """Compare explicit bytes to an exactly bound, reviewed expected projection.

    This pure API performs no file, process, provider, application or engine work.
    Unsupported/invalid results never contain a partial projection. Identity is
    only supplied conditional provenance; matching grants no action evidence.
    Limits may be lowered, never raised beyond the module's hard caps.
    """
    try:
        identity = _binding(base, scenario, contract, table, context, expected, limits)
        _expected(expected.value, limits)
        projection = _projection(base, scenario, table, limits)
        disposition = (Disposition.CONDITIONAL_MATCH if projection == expected.value
                       else Disposition.CONDITIONAL_MISMATCH)
        return ComparisonResult(disposition, "conditional projection comparison", identity, projection)
    except _Reject as error:
        return ComparisonResult(error.disposition, error.reason)
