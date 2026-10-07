"""Bounded, conditional scalar assertions over caller-supplied typed inputs.

This standalone module parses no XML and authenticates no historical evidence,
build, library, provider or runtime. A match means equality under the exact
supplied context and premises only. All eight unresolved domains persist.
Integer text uses a stipulated ASCII classifier. Float and boolean library
results are explicit premises, never local libc observations. There is no batch
API, integration, constructor inference or engine/feature verification API.
"""

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import math
import re
import struct
from typing import Optional, Union


MAX_TEXT_BYTES = 4096
SCHEMA = "conditional-scalar-projection-v1"
UNKNOWNS = (
    "U_XML", "U_STRINGS", "U_NUMERIC", "U_DEFAULT_LISTS", "U_LOCALE",
    "U_CONTAINERS", "U_PROVIDER", "U_RUNTIME",
)


class Disposition(Enum):
    CONDITIONAL_MATCH = "conditional-match"
    CONDITIONAL_MISMATCH = "conditional-mismatch"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


class Reason(Enum):
    COMPARISON = "conditional-scalar-comparison"
    TYPE = "invalid-type"
    STATE = "invalid-state"
    SHA256 = "invalid-sha256"
    BITS = "invalid-bits"
    PREMISE = "invalid-premise"
    ASSERTION = "invalid-assertion"
    IDENTITY = "identity-mismatch"
    BUDGET = "text-budget-exceeded"
    OVERLOAD = "unsupported-overload"
    MISSING_PREMISE = "missing-conditional-premise"
    STRING_DOMAIN = "unsupported-string-domain"
    INTEGER_RANGE = "unsupported-integer-range"
    FLOAT_DOMAIN = "unsupported-float-domain"


class Overload(Enum):
    NAMED = "named"
    DIRECT = "direct"


class ScalarType(Enum):
    INT32 = "int32"
    UINT32 = "uint32"
    FLOAT32 = "float32"
    BOOL = "bool"


class Presence(Enum):
    ABSENT = "absent"
    PRESENT = "present"


class Extraction(Enum):
    NOT_ATTEMPTED = "not-attempted"
    SUCCESS = "success"
    FAILURE = "failure"


class UnknownValue(Enum):
    BEFORE = "unknown-before"
    STACK = "unknown-stack"


class Branch(Enum):
    NAMED_ABSENT = "named-absent"
    NAMED_EXTRACTION_FAILURE = "named-extraction-failure"
    DIRECT_BOOL_EXTRACTION_FAILURE = "direct-bool-extraction-failure"
    INTEGER_EMPTY = "integer-empty"
    INTEGER_PREFIX = "integer-prefix"
    FLOAT_SUPPLIED = "float-supplied"
    BOOL_TRUE_WORD = "bool-true-word"
    BOOL_FALSE_WORD = "bool-false-word"
    BOOL_SUPPLIED_INTEGER = "bool-supplied-integer"


@dataclass(frozen=True)
class Sha256:
    value: str


@dataclass(frozen=True)
class KnownBits:
    value: int


Value = Union[KnownBits, UnknownValue]


@dataclass(frozen=True)
class EvidenceIdentity:
    """Caller assertions of historical identities, with no authenticity claim."""
    build: Sha256
    contract: Sha256
    review: Sha256


@dataclass(frozen=True)
class Assumptions:
    historical_contract: Optional[bool] = None
    ordinary_string_extraction: Optional[bool] = None
    ascii_integer_classifier: Optional[bool] = None
    ascii_whole_string_comparison: Optional[bool] = None
    ieee_binary32_assignment: Optional[bool] = None


@dataclass(frozen=True)
class ConditionalContext:
    identity: Sha256
    evidence: EvidenceIdentity
    assumptions: Assumptions


@dataclass(frozen=True)
class HistoricalDouble:
    """Supplied historical atof result; Python float is the double carrier."""
    value: float
    evidence: Sha256


@dataclass(frozen=True)
class HistoricalInteger:
    """Supplied defined signed32 atoi result; no lexical rule is inferred."""
    value: int
    evidence: Sha256


@dataclass(frozen=True)
class ScalarInput:
    overload: Overload
    scalar_type: ScalarType
    presence: Presence
    extraction: Extraction
    text: Optional[bytes]
    prior: Value
    atof_result: Optional[HistoricalDouble] = None
    atoi_result: Optional[HistoricalInteger] = None


@dataclass(frozen=True)
class InputIdentity:
    context: ConditionalContext
    scalar: ScalarInput
    content: Sha256


@dataclass(frozen=True)
class ScalarOutcome:
    branch: Branch
    wrote: bool
    return_value: int
    value: Value


@dataclass(frozen=True)
class ExpectedAssertion:
    identity: InputIdentity
    outcome: ScalarOutcome


@dataclass(frozen=True)
class ComparisonResult:
    disposition: Disposition
    reason: Reason
    identity: Optional[InputIdentity] = None
    outcome: Optional[ScalarOutcome] = None
    unknowns: tuple[str, ...] = UNKNOWNS


