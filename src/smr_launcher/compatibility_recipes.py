"""Pure, bounded building blocks for reviewed local compatibility recipes.

This module performs typed byte edits, not automatic map repair. Callers must
separately establish recipe applicability, registry and effective base context,
complete source inventory, and preparation metadata before publishing an edition.
No asset bytes are bundled here; output is constructed from local input bytes.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import re
from xml.parsers import expat

from .rules import RuleError, TokenPatch


COMPILER_VERSION = "typed-identity-spans-v2"
MAX_XML_BYTES = 1024 * 1024  # Also the existing full-file TokenPatch limit.
MAX_NODES = 50_000
MAX_DEPTH = 64
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9 _-]{0,127}\Z")
_TAG = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_KINDS = {
    "industry": ("RRTIndustries", "Industries", "RRTIndustry", "szName"),
    "depot": ("RRTDepots", "Depot", "szName"),
}


@dataclass(frozen=True)
class IdentityEdit:
    """One declared typed key/reference operation; paths are exact tag tuples."""

    kind: str
    source: str
    target: str
    count: int = 1
    goal_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.kind not in (*_KINDS, "industry-goal")
                or not isinstance(self.source, str) or not isinstance(self.target, str)
                or not _IDENTIFIER.fullmatch(self.source)
                or not _IDENTIFIER.fullmatch(self.target) or self.source == self.target
                or type(self.count) is not int or not 1 <= self.count <= 1000):
            raise RuleError("Invalid typed identity operation")
        if self.kind == "industry-goal":
            if (not isinstance(self.goal_path, tuple) or not 3 <= len(self.goal_path) <= MAX_DEPTH
                    or self.goal_path[-2:] != ("OwnListItem", "szObjectName")
                    or any(not isinstance(x, str) or not _TAG.fullmatch(x) for x in self.goal_path)):
                raise RuleError("Industry goal needs an exact structural path")
        elif self.goal_path:
            raise RuleError("Definition operation cannot declare a goal path")

    @property
    def path(self) -> tuple[str, ...]:
        return self.goal_path if self.kind == "industry-goal" else _KINDS[self.kind]


@dataclass(frozen=True)
class PreservedOccurrence:
    """Reviewed non-reference exact text/attribute that must remain unchanged."""

    path: tuple[str, ...]
    value: str
    count: int = 1
    attribute: str = ""

    def __post_init__(self) -> None:
        if (not isinstance(self.path, tuple) or not 1 <= len(self.path) <= MAX_DEPTH
                or any(not isinstance(x, str) or not _TAG.fullmatch(x) for x in self.path)
                or not isinstance(self.value, str) or not _IDENTIFIER.fullmatch(self.value)
                or type(self.count) is not int or not 1 <= self.count <= 1000
                or not isinstance(self.attribute, str)
                or self.attribute and not _TAG.fullmatch(self.attribute)):
            raise RuleError("Invalid preserved occurrence declaration")


@dataclass
class _Node:
    path: tuple[str, ...]
    start: int
    end: int = 0
    attributes: dict[str, str] = field(default_factory=dict)
    text: list[str] = field(default_factory=list)
    content: list = field(default_factory=list)
    children: list["_Node"] = field(default_factory=list)
    parent: "_Node | None" = field(default=None, repr=False)

    @property
    def value(self) -> str:
        return self.raw_value.strip()

    @property
    def raw_value(self) -> str:
        return "".join(item.raw_value if isinstance(item, _Node) else item
                       for item in self.content)


def _parse(data: bytes) -> list[_Node]:
    if not isinstance(data, bytes) or not data or len(data) > MAX_XML_BYTES:
        raise RuleError("Recipe XML must be nonempty bytes within the size limit")
    try:
        data.decode("utf-8-sig", errors="strict")
    except UnicodeError as exc:
        raise RuleError("Recipe XML requires UTF-8") from exc
    parser = expat.ParserCreate(encoding="UTF-8")
    nodes: list[_Node] = []
    stack: list[_Node] = []

    def forbidden(*_args):
        raise RuleError("DTD, entities and processing instructions are unsupported")

    def declaration(_version, encoding, _standalone):
        if encoding and encoding.casefold() in ("ascii", "us-ascii"):
            # ASCII has the same byte offsets as UTF-8 for every allowed byte.
            # Do not reinterpret mislabeled UTF-8 or rewrite the declaration.
            try:
                data.decode("ascii", errors="strict")
            except UnicodeError as exc:
                raise RuleError("Recipe XML declared ASCII contains non-ASCII bytes") from exc
        elif encoding and encoding.casefold() not in ("utf-8", "utf8"):
            raise RuleError("Recipe XML declaration requires UTF-8 or ASCII")

    def start(name, attrs):
        if (len(nodes) >= MAX_NODES or len(stack) >= MAX_DEPTH
                or not _TAG.fullmatch(name)
                or any(not _TAG.fullmatch(x) or x == "xmlns" for x in attrs)):
            raise RuleError("Unsupported namespace, tag or XML complexity")
        # Find the end of the opening tag while respecting quoted attributes.
        pos, quote = parser.CurrentByteIndex, None
        while pos < len(data):
            char = data[pos]
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in (34, 39):
                quote = char
            elif char == 62:
                break
            pos += 1
        if pos == len(data):
            raise RuleError("Unterminated XML opening tag")
        parent = stack[-1] if stack else None
        node = _Node((parent.path if parent else ()) + (name,), pos + 1,
                     attributes=attrs, parent=parent)
        if parent:
            parent.children.append(node)
            parent.content.append(node)
        nodes.append(node)
        stack.append(node)

    def end(_name):
        stack.pop().end = parser.CurrentByteIndex

    def text(value):
        if stack:
            stack[-1].text.append(value)
            stack[-1].content.append(value)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.XmlDeclHandler = declaration
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.ProcessingInstructionHandler = forbidden
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise RuleError("Recipe XML is not well formed") from exc
    return nodes


def compile_identity_patch(
    *, path: str, data: bytes, before_sha256: str, after_sha256: str,
    edits: tuple[IdentityEdit, ...],
    preserved: tuple[PreservedOccurrence, ...] = (),
) -> TokenPatch:
    """Compile one exact XML input to a full-file patch, without any file I/O.

