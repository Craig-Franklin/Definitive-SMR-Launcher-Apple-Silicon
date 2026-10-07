"""Handwritten synthetic assertions; no private corpus, tables, or parser oracle."""

from copy import deepcopy
from dataclasses import replace

import functools
import unittest
from xml.parsers import expat

from smr_launcher.compatibility_contracts import (
    Assumptions, ByteReader, ComparisonResult, ContractIdentity, Disposition,
    ExpectedProjection, InputIdentity, Limits, ProjectionContext, ReaderIdentity,
    ReaderState, ReviewedTable, SCHEMA, STRING_FIELDS, Sha256, TableIdentity,
    UNKNOWNS, compare_projection, digest, table_digest,
)


def cases(names, values):
    """Attach explicit cases to standard-library unittest discovery."""
    def decorate(function):
        function.fixed_cases = (names.split(","), values)
        return function
    return decorate


# Explicitly synthetic table and provenance, unrelated to any game build/table.
NAMES = ("Synthetic Birch", "Synthetic Cedar", "Synthetic Alder")
BUILD = digest(b"synthetic build identity")
TABLE_ID = TableIdentity(BUILD, digest(b"synthetic table review"), table_digest(NAMES))
TABLE = ReviewedTable(TABLE_ID, NAMES)
CONTRACT = ContractIdentity(
    SCHEMA, "synthetic-v1", BUILD, digest(b"synthetic contract review"),
    digest(b"synthetic field review"), TABLE_ID, 3,
)
ASSUMPTIONS = Assumptions(True, True, True, True, True, True)
CONTEXT = ProjectionContext(digest(b"synthetic conditional context"), CONTRACT, TABLE_ID, ASSUMPTIONS)
EMPTY = {"records": [], "accepted_ids": [], "pruned": []}


def reader(payload=None, state=ReaderState.ABSENT):
    return ByteReader(ReaderIdentity(state, None if payload is None else digest(payload)), payload)


def xml(contents):
    return ("<RRTIndustries><Industries>" + contents + "</Industries></RRTIndustries>").encode("ascii")


def industry(name, contents=""):
    return "<RRTIndustry><szName>" + name + "</szName>" + contents + "</RRTIndustry>"


def record(name, *, origin="base", touched=False, model=None, secondary=None,
           production=None, display=None, locations=None):
    # Expected defaults are explicit markers; no projection code computes them.
    strings = {f: {"unknown": "new-record-default"} for f in STRING_FIELDS}
    if model is not None:
        strings["szModel"] = model
    if secondary is not None:
        strings["szSecondaryModel"] = secondary
    return {
        "key": name, "constructed_in": origin, "scenario_touched": touched,
        "strings": strings,
        "production": {"unknown": "new-record-default"} if production is None else production,
        "display_names": {"unknown": "new-record-default"} if display is None else display,
        "locations": {"unknown": "new-record-default"} if locations is None else locations,
    }


def expectation(records, ids, pruned=()):
    return {"records": records, "accepted_ids": [{"key": k, "id": i} for k, i in ids], "pruned": list(pruned)}


def arguments(base_bytes=None, scenario_bytes=None, expected=EMPTY, *, state=None):
    base = reader(xml("") if base_bytes is None else base_bytes, ReaderState.VALID)
    scenario = reader(scenario_bytes, state or (ReaderState.ABSENT if scenario_bytes is None else ReaderState.VALID))
    identity = InputIdentity(CONTEXT, base.identity, scenario.identity)
    return dict(base=base, scenario=scenario, contract=CONTRACT, table=TABLE,
                context=CONTEXT, expected=ExpectedProjection(identity, expected))


def result(*args, **kwargs):
    return compare_projection(**arguments(*args, **kwargs))


def assert_match(out):
    assert type(out) is ComparisonResult
    assert out.disposition is Disposition.CONDITIONAL_MATCH
    assert out.unknowns == UNKNOWNS
    assert len(out.unknowns) == 8


