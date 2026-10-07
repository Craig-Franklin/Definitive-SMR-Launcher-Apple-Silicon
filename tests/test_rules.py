"""Synthetic fixtures for exact, repeatable compatibility rules."""
from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.rules import AssetAddition, CompatibilityRule, RuleError, TokenPatch, applicable_rules, apply_rules
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

    def test_opt_in_metadata_preserves_old_mtime_on_write_and_repeat(self):
        old_mtime = 946684800123456789
        os.utime(self.target, ns=(old_mtime, old_mtime))
        actual_mtime = self.target.stat().st_mtime_ns
        first = apply_rules(self.root, self.archive, self.game, (self.rule,),
                            metadata_policy=rule_module.PRESERVE_RESOURCE_MTIMES)
        self.assertEqual(self.target.read_bytes(), self.after)
        self.assertEqual(self.target.stat().st_mtime_ns, actual_mtime)
        second = apply_rules(self.root, self.archive, self.game, (self.rule,),
                             metadata_policy=rule_module.PRESERVE_RESOURCE_MTIMES)
        self.assertEqual(first, second)
        self.assertEqual(self.target.stat().st_mtime_ns, actual_mtime)
        legacy = apply_rules(self.root, self.archive, self.game, (self.rule,))
        self.assertNotEqual(first.fingerprint, legacy.fingerprint)
        self.assertEqual(self.target.stat().st_mtime_ns, actual_mtime)

    def test_legacy_fingerprint_unchanged_and_unknown_policy_does_not_write(self):
        descriptor = [dict(id=self.rule.rule_id, version=1, provenance=self.rule.provenance,
                           reason=self.rule.reason, archive=self.archive, game=self.game,
                           patches=[dict(path=self.patch.path, before=digest(self.before),
                                         after=digest(self.after), old=digest(self.patch.old),
                                         new=digest(self.patch.new))])]
        expected = digest(json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode())
        with self.assertRaisesRegex(RuleError, "metadata policy"):
            apply_rules(self.root, self.archive, self.game, (self.rule,), metadata_policy="freshen")
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertEqual(apply_rules(self.root, self.archive, self.game, (self.rule,)).fingerprint, expected)

    def test_failed_timestamp_restore_leaves_target_unchanged(self):
        before_stat = self.target.stat()
        with patch.object(rule_module.os, "utime", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                apply_rules(self.root, self.archive, self.game, (self.rule,),
                            metadata_policy=rule_module.PRESERVE_RESOURCE_MTIMES)
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertEqual(self.target.stat().st_mtime_ns, before_stat.st_mtime_ns)
        self.assertFalse(list(self.target.parent.glob(".smr-rule-*")))

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

    def test_asset_addition_is_hash_bound_and_metadata_preserving(self):
        data = b"new model bytes"
        addition = AssetAddition("UserMaps/Example/model.kfm", digest(data), data, 123456789)
        rule = CompatibilityRule("add-model", 1, "synthetic fixture", "add one model",
                                 self.archive, self.game, (), (addition,))
        first = apply_rules(self.root, self.archive, self.game, (rule,),
                            metadata_policy=rule_module.PRESERVE_RESOURCE_MTIMES)
        output = self.root / "UserMaps/Example/model.kfm"
        self.assertEqual(output.read_bytes(), data)
        self.assertEqual(output.stat().st_mtime_ns, 123456789)
        second = apply_rules(self.root, self.archive, self.game, (rule,),
                             metadata_policy=rule_module.PRESERVE_RESOURCE_MTIMES)
        self.assertEqual(first, second)

    def test_asset_addition_rejects_existing_different_target(self):
        data = b"new model bytes"
        addition = AssetAddition("UserMaps/Example/model.kfm", digest(data), data, 1)
        (self.root / "UserMaps/Example/model.kfm").write_bytes(b"different")
        rule = CompatibilityRule("add-model", 1, "synthetic fixture", "add one model",
                                 self.archive, self.game, (), (addition,))
        with self.assertRaisesRegex(RuleError, "already exists"):
            apply_rules(self.root, self.archive, self.game, (rule,))

    def test_asset_addition_rejects_mtime_drift_before_any_writes(self):
        data = b"new model bytes"
        expected_mtime = 1_234_567_890
        addition = AssetAddition("UserMaps/Example/model.kfm", digest(data), data, expected_mtime)
        output = self.root / "UserMaps/Example/model.kfm"
        output.write_bytes(data)
        os.utime(output, ns=(expected_mtime, expected_mtime))
        drifted_mtime = expected_mtime + 1_000_000_000
        os.utime(output, ns=(drifted_mtime, drifted_mtime))
        rule = CompatibilityRule("add-model", 1, "synthetic fixture", "add one model",
                                 self.archive, self.game, (self.patch,), (addition,))

        with self.assertRaisesRegex(RuleError, "different mtime"):
            apply_rules(self.root, self.archive, self.game, (rule,),
                        metadata_policy=rule_module.PRESERVE_RESOURCE_MTIMES)

        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertEqual(output.read_bytes(), data)
        self.assertEqual(output.stat().st_mtime_ns, drifted_mtime)

    def test_asset_addition_rejects_oversized_target_without_reading_it(self):
        data = b"small"
        output = self.root / "UserMaps/Example/model.kfm"
        with output.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024)
        addition = AssetAddition("UserMaps/Example/model.kfm", digest(data), data, 1)
        rule = CompatibilityRule("add-model", 1, "synthetic fixture", "add one model",
                                 self.archive, self.game, (), (addition,))

        with patch.object(Path, "read_bytes", side_effect=AssertionError("must not read target")):
            with self.assertRaisesRegex(RuleError, "already exists"):
                apply_rules(self.root, self.archive, self.game, (rule,))

        self.assertEqual(output.stat().st_size, 64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
