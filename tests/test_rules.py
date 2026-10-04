"""Synthetic fixtures for exact, repeatable compatibility rules."""
from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.rules import CompatibilityRule, RuleError, TokenPatch, applicable_rules, apply_rules
import smr_launcher.rules as rule_module


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RuleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "UserMaps/Example").mkdir(parents=True)
        self.target = self.root / "UserMaps/Example/scenario.xml"
        self.before = b"<scenario><limit>old</limit></scenario>"
        self.after = b"<scenario><limit>new</limit></scenario>"
        self.target.write_bytes(self.before)
        self.archive = digest(b"archive")
        self.game = digest(b"game")
        self.patch = TokenPatch(
            "UserMaps/Example/scenario.xml", digest(self.before), digest(self.after),
            b"<limit>old</limit>", b"<limit>new</limit>",
        )
        self.rule = CompatibilityRule(
            "example-limit", 1, "synthetic fixture", "Replace one known bad value",
            self.archive, self.game, (self.patch,),
        )

    def test_exact_rule_is_deterministic_and_idempotent(self):
        self.assertEqual(applicable_rules((self.rule,), self.archive, self.game), (self.rule,))
        first = apply_rules(self.root, self.archive, self.game, (self.rule,))
        self.assertEqual(self.target.read_bytes(), self.after)
        second = apply_rules(self.root, self.archive, self.game, (self.rule,))
        self.assertEqual(first, second)
        self.assertEqual(first.rule_keys, ("example-limit@1",))
        self.assertEqual(first.output_sha256, ((self.patch.path, digest(self.after)),))

    def test_wrong_archive_or_game_never_selects_or_applies_rule(self):
        self.assertEqual(applicable_rules((self.rule,), digest(b"other"), self.game), ())
        self.assertEqual(applicable_rules((self.rule,), self.archive, digest(b"other")), ())
        with self.assertRaises(RuleError):
            apply_rules(self.root, digest(b"other"), self.game, (self.rule,))
        self.assertEqual(self.target.read_bytes(), self.before)

    def test_unexpected_preimage_or_result_is_rejected(self):
        self.target.write_bytes(b"<scenario>changed</scenario>")
        with self.assertRaises(RuleError):
            apply_rules(self.root, self.archive, self.game, (self.rule,))
        self.assertEqual(self.target.read_bytes(), b"<scenario>changed</scenario>")
        self.target.write_bytes(self.before)
        bad_patch = TokenPatch(self.patch.path, digest(self.before), digest(b"wrong"), self.patch.old, self.patch.new)
        bad_rule = CompatibilityRule("bad-result", 1, "synthetic fixture", "bad hash",
                                     self.archive, self.game, (bad_patch,))
        with self.assertRaises(RuleError):
            apply_rules(self.root, self.archive, self.game, (bad_rule,))
        self.assertEqual(self.target.read_bytes(), self.before)

    def test_full_preflight_avoids_partial_mutation(self):
        missing = TokenPatch("UserMaps/Example/missing.xml", digest(b"old"), digest(b"new"), b"old", b"new")
        later = CompatibilityRule("later", 1, "synthetic fixture", "missing target",
                                  self.archive, self.game, (missing,))
        with self.assertRaises(RuleError):
            apply_rules(self.root, self.archive, self.game, (self.rule, later))
        self.assertEqual(self.target.read_bytes(), self.before)

    def test_growing_result_above_limit_is_rejected_before_write(self):
        before = b"AAAAAAA"
        after = b"AAAAAAAAA"
        self.target.write_bytes(before)
        growing = TokenPatch(self.patch.path, digest(before), digest(after), before, after)
        rule = CompatibilityRule("growing", 1, "synthetic fixture", "size boundary",
                                 self.archive, self.game, (growing,))
        with patch.object(rule_module, "MAX_PATCHED_FILE_BYTES", 8):
            with self.assertRaises(RuleError):
                apply_rules(self.root, self.archive, self.game, (rule,))
        self.assertEqual(self.target.read_bytes(), before)

    def test_ambiguous_versions_and_paths_are_rejected(self):
        another_version = CompatibilityRule("example-limit", 2, "synthetic fixture", "other version",
                                            self.archive, self.game, (self.patch,))
        with self.assertRaises(RuleError):
            applicable_rules((self.rule, another_version), self.archive, self.game)
        overlap = CompatibilityRule("another", 1, "synthetic fixture", "same path",
                                    self.archive, self.game, (self.patch,))
        with self.assertRaises(RuleError):
            applicable_rules((self.rule, overlap), self.archive, self.game)
        case_alias = TokenPatch(self.patch.path.replace("scenario.xml", "Scenario.xml"),
                                self.patch.before_sha256,
                                self.patch.after_sha256, self.patch.old, self.patch.new)
        alias = CompatibilityRule("case-alias", 1, "synthetic fixture", "same file by case",
                                  self.archive, self.game, (case_alias,))
        with self.assertRaises(RuleError):
            applicable_rules((self.rule, alias), self.archive, self.game)

    def test_linked_target_and_unsafe_paths_are_rejected(self):
        with self.assertRaises(RuleError):
            TokenPatch("UserMaps/../other", digest(b"old"), digest(b"new"), b"old", b"new")
        self.target.unlink()
        self.target.symlink_to(self.root / "outside")
        with self.assertRaises((RuleError, ValueError)):
            apply_rules(self.root, self.archive, self.game, (self.rule,))


if __name__ == "__main__":
    unittest.main()