def test_empty_control_and_dense_table_order():
    assert_match(result())
    raw = xml(industry(NAMES[2], "<szModel>alder</szModel>") + industry(NAMES[0], "<szModel>birch</szModel>"))
    fixed = expectation([record(NAMES[2], model="alder"), record(NAMES[0], model="birch")], [(NAMES[0], 0), (NAMES[2], 1)])
    assert_match(result(raw, expected=fixed))
    final = expectation([record(NAMES[2])], [(NAMES[2], 0)])
    assert_match(result(xml(industry(NAMES[2])), expected=final))


BASE = xml(industry(NAMES[0], "<szModel>base</szModel><szSecondaryModel>kept</szSecondaryModel>"
                    "<Production><Resource><Input>source</Input><Output>target</Output></Resource></Production>"
                    "<DisplayNames><szDisplayName>first</szDisplayName><szDisplayName>second</szDisplayName></DisplayNames>"
                    "<Locations><Location><InCity>town</InCity></Location></Locations>")
           + industry(NAMES[1], "<szModel>cedar</szModel>"))
INHERITED = record(NAMES[0], touched=True, model="base", secondary="kept",
                   production=[{"Input": "source", "Output": "target"}], display=["first", "second"], locations=["town"])


def test_absent_inherits_and_empty_container_does_not_clear():
    fixed = expectation([INHERITED], [(NAMES[0], 0)], [NAMES[1]])
    assert_match(result(BASE, xml(industry(NAMES[0])), fixed))
    changed = deepcopy(INHERITED)
    changed["strings"]["szSecondaryModel"] = ""
    fixed = expectation([changed], [(NAMES[0], 0)], [NAMES[1]])
    assert_match(result(BASE, xml(industry(NAMES[0], "<szSecondaryModel/><Production/><DisplayNames/><Locations/>")), fixed))


def test_first_entry_clears_all_three_lists_with_literal_empty_values():
    changed = deepcopy(INHERITED)
    changed.update(production=[{"Input": "", "Output": ""}], display_names=[""], locations=[""])
    fixed = expectation([changed], [(NAMES[0], 0)], [NAMES[1]])
    scenario = xml(industry(NAMES[0], "<Production><Resource><Input/><Output/></Resource></Production>"
                           "<DisplayNames><szDisplayName/></DisplayNames><Locations><Location><InCity/></Location></Locations>"))
    assert_match(result(BASE, scenario, fixed))


def test_first_direct_scalar_container_and_path_without_backtracking():
    changed = deepcopy(INHERITED)
    changed["strings"]["szModel"] = "selected"
    fixed = expectation([changed], [(NAMES[0], 0)], [NAMES[1]])
    scenario = xml(industry(NAMES[0], "<Wrapper><szModel>decoy</szModel></Wrapper>"
                           "<szModel>selected</szModel><szModel>late</szModel>"
                           "<Production/><Production><Resource><Input>late</Input></Resource></Production>"))
    assert_match(result(BASE, scenario, fixed))
    base_records = [deepcopy(INHERITED), record(NAMES[1], model="cedar")]
    base_records[0]["scenario_touched"] = False
    unchanged = expectation(base_records, [(NAMES[0], 0), (NAMES[1], 1)])
    path = b"<RRTIndustries><Industries/><Industries><RRTIndustry><szName>Synthetic Birch</szName></RRTIndustry></Industries></RRTIndustries>"
    assert_match(result(BASE, path, unchanged))


def test_exact_reuse_unknown_names_missing_key_and_defaults():
    duplicate = xml(industry(NAMES[0], "<szModel>early</szModel><szSecondaryModel>kept</szSecondaryModel>")
                    + industry(NAMES[0], "<szModel>late</szModel>"))
    fixed = expectation([record(NAMES[0], model="late", secondary="kept")], [(NAMES[0], 0)])
    assert_match(result(duplicate, expected=fixed))
    assert_match(result(xml("<RRTIndustry><szModel>nameless</szModel></RRTIndustry>"),
                        expected=expectation([record("", model="nameless")], [])))
    scenario = xml(industry("synthetic birch") + industry("Synthetic Unknown"))
    fixed = expectation([record("synthetic birch", origin="scenario", touched=True),
                         record("Synthetic Unknown", origin="scenario", touched=True)], [], [NAMES[0], NAMES[1]])
    assert_match(result(BASE, scenario, fixed))
    fixed = expectation([record(NAMES[2], origin="scenario", touched=True)], [(NAMES[2], 0)], [NAMES[0], NAMES[1]])
    assert_match(result(BASE, xml(industry(NAMES[2])), fixed))


