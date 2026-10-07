"""Handwritten synthetic scalar obligations; literal expectations precede code.

The tables contain numeric bits and explicit supplied library results, never a
local library oracle. Unknown outcomes deliberately have no invented bit value.
"""

# Type, label, present/extraction, text, prior bits, exact after bits, return.
PREIMAGE_CASES = (
    ("int", "absent", False, None, None, 73, 73, 0),
    ("int", "extract-failure", True, False, b"ignored", 73, 73, 1),
    ("int", "unknown-before", False, None, None, "unknown-before", "unknown-before", 0),
    ("uint", "absent", False, None, None, 73, 73, 0),
    ("uint", "extract-failure", True, False, b"ignored", 73, 73, 1),
    ("uint", "unknown-before", False, None, None, "unknown-before", "unknown-before", 0),
    ("float", "absent", False, None, None, 0x3fc00000, 0x3fc00000, 0),
    ("float", "extract-failure", True, False, b"ignored", 0x3fc00000, 0x3fc00000, 1),
    ("float", "unknown-before", False, None, None, "unknown-before", "unknown-before", 0),
    ("bool", "absent", False, None, None, 1, 1, 0),
    ("bool", "extract-failure", True, False, b"ignored", 1, 1, 1),
    ("bool", "unknown-before", False, None, None, "unknown-before", "unknown-before", 0),
)
DIRECT_FAILURE_CASES = ((1, 1, 0), ("unknown-before", "unknown-before", 0))

