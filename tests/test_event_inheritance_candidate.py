"""Independent literal-byte contract; these fixtures contain invented events."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))
import check_event_inheritance_candidate as checker
from smr_launcher.rules import RuleError

FIXTURES = Path(__file__).parent / "fixtures" / "event_inheritance"
PINS = {
    "original": "244b2eac35bac4f732731e5f1964ce20381a9f8fae52ebb702f7b37a9a00d692",
    "base": "5c19d275672e8d5045a1c407042a92fd6224bfcf6fd28d755b53d0123b03d291",
    "candidate": "07b5cbbcdd6b06995db99d262896b474199fddc4959918ff752e1776d043b139",
}
TITLES = ("EventAlpha", "EventBeta", "EventGamma")


class EventInheritanceCandidateTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {role: (FIXTURES / (role + ".xml")).read_bytes() for role in PINS}
        self.assertEqual(self.hashes(self.inputs), PINS)

    @staticmethod
    def hashes(inputs):
        return {role: hashlib.sha256(data).hexdigest() for role, data in inputs.items()}

    def check(self, inputs=None, *, titles=TITLES, hashes=None):
        inputs = self.inputs if inputs is None else inputs
        return checker.check_candidate(
            **inputs, titles=titles, hashes=self.hashes(inputs) if hashes is None else hashes
        )

    def test_independently_literal_complete_candidate(self):
        result = self.check(hashes=PINS)
        self.assertEqual(result["status"], "exact_candidate_bytes_match")
        self.assertEqual(result["selected_events"], 3)
        self.assertEqual(result["removed_effects"], 3)
        self.assertEqual(result["input_sha256"], PINS)
        for key in ("native_context", "feature_preservation", "gameplay_and_save"):
            self.assertEqual(result[key], "unknown")
        # The entire expected file is literal, including CRLF, comment decoy and
        # the unselected event's effect. No checker generates the expected bytes.
        self.assertIn(b"<!-- untouched <Effects>decoy</Effects> -->\r\n", self.inputs["candidate"])
        self.assertIn(b"<fModifierMin>7.25</fModifierMin>", self.inputs["candidate"])

    def test_semantic_or_lexical_changes_refuse_even_with_updated_hashes(self):
        cases = (
            ("original", b"1.25", b"1.26"),
            ("base", b"1.25", b"1.26"),
            ("original", b"EventBeta", b"EventAlpha"),
            ("base", b"EventBeta", b"EventAlpha"),
            ("original", b"<Event><szTitle>EventAlpha", b"<Other><szTitle>EventAlpha"),
            ("original", b"<szTitle>EventAlpha</szTitle>", b"<szTitle/>"),
            ("original", b"<szTitle>EventAlpha</szTitle>", b"<szTitle>EventAlpha</szTitle>" * 2),
            ("original", b"<Effects><EventEffect>", b"<Effects></Effects><Effects><EventEffect>"),
            ("base", b"<Effects>\n<EventEffect>", b"<Effects/>\n<EventEffect>"),
            (
                "original",
                b"<fModifierMin>1.25</fModifierMin>",
                b"<fModifierMin>1.25</fModifierMin>" * 2,
            ),
            ("original", b"<Effects><EventEffect>", b"<Effects>mixed<EventEffect>"),
            ("original", b"</EventEffect></Effects>", b"</EventEffect>mixed</Effects>"),
            ("original", b"<Effects><EventEffect>", b"<Effects><!-- decoy --><EventEffect>"),
            ("original", b"<Effects><EventEffect>", b"<Effects><![CDATA[ ]]><EventEffect>"),
            ("original", b"<Effects><EventEffect>", b"<Effects extra='yes'><EventEffect>"),
            ("original", b"<EventEffect>", b"<EventEffect extra='yes'>"),
            ("original", b"<szType>Stock</szType>", b"<szType>Other</szType>"),
            ("original", b"1.25", b" 1.25"),
            ("original", b"1.25", b"1&#46;25"),
            ("candidate", b"Alpha unchanged", b"Alpha changed"),
            ("candidate", b"</Note>", b"</Note><Effects/>"),
            ("candidate", b"<fModifierMin>7.25</fModifierMin>", b""),
            ("candidate", b"\r\n", b"\n"),
        )
        for role, before, after in cases:
            with self.subTest(role=role, before=before, after=after):
                self.assertIn(before, self.inputs[role])
                changed = dict(self.inputs)
                changed[role] = changed[role].replace(before, after, 1)
                with self.assertRaises(RuleError):
                    self.check(changed)

    def test_input_and_selector_admission(self):
        for titles in (
            (),
            list(TITLES),
            TITLES + TITLES,
            ("Absent",),
            ("bad/title",),
            (None,),
            ("A",) * 65,
        ):
            with self.subTest(titles=titles), self.assertRaises(RuleError):
                self.check(titles=titles)
        for role in self.inputs:
            for data in (b"", b"x" * (checker.MAX_XML_BYTES + 1)):
                with self.subTest(role=role, size=len(data)), self.assertRaises(RuleError):
                    self.check(dict(self.inputs, **{role: data}))
        for hashes in ({}, dict(PINS, original="0" * 64), dict(PINS, extra="0" * 64)):
            with self.subTest(hashes=hashes), self.assertRaises(RuleError):
                self.check(hashes=hashes)
        with self.assertRaises(RuleError):
            checker.check_candidate(
                **dict(self.inputs, original=bytearray(self.inputs["original"])),
                titles=TITLES,
                hashes=PINS,
            )

    def test_well_formed_wrong_scope_and_missing_effects(self):
        original = self.inputs["original"]
        for changed in (
            original.replace(b"<RRTEvents>", b"<RRTEvents><Wrapper>").replace(
                b"</RRTEvents>", b"</Wrapper></RRTEvents>"
            ),
            original.replace(b"RRTEvents", b"OtherEvents"),
            original.replace(b"<Effects><EventEffect>", b"<Other><EventEffect>").replace(
                b"</EventEffect></Effects>", b"</EventEffect></Other>"
            ),
        ):
            with self.subTest(changed=changed), self.assertRaises(RuleError):
                self.check(dict(self.inputs, original=changed))

    def test_paired_container_formatting_requires_literal_xml_whitespace(self):
        for token, prepend in (
            (b"<EventEffect>", True),
            (b"</EventEffect>", False),
            (b"<szType>", True),
            (b"</fModifierMax>", False),
        ):
            for formatting, accepted in (
                (b" \t\r\n", True),
                (b"\xc2\xa0", False),
                (b"&#32;", False),
            ):
                with self.subTest(token=token, formatting=formatting):
                    changed = dict(self.inputs)
                    replacement = formatting + token if prepend else token + formatting
                    for role in ("original", "base"):
                        changed[role] = changed[role].replace(token, replacement, 1)
                    # Both inputs receive the same lexical challenge. Correct
                    # hashes and the independent complete candidate stay valid.
                    if accepted:
                        self.assertEqual(
                            self.check(changed)["status"], "exact_candidate_bytes_match"
                        )
                    else:
                        with self.assertRaises(RuleError):
                            self.check(changed)

    def test_cli_closed_output_read_only_and_unsafe_path_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = {role: root / (role + ".xml") for role in PINS}
            for role, path in paths.items():
                path.write_bytes(self.inputs[role])
                path.chmod(0o444)
            before = {role: (path.stat(), path.read_bytes()) for role, path in paths.items()}
            argv = []
            for role, path in paths.items():
                argv.extend(("--" + role, str(path), "--" + role + "-sha256", PINS[role]))
            for title in TITLES:
                argv.extend(("--event-title", title))

            def run(arguments):
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = checker.main(arguments)
                self.assertEqual(stderr.getvalue(), "")
                for private in (str(root), "EventAlpha", "HeadlineAlpha", "<RRTEvents>"):
                    self.assertNotIn(private, stdout.getvalue())
                return code, json.loads(stdout.getvalue())

            code, result = run(argv)
            self.assertEqual((code, result["status"]), (0, "exact_candidate_bytes_match"))
            wrong_hash = list(argv)
            wrong_hash[3] = "0" * 64
            self.assertEqual(run(wrong_hash)[0], 1)
            linked = root / "linked.xml"
            linked.symlink_to(paths["original"])
            for bad in (str(linked), str(root), str(root / "missing.xml")):
                refused = list(argv)
                refused[1] = bad
                self.assertEqual(
                    run(refused),
                    (1, {"schema": 1, "checker": checker.CHECKER_VERSION, "status": "refused"}),
                )
            after = {role: (path.stat(), path.read_bytes()) for role, path in paths.items()}
            for role in PINS:
                self.assertEqual(before[role][1], after[role][1])
                for field in ("st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"):
                    self.assertEqual(
                        getattr(before[role][0], field), getattr(after[role][0], field)
                    )


if __name__ == "__main__":
    unittest.main()