def test_unfiltered_suffix_includes_other_elements_and_retained_text():
    raw = xml(industry(NAMES[0], "<Production>prefix<Resource><Input>a</Input><Output>b</Output></Resource> \n"
                      "<Other><Input>c</Input><Output>d</Output></Other></Production>"
                      "<Locations>prefix<Location><InCity>one</InCity></Location>\t<Other><InCity>two</InCity></Other></Locations>"))
    fixed = expectation([record(NAMES[0], production=[{"Input": "a", "Output": "b"}, {"Input": "", "Output": ""}, {"Input": "c", "Output": "d"}],
                               locations=["one", "", "two"])], [(NAMES[0], 0)])
    assert_match(result(raw, expected=fixed))


@cases("separator", ["<Other>stop</Other>", " ", "\n", "\r\n", "\t"])
def test_display_run_stops_at_immediate_element_or_text(separator):
    raw = xml(industry(NAMES[0], "<DisplayNames>prefix<szDisplayName>a</szDisplayName>" + separator + "<szDisplayName>b</szDisplayName></DisplayNames>"))
    fixed = expectation([record(NAMES[0], display=["a"])], [(NAMES[0], 0)])
    assert_match(result(raw, expected=fixed))


def test_literal_spaces_and_line_endings_preserved():
    raw = xml(" \n" + industry(NAMES[0], "<szModel> a\r\nb\t </szModel><DisplayNames><szDisplayName>x</szDisplayName><szDisplayName>x</szDisplayName></DisplayNames>"))
    fixed = expectation([record(NAMES[0], model=" a\r\nb\t ", display=["x", "x"])], [(NAMES[0], 0)])
    assert_match(result(raw, expected=fixed))


@cases("field", STRING_FIELDS)
def test_each_reviewed_scalar_retains_absent_and_clears_present_empty(field):
    raw = xml(industry(NAMES[0], "<" + field + ">retained</" + field + ">"))
    inherited = record(NAMES[0], touched=True)
    inherited["strings"][field] = "retained"
    fixed = expectation([inherited], [(NAMES[0], 0)])
    assert_match(result(raw, xml(industry(NAMES[0])), fixed))
    cleared = deepcopy(inherited)
    cleared["strings"][field] = ""
    assert_match(result(raw, xml(industry(NAMES[0], "<" + field + "/>")),
                        expectation([cleared], [(NAMES[0], 0)])))


@cases("field", ["historical_provenance", "distinct_readers", "ordinary_ascii_strings",
                 "successful_containers", "stipulated_ordered_nodes", "unknown_new_defaults"])
def test_each_missing_assumption_prevents_match(field):
    args = arguments()
    context = replace(CONTEXT, assumptions=replace(ASSUMPTIONS, **{field: None}))
    args["context"] = context
    args["expected"] = replace(args["expected"], identity=replace(args["expected"].identity, context=context))
    assert compare_projection(**args).disposition is Disposition.UNSUPPORTED


def test_depth_and_string_budget_exact_boundaries():
    model = "x" * 4096
    raw = xml(industry(NAMES[0], "<szModel>" + model + "</szModel>"))
    assert_match(result(raw, expected=expectation([record(NAMES[0], model=model)], [(NAMES[0], 0)])))
    # Root/container/record + 29 nested structural decoys is exactly depth32.
    raw = xml(industry(NAMES[0], "<Wrapper>" * 29 + "</Wrapper>" * 29))
    assert_match(result(raw, expected=expectation([record(NAMES[0])], [(NAMES[0], 0)])))
    too_deep = xml(industry(NAMES[0], "<Wrapper>" * 30 + "</Wrapper>" * 30))
    assert result(too_deep).disposition is Disposition.UNSUPPORTED