# Suffix, text, signed literal bits, unsigned literal bits. Base ten prefix only.
INTEGER_CASES = (
    ("empty", b"", 0, 0),
    ("zero", b"0", 0, 0),
    ("positive", b"27", 27, 27),
    ("negative", b"-27", 0xffffffe5, 4294967269),
    ("plus", b"+27", 27, 27),
    ("minus-one", b"-1", 0xffffffff, 4294967295),
    ("leading-zero", b"008", 8, 8),
    ("negative-zero", b"-0", 0, 0),
    ("sign-only", b"+", 0, 0),
    ("minus-only", b"-", 0, 0),
    ("malformed", b"garbage", 0, 0),
    ("decimal", b"12.75", 12, 12),
    ("negative-decimal", b"-12.75", 0xfffffff4, 4294967284),
    ("leading-decimal", b".75", 0, 0),
    ("exponent", b"1e3", 1, 1),
    ("hex-prefix", b"0x10", 0, 0),
    ("trailing-text", b"42tail", 42, 42),
    ("leading-space", b" 42", 42, 42),
    ("all-space", b" \t\r\n", 0, 0),
    ("max-safe", b"2147483647", 2147483647, 2147483647),
    ("min-safe", b"-2147483647", 0x80000001, 2147483649),
    ("above-float-exact-int", b"16777217", 16777217, 16777217),
)
INTEGER_UNSUPPORTED = (
    ("over-max", b"2147483648"),
    ("int-min", b"-2147483648"),
    ("uint-max", b"4294967295"),
    ("long", b"999999999999999999999"),
    ("non-ascii", b"\xc3\xa91"),
    ("nul", b"1\x002"),
)
# Suffix, text, explicit historical double premise, literal output bits.
FLOAT_CASES = (
    ("zero", b"0", 0.0, 0x00000000),
    ("one", b"1", 1.0, 0x3f800000),
    ("negative", b"-1", -1.0, 0xbf800000),
    ("dyadic", b"1.5", 1.5, 0x3fc00000),
    ("negative-dyadic", b"-1.5", -1.5, 0xbfc00000),
    ("exact-large", b"16777216", 16777216.0, 0x4b800000),
    ("empty", b"", 0.0, 0x00000000),
    ("malformed", b"garbage", 0.0, 0x00000000),
    ("prefix", b"1.5tail", 1.5, 0x3fc00000),
)
FLOAT_UNSUPPORTED = (
    ("decimal", b"0.1"), ("precision", b"16777217"),
    ("overflow", b"1e999"), ("underflow", b"1e-999"),
    ("comma", b"1,5"), ("nan", b"nan"), ("inf", b"inf"),
    ("empty-unconditioned", b""),
)
# Suffix, whole ASCII text, explicit atoi premise (None for word), prior, bits.
BOOL_CASES = (
    ("true", b"true", None, 0, 1),
    ("upper", b"TRUE", None, 0, 1),
    ("mixed", b"TrUe", None, 0, 1),
    ("false", b"false", None, 1, 0),
    ("false-upper", b"FALSE", None, 1, 0),
    ("zero", b"0", 0, 1, 0),
    ("one", b"1", 1, 0, 1),
    ("negative", b"-1", -1, 0, 1),
    ("decimal-zero", b"0.5", 0, 1, 0),
    ("decimal-one", b"1.5", 1, 0, 1),
    ("spaces", b" true ", 0, 1, 0),
    ("malformed", b"garbage", 0, 1, 0),
    ("empty", b"", 0, 1, 0),
)
BOOL_UNSUPPORTED = (
    ("range", b"2147483648"), ("locale", b"\xc4\xb0"),
    ("unconditioned", b"true"),
)
# Generic absence compositions carry supplied bits; they prove no constructors.
PRIOR_CASES = (
    ("float", 0x3f800000, 0x3f800000),
    ("int", 0xffffffff, 0xffffffff),
    ("bool", "unknown-before", "unknown-before"),
    ("int", "unknown-stack", "unknown-stack"),
    ("int", "unknown-stack", "unknown-stack"),
    ("float", 0x3f800000, 0x3f800000),
    ("float", 0x00000000, 0x00000000),
    ("uint", 0x00000000, 0x00000000),
)
# Original ten wrong conclusions, in bits, retain their exact value/return claim.
WRONG_CASES = (
    ("int-empty", 73, 1), ("int-decimal", 0, 1),
    ("int-exponent", 1000, 1), ("uint-minus-one", 0, 1),
    ("int-int-min", 0x80000000, 1),
    ("float-precision", 0x4b800000, 1),
    ("bool-spaces", 1, 1), ("bool-decimal-zero", 1, 1),
    ("bool-extract-failure", 1, 0), ("bool-unknown-before", 0, 0),
)
REVIEWED_FLOAT_CONTROLS = (
    (b"0.5", 0.5, 0x3f000000),
    (b"-0.5", -0.5, 0xbf000000),
    (b"0.25", 0.25, 0x3e800000),
    (b"2", 2.0, 0x40000000), (b"-2", -2.0, 0xc0000000),
    (b"0.75", 0.75, 0x3f400000), (b"-8", -8.0, 0xc1000000),
    (b"2.5", 2.5, 0x40200000),
    (b"16777216", 16777216.0, 0x4b800000),
    (b"16777218", 16777218.0, 0x4b800001),
    (b"0.000000059604644775390625", 0.000000059604644775390625, 0x33800000),
    (b"text-is-not-libc-proof", 4.0, 0x40800000),
)


from dataclasses import replace
import unittest

from smr_launcher import numeric_contracts as n


H1 = n.Sha256("1" * 64)
H2 = n.Sha256("2" * 64)
H3 = n.Sha256("3" * 64)
H4 = n.Sha256("4" * 64)
TYPES = {"int": n.ScalarType.INT32, "uint": n.ScalarType.UINT32,
         "float": n.ScalarType.FLOAT32, "bool": n.ScalarType.BOOL}
CONTEXT = n.ConditionalContext(
    H1, n.EvidenceIdentity(H2, H3, H4), n.Assumptions(True, True))


def value(bits):
    if bits == "unknown-before":
        return n.UnknownValue.BEFORE
    if bits == "unknown-stack":
        return n.UnknownValue.STACK
    return n.KnownBits(bits)


