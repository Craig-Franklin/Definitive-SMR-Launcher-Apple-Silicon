import hashlib
import unittest

from smr_launcher.compatibility_recipes import (
    IdentityEdit, PreservedOccurrence, compile_identity_patch,
)
from smr_launcher.rules import RuleError


def sha(data):
    return hashlib.sha256(data).hexdigest()


class TypedRecipeTests(unittest.TestCase):
    def compile(self, data, output, edits=None, preserved=()):
        return compile_identity_patch(
            path="UserMaps/Synthetic/industries.xml", data=data,
            before_sha256=sha(data), after_sha256=sha(output),
            edits=edits or (IdentityEdit("industry", "Test Mill", "Test Works"),),
            preserved=preserved,
        )

    def document(self, body):
        return b"<RRTIndustries><Industries>" + body + b"</Industries></RRTIndustries>"

    def test_preserves_lexical_bytes_and_reviewed_labels(self):
        data = (b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\r\n'
                b'<RRTIndustries><!-- Test Mill --><Industries>\r\n'
                b'\t<RRTIndustry art="left&gt;right">\r\n\t<szName>Test Mill</szName>\r\n'
                b'<Label>Test Mill</Label><Description>Test Mill ships caf\xc3\xa9 &amp; grain</Description>'
                b'<Art>Test Mill.nif</Art></RRTIndustry>\r\n</Industries></RRTIndustries>')
        output = data.replace(b"<szName>Test Mill", b"<szName>Test Works")
        keep = PreservedOccurrence(("RRTIndustries", "Industries", "RRTIndustry", "Label"), "Test Mill")
        patch = self.compile(data, output, preserved=(keep,))
        self.assertEqual(patch.old, data)
        self.assertEqual(patch.new, output)
        with self.assertRaisesRegex(RuleError, "occurrence"):
            self.compile(data, output)

    def test_exact_attributes_need_review(self):
        data = self.document(b'<RRTIndustry label="Test Mill"><szName>Test Mill</szName></RRTIndustry>')
        output = data.replace(b">Test Mill<", b">Test Works<")
        with self.assertRaisesRegex(RuleError, "occurrence"):
            self.compile(data, output)
        keep = PreservedOccurrence(("RRTIndustries", "Industries", "RRTIndustry"), "Test Mill", attribute="label")
        self.assertEqual(self.compile(data, output, preserved=(keep,)).new, output)

    def test_depot_and_goal_are_separate_typed_operations(self):
        data = b"<RRTDepots><Depot><szName>Test Annex</szName></Depot></RRTDepots>"
        self.compile(data, data.replace(b"Test Annex", b"Works Annex"),
                     (IdentityEdit("depot", "Test Annex", "Works Annex"),))
        data = (b"<Scenario><Goals><OwnListItem><szObjectType>Industry</szObjectType>"
                b"<szObjectName>Test Mill</szObjectName></OwnListItem></Goals></Scenario>")
        edit = IdentityEdit("industry-goal", "Test Mill", "Test Works",
                            goal_path=("Scenario", "Goals", "OwnListItem", "szObjectName"))
        self.compile(data, data.replace(b"Test Mill", b"Test Works"), (edit,))
        for kind in (b"Train", b"industry", b"Industry</szObjectType><szObjectType>Industry"):
            invalid = data.replace(b">Industry<", b">" + kind + b"<")
            with self.subTest(kind=kind), self.assertRaises(RuleError):
                self.compile(invalid, invalid.replace(b"Test Mill", b"Test Works"), (edit,))

    def test_nested_wrong_path_is_not_a_definition(self):
        data = self.document(b"<Nested><RRTIndustry><szName>Test Mill</szName></RRTIndustry></Nested>")
        with self.assertRaisesRegex(RuleError, "count"):
            self.compile(data, data.replace(b"Test Mill", b"Test Works"))

    def test_duplicate_and_colliding_definition_keys_rejected(self):
        first = b"<RRTIndustry><szName>Test Mill</szName></RRTIndustry>"
        for other in (first, first.replace(b"Test Mill", b"Test Works")):
            with self.subTest(other=other), self.assertRaises(RuleError):
                data = self.document(first + other)
                self.compile(data, data.replace(b"Test Mill", b"Test Works"))

    def test_engine_first_child_ambiguities_fail_closed(self):
        data = self.document(b"<RRTIndustry><szName>First Factory</szName><szName>Test Mill</szName></RRTIndustry>")
        with self.assertRaisesRegex(RuleError, "exactly one"):
            self.compile(data, data.replace(b"Test Mill", b"Test Works"))
        data = (b"<RRTIndustries><Industries/><Industries><RRTIndustry>"
                b"<szName>Test Mill</szName></RRTIndustry></Industries></RRTIndustries>")
        with self.assertRaisesRegex(RuleError, "container"):
            self.compile(data, data.replace(b"Test Mill", b"Test Works"))
        data = (b"<Scenario><OwnListItem><szObjectType>Industry</szObjectType>"
                b"<szObjectName>Other</szObjectName><szObjectName>Test Mill</szObjectName>"
                b"</OwnListItem></Scenario>")
        edit = IdentityEdit("industry-goal", "Test Mill", "Test Works",
                            goal_path=("Scenario", "OwnListItem", "szObjectName"))
        with self.assertRaisesRegex(RuleError, "unambiguously"):
            self.compile(data, data.replace(b"Test Mill", b"Test Works"), (edit,))

    def test_case_variant_goal_cannot_be_silently_left_behind(self):
        data = self.document(b"<RRTIndustry><szName>Test Mill</szName><OwnListItem>"
                             b"<szObjectType>Industry</szObjectType><szObjectName>TEST MILL</szObjectName>"
                             b"</OwnListItem></RRTIndustry>")
        with self.assertRaisesRegex(RuleError, "Case-variant"):
            self.compile(data, data.replace(b"Test Mill", b"Test Works"))

    def test_unselected_mixed_reference_and_padded_identity_are_rejected(self):
        data = self.document(b"<RRTIndustry><szName>Test Mill</szName><OwnListItem>"
                             b"<szObjectType>Industry</szObjectType><szObjectName>Test <b>Mill</b></szObjectName>"
                             b"</OwnListItem></RRTIndustry>")
        with self.assertRaisesRegex(RuleError, "plain unpadded"):
            self.compile(data, data.replace(b"Test Mill", b"Test Works"))

    def test_typed_reference_cannot_be_allowlisted_as_a_display_label(self):
        data = self.document(b"<RRTIndustry><szName>Test Mill</szName><OwnListItem>"
                             b"<szObjectType>Industry</szObjectType><szObjectName>Test Mill</szObjectName>"
                             b"</OwnListItem></RRTIndustry>")
        keep = PreservedOccurrence(("RRTIndustries", "Industries", "RRTIndustry", "OwnListItem", "szObjectName"), "Test Mill")
        with self.assertRaisesRegex(RuleError, "cannot be declared"):
            self.compile(data, data.replace(b"<szName>Test Mill", b"<szName>Test Works"), preserved=(keep,))
        data = self.document(b"<RRTIndustry><szName> Test Mill </szName></RRTIndustry>")
        with self.assertRaisesRegex(RuleError, "plain unpadded"):
            self.compile(data, data.replace(b"Test Mill", b"Test Works"))

    def test_aliases_cannot_merge_or_chain(self):
        data = self.document(b"<RRTIndustry><szName>Test Mill</szName></RRTIndustry>")
        for edits in (
            (IdentityEdit("industry", "Test Mill", "Test Works"), IdentityEdit("depot", "Other", "Test Works")),
            (IdentityEdit("industry", "Test Mill", "Test Works"), IdentityEdit("depot", "Test Works", "Other")),
            (IdentityEdit("industry", "Test Mill", "Test Works"), IdentityEdit("depot", "Test Mill", "Other")),
        ):
            with self.subTest(edits=edits), self.assertRaisesRegex(RuleError, "injective"):
                self.compile(data, b"unused", edits)

    def test_unsupported_xml_rejected(self):
        record = b"<RRTIndustry><szName>Test Mill</szName></RRTIndustry>"
        simple = self.document(record)
        cases = [
            b'<!DOCTYPE RRTIndustries [<!ENTITY x "Test Mill">]>' + simple,
            b'<?xml version="1.0" encoding="ISO-8859-1"?>' + simple,
            b'<?processor x?>' + simple,
            simple.replace(b"<RRTIndustries>", b'<RRTIndustries xmlns="urn:example">'),
            simple.replace(b"Test Mill", b"Test&#32;Mill"),
            simple.replace(b"Test Mill", b"<![CDATA[Test Mill]]>"),
            simple.replace(b"Test Mill", b"Test<!-- comment --> Mill"),
            simple.replace(b"Test Mill", b"Test <b/>Mill"),
            b"<x>" * 65 + b"</x>" * 65,
            b"\xff" + simple,
        ]
        for data in cases:
            with self.subTest(data=data[:70]), self.assertRaises(RuleError):
                self.compile(data, b"unused")

    def test_ascii_declarations_accept_only_ascii_bytes_and_preserve_every_other_byte(self):
        body = (b'<RRTIndustry><szName>Test Mill</szName>'
                b'<Description>caf&#233; ships by rail</Description>'
                b'<Art>Test Mill.nif</Art></RRTIndustry>')
        for declaration in (b"ASCII", b"US-ASCII", b"us-ascii"):
            with self.subTest(declaration=declaration):
                data = (b'<?xml version="1.0" encoding="' + declaration + b'"?>\r\n'
                        + self.document(body))
                output = data.replace(b"<szName>Test Mill", b"<szName>Test Works", 1)
                self.assertTrue(all(byte < 0x80 for byte in data))
                patch = self.compile(data, output)
                self.assertEqual(patch.old, data)
                self.assertEqual(patch.new, output)
                self.assertIn(b'encoding="' + declaration + b'"?>\r\n', patch.new)
                self.assertIn(b"<Description>caf&#233; ships by rail</Description>", patch.new)
                self.assertIn(b"<Art>Test Mill.nif</Art>", patch.new)

    def test_ascii_declaration_rejects_non_ascii_bytes_bom_and_other_encodings(self):
        ascii_body = self.document(
            b'<RRTIndustry><szName>Test Mill</szName></RRTIndustry>')
        declaration = b'<?xml version="1.0" encoding="US-ASCII"?>'
        raw_utf8 = (declaration + ascii_body.replace(
            b"</RRTIndustry>", b"<Description>caf\xc3\xa9</Description></RRTIndustry>"))
        bom_ascii = b"\xef\xbb\xbf" + declaration + ascii_body
        latin1 = (b'<?xml version="1.0" encoding="ISO-8859-1"?>' +
                  self.document(b'<RRTIndustry><szName>Test Mill</szName>'
                                b'<Description>caf\xe9</Description></RRTIndustry>'))
        utf16 = ('<?xml version="1.0" encoding="UTF-16"?>'
                 '<RRTIndustries><Industries><RRTIndustry><szName>Test Mill</szName>'
                 '</RRTIndustry></Industries></RRTIndustries>').encode("utf-16")

        for label, data in (("UTF-8 bytes under ASCII declaration", raw_utf8),
                            ("BOM plus ASCII declaration", bom_ascii),
                            ("Latin-1", latin1), ("UTF-16", utf16)):
            with self.subTest(label=label), self.assertRaises(RuleError):
                self.compile(data, b"unused")

    def test_expected_hashes_and_counts_are_mandatory(self):
        data = self.document(b"<RRTIndustry><szName>Test Mill</szName></RRTIndustry>")
        with self.assertRaisesRegex(RuleError, "output hash"):
            self.compile(data, data)
        with self.assertRaisesRegex(RuleError, "source hash"):
            compile_identity_patch(path="UserMaps/a.xml", data=data, before_sha256="0" * 64,
                                   after_sha256="0" * 64,
                                   edits=(IdentityEdit("industry", "Test Mill", "Test Works"),))
        with self.assertRaisesRegex(RuleError, "count"):
            self.compile(data, b"unused", (IdentityEdit("industry", "Test Mill", "Test Works", count=2),))

    def test_unsafe_patch_path_and_invalid_operations(self):
        data = self.document(b"<RRTIndustry><szName>Test Mill</szName></RRTIndustry>")
        with self.assertRaises(RuleError):
            compile_identity_patch(path="UserMaps/../../original.xml", data=data,
                                   before_sha256=sha(data), after_sha256=sha(data.replace(b"Test Mill", b"Test Works")),
                                   edits=(IdentityEdit("industry", "Test Mill", "Test Works"),))
        for kind, source, target in (("xpath", "A", "B"), ("industry", "A", "A"), ("industry", "A", "B&")):
            with self.subTest(kind=kind), self.assertRaises(RuleError):
                IdentityEdit(kind, source, target)
        with self.assertRaises(RuleError):
            IdentityEdit("industry", "A", "B", count=True)


if __name__ == "__main__":
    unittest.main()