def test_explicit_full_synthetic_table_dense_identity_control():
    # No archived registry membership is used, including at the table boundary.
    names = tuple("Synthetic Slot " + str(i) for i in range(36))
    tid = replace(TABLE_ID, content=table_digest(names))
    table = ReviewedTable(tid, names)
    contract = replace(CONTRACT, table=tid, table_size=36)
    context = replace(CONTEXT, contract=contract, table=tid)
    raw = xml("".join(industry(name) for name in reversed(names)))
    fixed = expectation([record(name) for name in reversed(names)], list(zip(names, range(36))))
    args = arguments(raw, expected=fixed)
    args.update(table=table, contract=contract, context=context)
    args["expected"] = replace(args["expected"], identity=replace(args["expected"].identity, context=context))
    assert_match(compare_projection(**args))


@cases("scenario,state", [(None, ReaderState.ABSENT), (None, ReaderState.INVALID), (b"broken unread bytes", ReaderState.INVALID), (xml(""), ReaderState.VALID), (b"<RRTIndustries/>", ReaderState.VALID)])
def test_reader_guards_and_valid_path_absence_disable_pruning(scenario, state):
    fixed = expectation([record(NAMES[2])], [(NAMES[2], 0)])
    assert_match(result(xml(industry(NAMES[2])), scenario, fixed, state=state))


@cases("payload", [b"", b"<RRTIndustries>", b"<RRTIndustries></Industries>", b"<RRTIndustries/><RRTIndustries/>", b"text<RRTIndustries/>", b"<RRTIndustries/><", b"<1bad/>"])
def test_malformed_checker_policy(payload):
    out = result(payload)
    assert out.disposition is Disposition.INVALID
    assert out.projection is None
    assert out.unknowns == UNKNOWNS


@cases("payload", [
    b"<!DOCTYPE RRTIndustries><RRTIndustries/>", b"<!--text--><RRTIndustries/>",
    b'<?xml version="1.0"?><RRTIndustries/>', b'<RRTIndustries xmlns="synthetic"/>',
    b"<s:RRTIndustries/>", b"<RRTIndustries>&amp;</RRTIndustries>",
    b"<RRTIndustries>\xff</RRTIndustries>", b"<RRTIndustries>\x00</RRTIndustries>",
    xml(industry(NAMES[0], "<szModel>a<Wrapper/>b</szModel>")),
    xml(industry(NAMES[0], "<iCost>7</iCost>")), xml(industry(NAMES[0], "<fScale/>")),
    xml(industry(NAMES[0], "<bInCity/>")), xml(industry(NAMES[0], "<szIcon/>")),
    xml(industry(NAMES[0], "<Production><Resource><InputOutputRatio/></Resource></Production>")),
    xml(industry(NAMES[0], "<DisplayNames><EnglishOnly/></DisplayNames>")),
    xml(industry(NAMES[0], "<Locations><Location><StartX/></Location></Locations>")),
    xml("<Other/>"), xml("uncovered"), xml(industry(NAMES[0]) + "\n"),
    xml(industry(NAMES[0]) + "<Other/>"), xml(industry(NAMES[0], "<Unreviewed/>")),
])
def test_explicit_unsupported_domain(payload):
    out = result(payload)
    assert out.disposition is Disposition.UNSUPPORTED
    assert out.projection is None


@cases("field,limit,payload", [
    ("bytes_per_reader", 8, xml("")), ("depth", 1, xml("")), ("nodes", 2, xml("")),
    ("string_bytes", 14, xml(industry(NAMES[0], "<szModel>" + "x" * 15 + "</szModel>"))),
    ("total_string_bytes", 60, xml(industry(NAMES[0]) * 4)),
    ("records", 1, xml(industry(NAMES[0]) * 2)),
])
def test_all_reader_budgets(field, limit, payload):
    args = arguments(payload)
    args["limits"] = replace(Limits(), **{field: limit})
    assert compare_projection(**args).disposition is Disposition.UNSUPPORTED