def scalar(typ="int", text=b"42", prior=73, mode="named", present=True,
           extraction=True, atof=None, atoi=None):
    return n.ScalarInput(
        n.Overload.NAMED if mode == "named" else n.Overload.DIRECT,
        TYPES[typ], n.Presence.PRESENT if present else n.Presence.ABSENT,
        (n.Extraction.NOT_ATTEMPTED if not present else
         n.Extraction.SUCCESS if extraction else n.Extraction.FAILURE),
        text, value(prior),
        None if atof is None else n.HistoricalDouble(atof, H4),
        None if atoi is None else n.HistoricalInteger(atoi, H4),
    )


def context(**premises):
    return replace(CONTEXT, assumptions=replace(CONTEXT.assumptions, **premises))


def outcome(branch, bits, ret=1, wrote=True):
    return n.ScalarOutcome(branch, wrote, ret, value(bits))


def expectation(ctx, inp, out):
    return n.ExpectedAssertion(n.bind_input(ctx, inp), out)


def original_cases():
    """Expand fixed literal tables; no production projection supplies outputs."""
    cases = []
    for typ, label, present, extraction, text, prior, after, ret in PREIMAGE_CASES:
        inp = scalar(typ, text, prior, present=present, extraction=extraction)
        branch = (n.Branch.NAMED_EXTRACTION_FAILURE if present else n.Branch.NAMED_ABSENT)
        cases.append((typ + "-" + label, CONTEXT, inp, outcome(branch, after, ret, False)))
    for suffix, text, signed, unsigned in INTEGER_CASES:
        empty = suffix == "empty"
        ctx = CONTEXT if empty else context(ascii_integer_classifier=True)
        branch = n.Branch.INTEGER_EMPTY if empty else n.Branch.INTEGER_PREFIX
        for typ, bits in (("int", signed), ("uint", unsigned)):
            cases.append((typ + "-" + suffix, ctx, scalar(typ, text), outcome(branch, bits)))
    for suffix, text in INTEGER_UNSUPPORTED:
        for typ in ("int", "uint"):
            cases.append((typ + "-" + suffix, context(ascii_integer_classifier=True),
                          scalar(typ, text), None))
    for suffix, text, supplied, bits in FLOAT_CASES:
        cases.append(("float-" + suffix, context(ieee_binary32_assignment=True),
                      scalar("float", text, 0x40000000, atof=supplied),
                      outcome(n.Branch.FLOAT_SUPPLIED, bits)))
    for suffix, text in FLOAT_UNSUPPORTED:
        cases.append(("float-" + suffix, CONTEXT, scalar("float", text, 0x40000000), None))
    for suffix, text, supplied, prior, bits in BOOL_CASES:
        branch = (n.Branch.BOOL_TRUE_WORD if suffix in ("true", "upper", "mixed")
                  else n.Branch.BOOL_FALSE_WORD if suffix in ("false", "false-upper")
                  else n.Branch.BOOL_SUPPLIED_INTEGER)
        for mode in ("named", "direct"):
            name = "bool-" + ("direct-" if mode == "direct" else "") + suffix
            cases.append((name, context(ascii_whole_string_comparison=True),
                          scalar("bool", text, prior, mode, atoi=supplied), outcome(branch, bits)))
    for prior, bits, ret in DIRECT_FAILURE_CASES:
        name = "bool-direct-" + ("no-current" if prior == 1 else "unknown-before")
        cases.append((name, CONTEXT, scalar("bool", None, prior, "direct", extraction=False),
                      outcome(n.Branch.DIRECT_BOOL_EXTRACTION_FAILURE, bits, ret, False)))
    for suffix, text in BOOL_UNSUPPORTED:
        cases.append(("bool-" + suffix, CONTEXT, scalar("bool", text, 1), None))
    return tuple(cases)


def unsupported_assertion(ctx, inp):
    # A well-typed concrete caller claim earns no match outside the domain.
    return expectation(ctx, inp, outcome(n.Branch.INTEGER_PREFIX, 0))