class InputRejected(ValueError):
    """A binding error with a fixed disposition and reason vocabulary."""
    def __init__(self, disposition: Disposition, reason: Reason):
        super().__init__(reason.value)
        self.disposition = disposition
        self.reason = reason


def _reject(reason, disposition=Disposition.INVALID):
    raise InputRejected(disposition, reason)


def _unsupported(reason):
    _reject(reason, Disposition.UNSUPPORTED)


def _sha(value):
    if (type(value) is not Sha256 or type(value.value) is not str
            or len(value.value) != 64
            or re.fullmatch(r"[0-9a-f]{64}", value.value) is None):
        _reject(Reason.SHA256)


def digest_text(data: bytes) -> Sha256:
    """Exact bounded bytes only; no decoding or normalization."""
    if type(data) is not bytes:
        _reject(Reason.TYPE)
    if len(data) > MAX_TEXT_BYTES:
        _unsupported(Reason.BUDGET)
    return Sha256(hashlib.sha256(data).hexdigest())


def _value(value, scalar_type):
    if type(value) is UnknownValue:
        return
    if (type(value) is not KnownBits or type(value.value) is not int
            or not 0 <= value.value <= 0xffffffff):
        _reject(Reason.BITS)
    if scalar_type is ScalarType.BOOL and value.value not in (0, 1):
        _reject(Reason.BITS)


def _validate(context, scalar):
    if type(context) is not ConditionalContext or type(scalar) is not ScalarInput:
        _reject(Reason.TYPE)
    _sha(context.identity)
    if type(context.evidence) is not EvidenceIdentity:
        _reject(Reason.TYPE)
    for f in fields(EvidenceIdentity):
        _sha(getattr(context.evidence, f.name))
    if type(context.assumptions) is not Assumptions:
        _reject(Reason.TYPE)
    for f in fields(Assumptions):
        v = getattr(context.assumptions, f.name)
        if v is not None and type(v) is not bool:
            _reject(Reason.PREMISE)
    for name, enum in (("overload", Overload), ("scalar_type", ScalarType),
                       ("presence", Presence), ("extraction", Extraction)):
        if type(getattr(scalar, name)) is not enum:
            _reject(Reason.TYPE)
    _value(scalar.prior, scalar.scalar_type)
    if scalar.text is not None:
        digest_text(scalar.text)
    if scalar.presence is Presence.ABSENT:
        if scalar.extraction is not Extraction.NOT_ATTEMPTED or scalar.text is not None:
            _reject(Reason.STATE)
    elif scalar.extraction is Extraction.NOT_ATTEMPTED:
        _reject(Reason.STATE)
    elif scalar.extraction is Extraction.SUCCESS and scalar.text is None:
        _reject(Reason.STATE)
    if scalar.atof_result is not None:
        v = scalar.atof_result
        if scalar.scalar_type is not ScalarType.FLOAT32 or type(v) is not HistoricalDouble:
            _reject(Reason.PREMISE)
        if type(v.value) is not float:
            _reject(Reason.PREMISE)
        _sha(v.evidence)
    if scalar.atoi_result is not None:
        v = scalar.atoi_result
        if scalar.scalar_type is not ScalarType.BOOL or type(v) is not HistoricalInteger:
            _reject(Reason.PREMISE)
        if type(v.value) is not int or not -2147483648 <= v.value <= 2147483647:
            _reject(Reason.PREMISE)
        _sha(v.evidence)


def _value_encoding(value):
    return ["bits", value.value] if type(value) is KnownBits else ["unknown", value.value]