@cases("limits", [Limits(depth=0), Limits(nodes=True), Limits(depth=33), Limits(table_entries=0)])
def test_limits_cannot_exceed_hard_caps_or_use_bool(limits):
    assert compare_projection(**arguments(), limits=limits).disposition is Disposition.INVALID


def test_record_budget_counts_reused_records_across_both_readers():
    args = arguments(xml(industry(NAMES[0])), xml(industry(NAMES[0])))
    assert compare_projection(**args, limits=Limits(records=1)).disposition is Disposition.UNSUPPORTED


@cases("bad", [None, False, 1, "true"])
def test_assumption_is_explicit_bool_and_required(bad):
    args = arguments()
    context = replace(CONTEXT, assumptions=replace(ASSUMPTIONS, ordinary_ascii_strings=bad))
    args["context"] = context
    args["expected"] = replace(args["expected"], identity=replace(args["expected"].identity, context=context))
    disposition = Disposition.UNSUPPORTED if bad is None or bad is False else Disposition.INVALID
    assert compare_projection(**args).disposition is disposition


@cases("mutation", ["hash", "state", "payload", "absent-bytes", "alias", "base-invalid", "base-absent", "expectation-context", "expectation-bytes", "field-set", "contract-schema", "contract-bool", "table-order", "table-duplicate", "table-size", "table-build", "table-digest", "untyped-context", "untyped-hash"])
def test_identity_fail_closed(mutation):
    args = arguments()
    if mutation == "hash":
        args["base"] = replace(args["base"], identity=replace(args["base"].identity, content=digest(b"wrong")))
    elif mutation == "state":
        args["base"] = replace(args["base"], identity=replace(args["base"].identity, state="valid"))
    elif mutation == "payload":
        args["base"] = replace(args["base"], payload="<RRTIndustries/>")
    elif mutation == "absent-bytes":
        args["scenario"] = reader(b"bytes", ReaderState.ABSENT)
    elif mutation == "alias":
        args["scenario"] = args["base"]
    elif mutation in ("base-invalid", "base-absent"):
        args["base"] = reader(None, ReaderState.INVALID if mutation == "base-invalid" else ReaderState.ABSENT)
    elif mutation == "expectation-context":
        args["expected"] = replace(args["expected"], identity=replace(args["expected"].identity, context=replace(CONTEXT, identity=digest(b"other context"))))
    elif mutation == "expectation-bytes":
        args["expected"] = replace(args["expected"], identity=replace(args["expected"].identity, base=ReaderIdentity(ReaderState.VALID, digest(b"other bytes"))))
    elif mutation == "field-set":
        args["contract"] = replace(CONTRACT, string_fields=("szModel",))
    elif mutation == "contract-schema":
        args["contract"] = replace(CONTRACT, schema="other")
    elif mutation == "contract-bool":
        args["contract"] = replace(CONTRACT, table_size=True)
    elif mutation == "table-order":
        args["table"] = replace(TABLE, names=tuple(reversed(NAMES)))
    elif mutation == "table-duplicate":
        args["table"] = replace(TABLE, names=(NAMES[0], NAMES[0], NAMES[2]))
    elif mutation == "table-size":
        args["table"] = replace(TABLE, names=NAMES[:2])
    elif mutation == "table-build":
        args["table"] = replace(TABLE, identity=replace(TABLE_ID, build=digest(b"other build")))
    elif mutation == "table-digest":
        args["table"] = replace(TABLE, identity=replace(TABLE_ID, content=digest(b"wrong table")))
    elif mutation == "untyped-context":
        args["context"] = {}
    else:
        args["base"] = replace(args["base"], identity=replace(args["base"].identity, content=Sha256("A" * 64)))
    expected = Disposition.UNSUPPORTED if mutation in ("base-invalid", "base-absent") else Disposition.INVALID
    out = compare_projection(**args)
    assert out.disposition is expected
    assert out.projection is None


