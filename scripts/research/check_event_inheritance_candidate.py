#!/usr/bin/env python3
"""Read-only exact duplicate-Effects candidate check; native behavior is unknown.

Inputs must be independently hash-bound frozen copies. A match proves only that
the candidate removes the declared duplicate subtrees and preserves other bytes.
It does not select native resources, enable a recipe or certify a repair.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from smr_launcher.compatibility_recipes import MAX_XML_BYTES, _parse  # noqa: E402
from smr_launcher.activation import ActivationError  # noqa: E402
from smr_launcher.recipe_service import _read  # noqa: E402
from smr_launcher.rules import RuleError  # noqa: E402

CHECKER_VERSION = "exact-duplicate-stock-effects-v1"
_TITLE = re.compile(r"[A-Za-z][A-Za-z0-9 _-]{0,127}\Z")
_FIELDS = ("szType", "fModifierMin", "fModifierMax")


def _literal_span(node, data):
    name = node.path[-1].encode("ascii")
    start = data.rfind(b"<", 0, node.start)
    stop = node.end + len(name) + 3
    if (
        data[start : node.start] != b"<" + name + b">"
        or data[node.end : stop] != b"</" + name + b">"
    ):
        raise RuleError("Selected elements require plain literal opening and closing tags")
    return start, stop


def _container_span(node, data):
    span = _literal_span(node, data)
    previous = node.start
    for child in node.children:
        start, stop = _literal_span(child, data)
        if data[previous:start].strip(b" \t\r\n"):
            raise RuleError("Selected containers require literal XML whitespace")
        previous = stop
    if data[previous : node.end].strip(b" \t\r\n"):
        raise RuleError("Selected containers require literal XML whitespace")
    return span


def _leaf(node, data):
    _literal_span(node, data)
    if (
        node.children
        or node.attributes
        or not node.raw_value
        or node.raw_value != node.raw_value.strip()
        or data[node.start : node.end] != node.raw_value.encode("utf-8")
    ):
        raise RuleError("Selected fields require plain nonempty unpadded leaf text")
    return node.raw_value


def _events(data):
    nodes = _parse(data)
    if nodes[0].path != ("RRTEvents",):
        raise RuleError("Expected an RRTEvents root")
    result = {}
    for node in nodes[0].children:
        if node.path != ("RRTEvents", "Event"):
            continue
        names = [child for child in node.children if child.path[-1] == "szTitle"]
        if len(names) != 1:
            raise RuleError("Event identity is absent or ambiguous")
        title = _leaf(names[0], data)
        if not _TITLE.fullmatch(title) or title in result:
            raise RuleError("Event identity is unsupported or duplicated")
        result[title] = node
    return result


def _effects(event, data):
    matches = [child for child in event.children if child.path[-1] == "Effects"]
    if len(matches) != 1:
        raise RuleError("Selected event needs exactly one Effects subtree")
    node = matches[0]
    # Use parser offsets and raw inter-element gaps. Decoded text can hide
    # character references or non-XML whitespace, so it cannot supply this gate.
    span = _container_span(node, data)
    if len(node.children) != 1 or node.children[0].path[-1] != "EventEffect":
        raise RuleError("Effects subtree is outside the supported literal subset")
    effect = node.children[0]
    _container_span(effect, data)
    if tuple(child.path[-1] for child in effect.children) != _FIELDS:
        raise RuleError("Expected one Stock effect with the three explicit ordered fields")
    values = tuple(_leaf(child, data) for child in effect.children)
    if values[0] != "Stock":
        raise RuleError("Only literal Stock effects are supported")
    return span, values


def check_candidate(*, original, base, candidate, titles, hashes):
    """Compare bounded immutable bytes; never write files or infer engine state."""
    inputs = dict(original=original, base=base, candidate=candidate)
    if not isinstance(hashes, dict) or set(hashes) != set(inputs):
        raise RuleError("All three independently supplied input hashes are required")
    for role, data in inputs.items():
        expected = hashes[role]
        if (
            not isinstance(data, bytes)
            or not 0 < len(data) <= MAX_XML_BYTES
            or not isinstance(expected, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or hashlib.sha256(data).hexdigest() != expected
        ):
            raise RuleError("Input is oversized, empty, unsupported or not hash-bound")
    if (
        not isinstance(titles, tuple)
        or not 1 <= len(titles) <= 64
        or any(not isinstance(title, str) or not _TITLE.fullmatch(title) for title in titles)
        or len(set(titles)) != len(titles)
    ):
        raise RuleError("Selected event identities must be a bounded unique tuple")
    originals, defaults = _events(original), _events(base)
    spans = []
    for title in titles:
        if title not in originals or title not in defaults:
            raise RuleError("Selected event is missing from original or base")
        span, values = _effects(originals[title], original)
        _, base_values = _effects(defaults[title], base)
        if values != base_values:
            raise RuleError("Selected Effects differ from the supplied base")
        spans.append(span)
    chunks, previous = [], 0
    for start, stop in sorted(spans):
        if start < previous or stop <= start:
            raise RuleError("Selected subtree spans overlap or are invalid")
        chunks.append(original[previous:start])
        previous = stop
    chunks.append(original[previous:])
    if candidate != b"".join(chunks):
        raise RuleError("Candidate differs outside the declared subtree removals")
    return dict(
        schema=1,
        checker=CHECKER_VERSION,
        status="exact_candidate_bytes_match",
        input_sha256=dict(hashes),
        selected_events=len(titles),
        removed_effects=len(spans),
        native_context="unknown",
        feature_preservation="unknown",
        gameplay_and_save="unknown",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for role in ("original", "base", "candidate"):
        parser.add_argument("--" + role, type=Path, required=True)
        parser.add_argument("--" + role + "-sha256", required=True)
    parser.add_argument("--event-title", action="append", required=True)
    args = parser.parse_args(argv)
    hashes = {role: getattr(args, role + "_sha256") for role in ("original", "base", "candidate")}
    try:
        inputs = {
            role: _read(getattr(args, role), hashes[role], limit=MAX_XML_BYTES) for role in hashes
        }
        result = check_candidate(**inputs, titles=tuple(args.event_title), hashes=hashes)
    except (ActivationError, OSError, RuleError):
        print(json.dumps(dict(schema=1, checker=CHECKER_VERSION, status="refused")))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