def bind_input(context: ConditionalContext, scalar: ScalarInput) -> InputIdentity:
    """Bind exact typed input/context and bytes; never certify their provenance.

    Invalid types/states and exceeded budgets raise InputRejected. Semantic
    premises are evaluated only by check_scalar, after assertion identity checks.
    Encoding is fixed positional compact ASCII JSON with exact byte hex and
    double bits, including a supplied zero's sign. No caller dictionary is read.
    """
    _validate(context, scalar)
    atof = scalar.atof_result
    atoi = scalar.atoi_result
    encoded = [
        SCHEMA, context.identity.value,
        [getattr(context.evidence, f.name).value for f in fields(EvidenceIdentity)],
        [getattr(context.assumptions, f.name) for f in fields(Assumptions)],
        scalar.overload.value, scalar.scalar_type.value, scalar.presence.value,
        scalar.extraction.value, None if scalar.text is None else scalar.text.hex(),
        _value_encoding(scalar.prior),
        None if atof is None else [struct.pack(">d", atof.value).hex(), atof.evidence.value],
        None if atoi is None else [atoi.value, atoi.evidence.value],
    ]
    data = json.dumps(encoded, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return InputIdentity(context, scalar, Sha256(hashlib.sha256(data).hexdigest()))


def _require(premise):
    if premise is not True:
        _unsupported(Reason.MISSING_PREMISE)


def _ascii(text):
    # Check the complete bounded text: high bytes/NUL after a prefix remain out
    # of domain. This is a checker restriction, never an engine parser verdict.
    if any(b == 0 or b >= 128 for b in text):
        _unsupported(Reason.STRING_DOMAIN)


def _integer(text):
    i = 0
    while i < len(text) and text[i] in b" \t\n\r\v\f":
        i += 1
    negative = i < len(text) and text[i] == 45
    if i < len(text) and text[i] in (43, 45):
        i += 1
    magnitude = 0
    while i < len(text) and 48 <= text[i] <= 57:
        digit = text[i] - 48
        if magnitude > (2147483647 - digit) // 10:
            _unsupported(Reason.INTEGER_RANGE)
        magnitude = magnitude * 10 + digit
        i += 1
    return (-magnitude if negative else magnitude) & 0xffffffff


def _float_bits(value):
    # Local IEEE packing tests exactness of an explicitly supplied double. It
    # never converts lexical text or asserts anything about historical atof.
    if not math.isfinite(value) or (value == 0.0 and math.copysign(1.0, value) < 0):
        _unsupported(Reason.FLOAT_DOMAIN)
    try:
        packed = struct.pack(">f", value)
    except (OverflowError, struct.error):
        _unsupported(Reason.FLOAT_DOMAIN)
    bits = int.from_bytes(packed, "big")
    exponent = (bits >> 23) & 255
    if value == 0.0:
        return 0
    if exponent in (0, 255) or struct.unpack(">f", packed)[0] != value:
        _unsupported(Reason.FLOAT_DOMAIN)
    return bits


def _project(context, scalar):
    a = context.assumptions
    _require(a.historical_contract)
    if scalar.overload is Overload.DIRECT and scalar.scalar_type is not ScalarType.BOOL:
        _unsupported(Reason.OVERLOAD)
    if scalar.overload is Overload.NAMED and scalar.presence is Presence.ABSENT:
        return ScalarOutcome(Branch.NAMED_ABSENT, False, 0, scalar.prior)
    if scalar.presence is Presence.ABSENT or scalar.extraction is Extraction.FAILURE:
        if scalar.overload is Overload.DIRECT:
            return ScalarOutcome(Branch.DIRECT_BOOL_EXTRACTION_FAILURE, False, 0, scalar.prior)
        return ScalarOutcome(Branch.NAMED_EXTRACTION_FAILURE, False, 1, scalar.prior)
    _require(a.ordinary_string_extraction)
    text = scalar.text
    _ascii(text)
    if scalar.scalar_type in (ScalarType.INT32, ScalarType.UINT32):
        if not text:
            return ScalarOutcome(Branch.INTEGER_EMPTY, True, 1, KnownBits(0))
        _require(a.ascii_integer_classifier)
        return ScalarOutcome(Branch.INTEGER_PREFIX, True, 1, KnownBits(_integer(text)))
    if scalar.scalar_type is ScalarType.FLOAT32:
        _require(a.ieee_binary32_assignment)
        if scalar.atof_result is None:
            _unsupported(Reason.MISSING_PREMISE)
        return ScalarOutcome(Branch.FLOAT_SUPPLIED, True, 1,
                             KnownBits(_float_bits(scalar.atof_result.value)))
    _require(a.ascii_whole_string_comparison)
    word = text.lower()  # bytes.lower is ASCII whole-string, without stripping.
    if word == b"true":
        return ScalarOutcome(Branch.BOOL_TRUE_WORD, True, 1, KnownBits(1))
    if word == b"false":
        return ScalarOutcome(Branch.BOOL_FALSE_WORD, True, 1, KnownBits(0))
    if scalar.atoi_result is None:
        _unsupported(Reason.MISSING_PREMISE)
    return ScalarOutcome(Branch.BOOL_SUPPLIED_INTEGER, True, 1,
                         KnownBits(int(scalar.atoi_result.value != 0)))


def _assertion(expected):
    if type(expected) is not ExpectedAssertion or type(expected.identity) is not InputIdentity:
        _reject(Reason.ASSERTION)
    identity = expected.identity
    _sha(identity.content)
    if bind_input(identity.context, identity.scalar) != identity:
        _reject(Reason.IDENTITY)
    v = expected.outcome
    if (type(v) is not ScalarOutcome or type(v.branch) is not Branch
            or type(v.wrote) is not bool or type(v.return_value) is not int
            or v.return_value not in (0, 1)):
        _reject(Reason.ASSERTION)
    _value(v.value, identity.scalar.scalar_type)


def check_scalar(context: ConditionalContext, scalar: ScalarInput,
                 expected: ExpectedAssertion) -> ComparisonResult:
    """Compare one assertion bound to identical typed context/input/content.

    Unsupported conversion has no concrete outcome. Even a self-consistent
    caller identity and matching assertion authenticates no runtime evidence.
    Every returned result preserves the same eight unresolved domain IDs.
    """
    identity = None
    try:
        identity = bind_input(context, scalar)
        _assertion(expected)
        if expected.identity != identity:
            _reject(Reason.IDENTITY)
        outcome = _project(context, scalar)
        disposition = (Disposition.CONDITIONAL_MATCH if outcome == expected.outcome
                       else Disposition.CONDITIONAL_MISMATCH)
        return ComparisonResult(disposition, Reason.COMPARISON, identity, outcome)
    except InputRejected as rejected:
        return ComparisonResult(rejected.disposition, rejected.reason, identity)