def test_table_reordering_requires_new_binding_and_changes_dense_ids():
    names = tuple(reversed(NAMES))
    tid = replace(TABLE_ID, content=table_digest(names))
    table = ReviewedTable(tid, names)
    contract = replace(CONTRACT, table=tid)
    context = replace(CONTEXT, table=tid, contract=contract)
    raw = xml(industry(NAMES[0]) + industry(NAMES[2]))
    fixed = expectation([record(NAMES[0]), record(NAMES[2])], [(NAMES[2], 0), (NAMES[0], 1)])
    args = arguments(raw, expected=fixed)
    args.update(table=table, contract=contract, context=context)
    args["expected"] = replace(args["expected"], identity=replace(args["expected"].identity, context=context))
    assert_match(compare_projection(**args))


@cases("part", ["model", "list", "empty", "unknown", "origin", "touched", "id", "extra", "bool-id", "cycle"])
def test_planted_output_errors_and_malformed_expectations(part):
    raw = xml(industry(NAMES[0], "<szModel>right</szModel>"))
    fixed = expectation([record(NAMES[0], model="right")], [(NAMES[0], 0)])
    assert_match(result(raw, expected=fixed))
    wrong = deepcopy(fixed)
    malformed = part in ("id", "extra", "bool-id")
    if part == "model":
        wrong["records"][0]["strings"]["szModel"] = "wrong"
    elif part == "list":
        wrong["records"][0]["production"] = []
    elif part == "empty":
        wrong["records"][0]["strings"]["szModel"] = ""
    elif part == "unknown":
        wrong["records"][0]["strings"]["szSecondaryModel"] = ""
    elif part in ("origin", "touched"):
        wrong["records"][0]["constructed_in" if part == "origin" else "scenario_touched"] = "scenario" if part == "origin" else True
    elif part == "id":
        wrong["accepted_ids"][0]["id"] = 8
    elif part == "extra":
        wrong["engine_verdict"] = "invented"
    elif part == "bool-id":
        wrong["accepted_ids"][0]["id"] = False
    else:
        wrong["records"].append(wrong)
    out = result(raw, expected=wrong)
    assert out.disposition is (Disposition.UNSUPPORTED if part == "cycle" else Disposition.INVALID if malformed else Disposition.CONDITIONAL_MISMATCH)


def test_wrong_bytes_require_explicit_rebinding_and_still_mismatch():
    original = xml(industry(NAMES[0], "<szModel>right</szModel>"))
    wrong = xml(industry(NAMES[0], "<szModel>wrong</szModel>"))
    fixed = expectation([record(NAMES[0], model="right")], [(NAMES[0], 0)])
    args = arguments(original, expected=fixed)
    args["base"] = reader(wrong, ReaderState.VALID)
    assert compare_projection(**args).disposition is Disposition.INVALID
    assert result(wrong, expected=fixed).disposition is Disposition.CONDITIONAL_MISMATCH


def test_hash_helpers_reject_implicit_decoding_and_table_has_fixed_encoding():
    assert table_digest(("a", "b")) == digest(b'["a","b"]')
    with unittest.TestCase().assertRaises(TypeError):
        digest("bytes")
    with unittest.TestCase().assertRaises(TypeError):
        table_digest(["a"])


def assert_wellformed(raw, valid):
    # Independent XML lexical control only; no parsed values supply expectations.
    parser = expat.ParserCreate()
    if valid:
        parser.Parse(raw, True)
    else:
        with unittest.TestCase().assertRaises(expat.ExpatError):
            parser.Parse(raw, True)


@cases("field,scenario", [(field, scenario) for field in STRING_FIELDS
                          for scenario in (False, True)])
def test_literal_cdata_close_rejects_each_scalar_in_either_reader(field, scenario):
    raw = xml(industry(NAMES[0], "<" + field + ">]]></" + field + ">"))
    assert_wellformed(raw, False)
    fixed_record = record(NAMES[0], origin="scenario" if scenario else "base",
                          touched=scenario)
    fixed_record["strings"][field] = "]]>"
    fixed = expectation([fixed_record], [(NAMES[0], 0)])
    out = result(xml("") if scenario else raw, raw if scenario else None, fixed)
    assert out.disposition is Disposition.INVALID
    assert out.identity is None and out.projection is None
    assert out.unknowns == UNKNOWNS