class NumericContractTests(unittest.TestCase):
    def compare(self, ctx, inp, out):
        expected = expectation(ctx, inp, out) if out is not None else unsupported_assertion(ctx, inp)
        return n.check_scalar(ctx, inp, expected)

    def test_original_124_obligations(self):
        cases = original_cases()
        self.assertEqual(len(cases), 116)
        for name, ctx, inp, expected in cases:
            with self.subTest(name=name):
                actual = self.compare(ctx, inp, expected)
                self.assertEqual(actual.unknowns, n.UNKNOWNS)
                if expected is None:
                    self.assertEqual(actual.disposition, n.Disposition.UNSUPPORTED)
                    self.assertIsNone(actual.outcome)
                else:
                    self.assertEqual(actual.disposition, n.Disposition.CONDITIONAL_MATCH)
                    self.assertEqual(actual.outcome, expected)
        self.assertEqual(len(PRIOR_CASES), 8)
        for index, (typ, prior, after) in enumerate(PRIOR_CASES):
            with self.subTest(prior=index):
                inp = scalar(typ, None, prior, present=False)
                expected = outcome(n.Branch.NAMED_ABSENT, after, 0, False)
                actual = self.compare(CONTEXT, inp, expected)
                self.assertEqual(actual.disposition, n.Disposition.CONDITIONAL_MATCH)
                self.assertEqual(actual.outcome, expected)

    def test_ten_wrong_conclusions(self):
        cases = {name: (ctx, inp, out) for name, ctx, inp, out in original_cases()}
        self.assertEqual(len(WRONG_CASES), 10)
        for name, wrong_bits, wrong_ret in WRONG_CASES:
            with self.subTest(name=name):
                ctx, inp, out = cases[name]
                wrong = (outcome(n.Branch.INTEGER_PREFIX, wrong_bits, wrong_ret) if out is None
                         else replace(out, value=value(wrong_bits), return_value=wrong_ret))
                actual = self.compare(ctx, inp, wrong)
                self.assertEqual(actual.disposition, n.Disposition.UNSUPPORTED if out is None
                                 else n.Disposition.CONDITIONAL_MISMATCH)

    def test_independently_reviewed_literal_float_bits(self):
        for text, supplied, bits in REVIEWED_FLOAT_CONTROLS:
            with self.subTest(text=text):
                ctx = context(ieee_binary32_assignment=True)
                inp = scalar("float", text, 0xc1200000, atof=supplied)
                actual = self.compare(ctx, inp, outcome(n.Branch.FLOAT_SUPPLIED, bits))
                self.assertEqual(actual.disposition, n.Disposition.CONDITIONAL_MATCH)
                self.assertEqual(actual.outcome.value, n.KnownBits(bits))

    def test_float_excluded_domains(self):
        for supplied in (-0.0, 2.0**-149, 2.0**-150, 0.1, 16777217.0,
                         float("inf"), float("-inf"), float("nan"), 1e300):
            with self.subTest(supplied=supplied):
                ctx = context(ieee_binary32_assignment=True)
                inp = scalar("float", b"unrelated", atof=supplied)
                actual = self.compare(ctx, inp, outcome(n.Branch.FLOAT_SUPPLIED, 0))
                self.assertEqual(actual.disposition, n.Disposition.UNSUPPORTED)
                self.assertEqual(actual.reason, n.Reason.FLOAT_DOMAIN)
                self.assertIsNone(actual.outcome)
        ctx = context(ieee_binary32_assignment=True)
        positive = scalar("float", b"unrelated", atof=0.0)
        negative = replace(positive, atof_result=n.HistoricalDouble(-0.0, H4))
        self.assertNotEqual(n.bind_input(ctx, positive).content, n.bind_input(ctx, negative).content)

    def test_word_priority_and_supplied_fallback(self):
        ctx = context(ascii_whole_string_comparison=True)
        rows = ((b"tRuE", 0, 1, n.Branch.BOOL_TRUE_WORD),
                (b"fAlSe", 99, 0, n.Branch.BOOL_FALSE_WORD),
                (b" true ", 0, 0, n.Branch.BOOL_SUPPLIED_INTEGER),
                (b"falsex", -2147483648, 1, n.Branch.BOOL_SUPPLIED_INTEGER),
                (b"0.9", 0, 0, n.Branch.BOOL_SUPPLIED_INTEGER),
                (b"no-atoi-lexical-rule", 2147483647, 1, n.Branch.BOOL_SUPPLIED_INTEGER))
        for mode in ("named", "direct"):
            for text, supplied, bits, branch in rows:
                with self.subTest(mode=mode, text=text):
                    inp = scalar("bool", text, 0, mode, atoi=supplied)
                    self.assertEqual(self.compare(ctx, inp, outcome(branch, bits)).disposition,
                                     n.Disposition.CONDITIONAL_MATCH)

    def test_exact_false_and_unknown_preservation(self):
        for prior in (0, "unknown-before", "unknown-stack"):
            inp = scalar("bool", b"\x00\xff", prior, extraction=False)
            out = outcome(n.Branch.NAMED_EXTRACTION_FAILURE, prior, 1, False)
            self.assertEqual(self.compare(CONTEXT, inp, out).outcome, out)
            changed_branch = replace(out, branch=n.Branch.DIRECT_BOOL_EXTRACTION_FAILURE)
            self.assertEqual(self.compare(CONTEXT, inp, changed_branch).disposition,
                             n.Disposition.CONDITIONAL_MISMATCH)
        # The false preimage equals a proposed false wrong value: excluded from
        # rejection credit. A wrong return is a separate discriminating control.
        inp = scalar("bool", b"ignored", 0, extraction=False)
        correct = outcome(n.Branch.NAMED_EXTRACTION_FAILURE, 0, 1, False)
        self.assertEqual(self.compare(CONTEXT, inp, correct).disposition,
                         n.Disposition.CONDITIONAL_MATCH)
        self.assertEqual(self.compare(CONTEXT, inp, replace(correct, return_value=0)).disposition,
                         n.Disposition.CONDITIONAL_MISMATCH)

    def test_explicit_premises_and_no_lexical_label_guess(self):
        ctx = context(ascii_integer_classifier=True)
        inp = scalar(text=b"42")
        expected = outcome(n.Branch.INTEGER_PREFIX, 42)
        for field in ("historical_contract", "ordinary_string_extraction", "ascii_integer_classifier"):
            for missing in (None, False):
                limited = replace(ctx, assumptions=replace(ctx.assumptions, **{field: missing}))
                self.assertEqual(self.compare(limited, inp, expected).disposition,
                                 n.Disposition.UNSUPPORTED)
        empty = scalar(text=b"")
        self.assertEqual(self.compare(CONTEXT, empty, outcome(n.Branch.INTEGER_EMPTY, 0)).disposition,
                         n.Disposition.CONDITIONAL_MATCH)
        for text in (b"1e999", b"1e9999", b"garbage", b"0.1", b""):
            actual = self.compare(context(ieee_binary32_assignment=True), scalar("float", text), None)
            self.assertEqual(actual.reason, n.Reason.MISSING_PREMISE)
            self.assertIsNone(actual.outcome)
        bool_ctx = context(ascii_whole_string_comparison=True)
        self.assertEqual(self.compare(bool_ctx, scalar("bool", b"0", 0), None).reason,
                         n.Reason.MISSING_PREMISE)

    def test_integer_boundaries_and_whitespace(self):
        ctx = context(ascii_integer_classifier=True)
        for text, bits in ((b"\t\r\n\v\f 17tail", 17), (b"+\t19", 0),
                           (b"--9", 0), (b"+-9", 0), (b"010", 10),
                           (b"0b11", 0), (b"-0xF", 0), (b"26E2", 26),
                           (b"26,9", 26), (b"26 9", 26),
                           (b"-2147483647.9", 0x80000001)):
            with self.subTest(text=text):
                self.assertEqual(self.compare(ctx, scalar(text=text),
                                              outcome(n.Branch.INTEGER_PREFIX, bits)).disposition,
                                 n.Disposition.CONDITIONAL_MATCH)
        for text in (b"1\x00tail", b"1\xff", b"2147483648tail", b"-2147483648"):
            self.assertEqual(self.compare(ctx, scalar(text=text), None).disposition,
                             n.Disposition.UNSUPPORTED)
        for typ in ("int", "uint", "float"):
            self.assertEqual(self.compare(CONTEXT, scalar(typ, None, 0, "direct", present=False),
                                          outcome(n.Branch.NAMED_ABSENT, 0, 0, False)).reason,
                             n.Reason.OVERLOAD)

    def test_bounds_before_conversion(self):
        ctx = context(ascii_integer_classifier=True)
        inp = scalar(text=b"0" * 4096)
        self.assertEqual(self.compare(ctx, inp, outcome(n.Branch.INTEGER_PREFIX, 0)).disposition,
                         n.Disposition.CONDITIONAL_MATCH)
        inp = scalar(text=b"0" * 4097)
        valid = scalar()
        expected = expectation(ctx, valid, outcome(n.Branch.INTEGER_PREFIX, 42))
        actual = n.check_scalar(ctx, inp, expected)
        self.assertEqual((actual.disposition, actual.reason),
                         (n.Disposition.UNSUPPORTED, n.Reason.BUDGET))
        self.assertIsNone(actual.identity)
        with self.assertRaises(n.InputRejected):
            n.digest_text(bytearray(b"0"))

    def test_strict_types_and_states(self):
        ctx = context(ascii_integer_classifier=True)
        inp = scalar()
        expected = expectation(ctx, inp, outcome(n.Branch.INTEGER_PREFIX, 42))
        invalid_inputs = (
            {}, replace(inp, overload="named"), replace(inp, scalar_type="int32"),
            replace(inp, presence=True), replace(inp, extraction=True),
            replace(inp, text="42"), replace(inp, text=bytearray(b"42")),
            replace(inp, prior=None), replace(inp, prior=False),
            replace(inp, prior=n.KnownBits(True)), replace(inp, prior=n.KnownBits(-1)),
            replace(inp, prior=n.KnownBits(2**32)),
            replace(inp, prior="unknown-before"),
            replace(inp, presence=n.Presence.ABSENT),
            replace(inp, text=None), replace(inp, extraction=n.Extraction.NOT_ATTEMPTED),
            replace(inp, atof_result=n.HistoricalDouble(1.0, H4)),
        )
        for bad in invalid_inputs:
            with self.subTest(bad=bad):
                self.assertEqual(n.check_scalar(ctx, bad, expected).disposition, n.Disposition.INVALID)
        invalid_contexts = (
            {}, replace(ctx, identity="1" * 64), replace(ctx, identity=n.Sha256("A" * 64)),
            replace(ctx, identity=n.Sha256("1" * 63)),
            replace(ctx, evidence={}), replace(ctx, assumptions={}),
            replace(ctx, assumptions=replace(ctx.assumptions, historical_contract=1)),
            replace(ctx, evidence=replace(ctx.evidence, build=n.Sha256(True))),
        )
        for bad in invalid_contexts:
            with self.subTest(bad_context=bad):
                self.assertEqual(n.check_scalar(bad, inp, expected).disposition, n.Disposition.INVALID)
        for supplied in (True, 1, "1.0"):
            f = scalar("float", atof=1.0)
            f = replace(f, atof_result=n.HistoricalDouble(supplied, H4))
            self.assertEqual(n.check_scalar(ctx, f, expected).disposition, n.Disposition.INVALID)
        for supplied in (True, 1.0, 2147483648, -2147483649):
            b = scalar("bool", prior=0, atoi=0)
            b = replace(b, atoi_result=n.HistoricalInteger(supplied, H4))
            self.assertEqual(n.check_scalar(ctx, b, expected).disposition, n.Disposition.INVALID)
        self.assertEqual(n.check_scalar(ctx, scalar("bool", prior=2), expected).disposition,
                         n.Disposition.INVALID)

    def test_identity_binds_every_typed_component(self):
        ctx = context(ascii_integer_classifier=True)
        inp = scalar()
        out = outcome(n.Branch.INTEGER_PREFIX, 42)
        expected = expectation(ctx, inp, out)
        variations = (
            (replace(ctx, identity=H2), inp),
            (replace(ctx, evidence=replace(ctx.evidence, contract=H2)), inp),
            (replace(ctx, evidence=replace(ctx.evidence, build=H1)), inp),
            (replace(ctx, evidence=replace(ctx.evidence, review=H1)), inp),
            (replace(ctx, assumptions=replace(ctx.assumptions, ieee_binary32_assignment=True)), inp),
            (ctx, replace(inp, text=b"42tail")), (ctx, replace(inp, prior=n.KnownBits(74))),
            (ctx, replace(inp, scalar_type=n.ScalarType.UINT32)),
            (ctx, replace(inp, extraction=n.Extraction.FAILURE)),
            (ctx, replace(inp, overload=n.Overload.DIRECT)),
            (ctx, scalar(text=None, present=False)),
        )
        for changed_context, changed_input in variations:
            with self.subTest(context=changed_context, inp=changed_input):
                self.assertNotEqual(n.bind_input(changed_context, changed_input).content,
                                    expected.identity.content)
                actual = n.check_scalar(changed_context, changed_input, expected)
                self.assertEqual((actual.disposition, actual.reason),
                                 (n.Disposition.INVALID, n.Reason.IDENTITY))
        forged = replace(expected, identity=replace(expected.identity, content=H1))
        self.assertEqual(n.check_scalar(ctx, inp, forged).reason, n.Reason.IDENTITY)
        bool_ctx = context(ascii_whole_string_comparison=True)
        a = scalar("bool", b"true", 0, atoi=0)
        b = replace(a, atoi_result=n.HistoricalInteger(1, H4))
        c = replace(a, atoi_result=n.HistoricalInteger(0, H3))
        self.assertNotEqual(n.bind_input(bool_ctx, a).content, n.bind_input(bool_ctx, b).content)
        self.assertNotEqual(n.bind_input(bool_ctx, a).content, n.bind_input(bool_ctx, c).content)
        f = scalar("float", atof=1.0)
        self.assertNotEqual(n.bind_input(ctx, f).content,
                            n.bind_input(ctx, replace(f, atof_result=n.HistoricalDouble(1.0, H3))).content)

    def test_strict_assertion_and_fixed_result_vocabulary(self):
        ctx = context(ascii_integer_classifier=True)
        inp = scalar()
        out = outcome(n.Branch.INTEGER_PREFIX, 42)
        expected = expectation(ctx, inp, out)
        for bad in ({}, replace(expected, identity={}), replace(expected, outcome={}),
                    replace(expected, outcome=replace(out, wrote=1)),
                    replace(expected, outcome=replace(out, return_value=True)),
                    replace(expected, outcome=replace(out, return_value=2)),
                    replace(expected, outcome=replace(out, branch="integer-prefix")),
                    replace(expected, outcome=replace(out, value=n.KnownBits(True)))):
            self.assertEqual(n.check_scalar(ctx, inp, bad).disposition, n.Disposition.INVALID)
        self.assertEqual(tuple(d.value for d in n.Disposition),
                         ("conditional-match", "conditional-mismatch", "unsupported", "invalid"))
        self.assertEqual(n.UNKNOWNS, ("U_XML", "U_STRINGS", "U_NUMERIC", "U_DEFAULT_LISTS",
                                     "U_LOCALE", "U_CONTAINERS", "U_PROVIDER", "U_RUNTIME"))
        for result in (self.compare(ctx, inp, out),
                       self.compare(ctx, inp, replace(out, value=n.KnownBits(0))),
                       self.compare(CONTEXT, inp, None), n.check_scalar(ctx, {}, expected)):
            self.assertIs(type(result.reason), n.Reason)
            self.assertEqual(result.unknowns, n.UNKNOWNS)
            self.assertFalse(hasattr(result, "engine_verified"))
        # A caller can choose mutually consistent evidence hashes; the only
        # possible positive disposition is still explicitly conditional.
        self.assertEqual(self.compare(ctx, inp, out).disposition.value, "conditional-match")


if __name__ == "__main__":
    unittest.main()