Every exact old/new text or attribute occurrence must be either a declared typed
edit or a reviewed preserved occurrence. Comments and descriptive substrings
remain untouched. A successful result proves byte transformation only.
    """
    if hashlib.sha256(data).hexdigest() != before_sha256:
        raise RuleError("Recipe source hash differs")
    if (not isinstance(edits, tuple) or not 1 <= len(edits) <= 256
            or any(not isinstance(e, IdentityEdit) for e in edits)
            or not isinstance(preserved, tuple) or len(preserved) > 1000
            or any(not isinstance(p, PreservedOccurrence) for p in preserved)):
        raise RuleError("Recipe operations must be bounded typed declarations")
    # One source maps to one destination, and distinct sources remain distinct.
    aliases = {e.source: e.target for e in edits}
    if (any(aliases[e.source] != e.target for e in edits)
            or len(set(aliases.values())) != len(aliases)
            or set(aliases) & set(aliases.values())):
        raise RuleError("Recipe aliases must be injective and nonoverlapping")
    declarations = [(e.kind, e.path, e.source) for e in edits]
    if len(declarations) != len(set(declarations)):
        raise RuleError("Duplicate identity edit")
    nodes = _parse(data)
    for node in nodes:
        if (node.path in _KINDS.values()
                or node.path[-2:] in (("OwnListItem", "szObjectName"),
                                     ("OwnListItem", "szObjectType"))):
            if node.children or node.raw_value != node.value:
                raise RuleError("Identity fields require plain unpadded leaf text")
    for definition_path in _KINDS.values():
        # The engine reads the first matching singleton child. A second name or
        # Industries container is not an additional effective definition.
        container_path = definition_path[:-2]
        if len([n for n in nodes if n.path == container_path]) > 1:
            raise RuleError("Duplicate definition container")
        for record in (n for n in nodes if n.path == definition_path[:-1]):
            names = [c for c in record.children if c.path[-1] == "szName"]
            if len(names) != 1 or names[0].children or not names[0].value:
                raise RuleError("Definition needs exactly one nonempty leaf identity")
        keys = [n.value for n in nodes if n.path == definition_path]
        if len(keys) != len(set(keys)):
            raise RuleError("Duplicate definition keys")
        if set(keys) & set(aliases.values()):
            raise RuleError("Destination already exists as a definition key")
    replacements: list[tuple[int, int, bytes]] = []
    selected: set[int] = set()
    for edit in edits:
        matches = [n for n in nodes if n.path == edit.path and n.value == edit.source]
        if len(matches) != edit.count:
            raise RuleError("Typed identity match count differs")
        for node in matches:
            if id(node) in selected or node.children:
                raise RuleError("Overlapping or mixed-content identity edit")
            if edit.kind == "industry-goal":
                types = [c for c in node.parent.children if c.path[-1] == "szObjectType"]
                names = [c for c in node.parent.children if c.path[-1] == "szObjectName"]
                if (len(names) != 1 or len(types) != 1 or types[0].children
                        or types[0].value != "Industry"):
                    raise RuleError("Ownership reference is not unambiguously Industry typed")
            body = data[node.start:node.end]
            # Target keys use plain text. Entities, comments or CDATA inside a
            # target are unsupported rather than silently reformatted.
            if body.strip(b" \t\r\n") != edit.source.encode("ascii"):
                raise RuleError("Unsupported lexical identity representation")
            left = len(body) - len(body.lstrip(b" \t\r\n"))
            right = len(body.rstrip(b" \t\r\n"))
            replacements.append((node.start + left, node.start + right, edit.target.encode("ascii")))
            selected.add(id(node))
    actual = Counter()
    relevant = set(aliases) | set(aliases.values())
    folded = {value.casefold() for value in relevant}
    for node in nodes:
        if (node.path[-2:] == ("OwnListItem", "szObjectName")
                and node.value.casefold() in folded and node.value not in relevant):
            raise RuleError("Case-variant ownership reference needs explicit review")
        if (id(node) not in selected and node.value in relevant
                and (not node.children or any(text.strip() for text in node.text))):
            actual[node.path, node.value, ""] += 1
        for attribute, value in node.attributes.items():
            if value.strip() in relevant:
                actual[node.path, value.strip(), attribute] += 1
    expected = Counter()
    for occurrence in preserved:
        if (occurrence.path in _KINDS.values()
                or occurrence.path[-2:] in (("OwnListItem", "szObjectName"),
                                           ("OwnListItem", "szObjectType"))):
            raise RuleError("Typed identity fields cannot be declared descriptive occurrences")
        key = occurrence.path, occurrence.value, occurrence.attribute
        if expected[key] or occurrence.value not in relevant:
            raise RuleError("Duplicate or irrelevant preserved occurrence")
        expected[key] = occurrence.count
    if actual != expected:
        raise RuleError("Unreviewed or missing exact identity occurrence")
    replacements.sort()
    parts, previous = [], 0
    for start, end, replacement in replacements:
        if start < previous or end < start:
            raise RuleError("Overlapping identity byte spans")
        parts.extend((data[previous:start], replacement))
        previous = end
    parts.append(data[previous:])
    changed = b"".join(parts)
    _parse(changed)
    if hashlib.sha256(changed).hexdigest() != after_sha256:
        raise RuleError("Recipe output hash differs")
    return TokenPatch(path, before_sha256, after_sha256, data, changed)