@cases("raw", [
    b"<RRTIndustries>]]></RRTIndustries>",
    xml("<RRTIndustry>]]></RRTIndustry>"),
    xml(industry(NAMES[0], "<Wrapper>]]></Wrapper>")),
    xml(industry(NAMES[0], "<szModel>first</szModel><szModel>]]></szModel>")),
    xml(industry(NAMES[0], "<szName>]]></szName>")),
    xml(industry(NAMES[0], "<Production/><Production>]]></Production>")),
    xml(industry(NAMES[0], "<DisplayNames/><DisplayNames>]]></DisplayNames>")),
    xml(industry(NAMES[0], "<Locations/><Locations>]]></Locations>")),
    xml(industry(NAMES[0], "<Production>]]><Resource/></Production>")),
    xml(industry(NAMES[0], "<Production><Resource/>]]></Production>")),
    xml(industry(NAMES[0], "<DisplayNames><szDisplayName>first</szDisplayName>]]>"
                 "<szDisplayName>late</szDisplayName></DisplayNames>")),
    xml(industry(NAMES[0], "<Locations><Location/>]]></Locations>")),
    b"<RRTIndustries><Industries/><Industries>]]></Industries></RRTIndustries>",
])
def test_literal_cdata_close_rejects_unselected_structural_and_list_text(raw):
    assert_wellformed(raw, False)
    for scenario in (False, True):
        out = result(xml("") if scenario else raw, raw if scenario else None)
        assert out.disposition is Disposition.INVALID
        assert out.identity is None and out.projection is None
        assert out.unknowns == UNKNOWNS


@cases("literal", ["] >", "]] >", "]>", "]]\r>", "]]\n>", "]]\t>",
                   "]\r]>", "]\n]>", "]\t]>"])
def test_nearby_valid_literals_preserve_exact_scalar_and_list_text(literal):
    raw = xml(industry(NAMES[0], "<szModel>" + literal + "</szModel>"
                      "<Production><Resource/><Other><Input>" + literal
                      + "</Input></Other></Production>"
                      "<DisplayNames><szDisplayName>" + literal
                      + "</szDisplayName><szDisplayName>" + literal
                      + "</szDisplayName></DisplayNames>"
                      "<Locations><Location><InCity>" + literal
                      + "</InCity></Location></Locations>"))
    assert_wellformed(raw, True)
    fixed = expectation([record(NAMES[0], model=literal,
                                production=[{"Input": "", "Output": ""},
                                            {"Input": literal, "Output": ""}],
                                display=[literal, literal], locations=[literal])],
                        [(NAMES[0], 0)])
    assert_match(result(raw, expected=fixed))
    root = ("<RRTIndustries>" + literal + "</RRTIndustries>").encode("ascii")
    assert_wellformed(root, True)
    assert_match(result(root))


@cases("construct", ["<![CDATA[ordinary]]>", "<!--ordinary-->", "<?synthetic ordinary?>",
                     "<szModel>&amp;</szModel>", "<x:szModel/>"])
def test_lexical_correction_retains_unsupported_construct_domain(construct):
    out = result(xml(industry(NAMES[0], construct)))
    assert out.disposition is Disposition.UNSUPPORTED
    assert out.identity is None and out.projection is None


def load_tests(loader, suite, pattern):
    focused = unittest.TestSuite()
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        if not hasattr(function, "fixed_cases"):
            focused.addTest(unittest.FunctionTestCase(function, description=name))
            continue
        names, values = function.fixed_cases
        for index, values_row in enumerate(values):
            row = (values_row,) if len(names) == 1 else values_row
            call = functools.partial(function, **dict(zip(names, row)))
            call.__name__ = name + "_" + str(index)
            focused.addTest(unittest.FunctionTestCase(call, description=call.__name__))
    return focused


if __name__ == "__main__":
    unittest.main()
