"""Synthetic byte-preservation controls; these do not establish game semantics."""

import hashlib
import unittest

from smr_launcher.compatibility_recipes import IdentityEdit, compile_sealed_industry_restoration
from smr_launcher.rules import RuleError


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SealedIndustryRestorationTests(unittest.TestCase):
    BEFORE = (
        b"<RRTIndustries><Industries><RRTIndustry><szName>Example Mill </szName>"
        b"<szModel>authored.kfm</szModel><DisplayNames><szDisplayName>Example Mill </szDisplayName>"
        b"</DisplayNames><Production><Resource><Input>Input Crop</Input><Output>Output Crop</Output>"
        b"</Resource></Production><Locations><Location><StartX>17</StartX></Location></Locations>"
        b"<!-- Example Mill : preserve this authored description -->"
        b"</RRTIndustry></Industries></RRTIndustries>"
    )
    INTENDED = (
        b"<RRTIndustries><Industries><RRTIndustry><szName>Example Mill</szName>"
        b"<szModel>authored.kfm</szModel><DisplayNames><szDisplayName>Example Mill </szDisplayName>"
        b"</DisplayNames><Production><Resource><Input>Input Crop</Input><Output>Output Crop</Output>"
        b"</Resource></Production><Locations><Location><StartX>17</StartX></Location></Locations>"
        b"<!-- Example Mill : preserve this authored description -->"
        b"</RRTIndustry></Industries></RRTIndustries>"
    )

    def compile(self, data=None, intended=None, **kwargs):
        data = self.BEFORE if data is None else data
        intended = self.INTENDED if intended is None else intended
        arguments = dict(
            path="UserMaps/Synthetic/industries.xml",
            data=data,
            before_sha256=sha(data),
            after_sha256=sha(intended),
            edit=IdentityEdit("industry", "Example Mill ", "Example Mill"),
        )
        arguments.update(kwargs)
        return compile_sealed_industry_restoration(**arguments)

    def test_restores_only_declared_key_and_retains_authored_payload(self):
        patch = self.compile()
        self.assertEqual(patch.old, self.BEFORE)
        self.assertEqual(patch.new, self.INTENDED)
        self.assertEqual(len(patch.old) - len(patch.new), 1)
        self.assertIn(b"<szDisplayName>Example Mill </szDisplayName>", patch.new)

    def test_refuses_changed_source_or_unsealed_output(self):
        with self.assertRaisesRegex(RuleError, "source hash"):
            self.compile(data=self.BEFORE + b" ", before_sha256=sha(self.BEFORE))
        with self.assertRaisesRegex(RuleError, "sealed intent"):
            self.compile(intended=self.INTENDED.replace(b"authored.kfm", b"lost.kfm"))

    def test_refuses_other_whitespace_and_nonliteral_representations(self):
        for value in (b"Example Mill", b"Example Mill  ", b"Example Mill\t"):
            with self.subTest(value=value), self.assertRaises(RuleError):
                self.compile(
                    data=self.BEFORE.replace(b"Example Mill </szName>", value + b"</szName>")
                )
        for value in (b"Example Mill&#32;", b"<![CDATA[Example Mill ]]> "):
            with self.subTest(value=value), self.assertRaises(RuleError):
                self.compile(
                    data=self.BEFORE.replace(b"Example Mill </szName>", value + b"</szName>")
                )

    def test_refuses_destination_collision_duplicate_keys_and_containers(self):
        for addition in (
            b"<RRTIndustry><szName>Example Mill</szName></RRTIndustry>",
            b"<RRTIndustry><szName>Example Mill </szName></RRTIndustry>",
        ):
            with self.subTest(addition=addition), self.assertRaises(RuleError):
                self.compile(
                    data=self.BEFORE.replace(b"</Industries>", addition + b"</Industries>")
                )
        with self.assertRaises(RuleError):
            self.compile(
                data=self.BEFORE.replace(b"</RRTIndustries>", b"<Industries/></RRTIndustries>")
            )

    def test_refuses_missing_or_multiple_singleton_names_and_wrong_edit_kind(self):
        for replacement in (
            b"<szName>Example Mill </szName><szName>Other</szName>",
            b"<szName><Nested>Example Mill </Nested></szName>",
        ):
            with self.subTest(replacement=replacement), self.assertRaises(RuleError):
                self.compile(
                    data=self.BEFORE.replace(b"<szName>Example Mill </szName>", replacement)
                )
        with self.assertRaises(RuleError):
            self.compile(edit=IdentityEdit("depot", "Example Mill ", "Example Mill"))


if __name__ == "__main__":
    unittest.main()
