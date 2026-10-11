"""Invented bank graphs through existing disposable-copy rules, never activation."""

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from smr_launcher import bank_model
from smr_launcher.recipe_identity import expected_patched_context, map_file_context
from smr_launcher.rules import (
    CompatibilityRule,
    PRESERVE_RESOURCE_MTIMES,
    RuleError,
    TokenPatch,
    apply_rules,
)
from test_bank_model import synthetic_model


ARCHIVE, GAME = "a" * 64, "b" * 64
MODEL = "UserMaps/Invented/warehouse/bank.nif"
MTIME = 1_700_000_000_123_456_789


def sha(data):
    return hashlib.sha256(data).hexdigest()


def facts(root):
    """Independently snapshot every fixture file; omit read-sensitive atime."""
    return {
        p.relative_to(root).as_posix(): (
            p.read_bytes(),
            p.stat().st_mtime_ns,
            p.stat().st_ino,
            p.stat().st_mode,
        )
        for p in root.rglob("*")
        if p.is_file()
    }


class BankRulePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.original, self.prepared = self.root / "original", self.root / "prepared"

    def fixture(self, *, colors=False, reverse=False):
        source, _ = synthetic_model(colors=colors, reverse_properties=reverse)
        expected, _ = synthetic_model(modern=True, colors=colors, reverse_properties=reverse)
        # These complete expected bytes come from the independent literal encoder.
        output = bank_model._convert_supported_layout(source)
        target = self.original / MODEL
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        target.with_name("untouched.dds").write_bytes(b"invented texture\0\xff")
        for p in target.parent.iterdir():
            os.utime(p, ns=(MTIME, MTIME))
        shutil.copytree(self.original, self.prepared, copy_function=shutil.copy2)
        patch = TokenPatch(MODEL, sha(source), sha(expected), source, output)
        rule = CompatibilityRule(
            "invented-bank-layout",
            1,
            "Independent synthetic bank fixture; no engine applicability",
            "Exercise exact full-file patch mechanics in a disposable copy",
            ARCHIVE,
            GAME,
            (patch,),
        )
        return rule, expected

    def apply(self, rule, archive=ARCHIVE, game=GAME):
        return apply_rules(
            self.prepared, archive, game, (rule,), metadata_policy=PRESERVE_RESOURCE_MTIMES
        )

    def test_complete_bank_output_context_preservation_and_idempotence(self):
        for colors in (False, True):
            for reverse in (False, True):
                with self.subTest(colors=colors, reverse=reverse):
                    self.original = self.root / f"original-{colors}-{reverse}"
                    self.prepared = self.root / f"prepared-{colors}-{reverse}"
                    rule, expected = self.fixture(colors=colors, reverse=reverse)
                    original = facts(self.original)
                    before = facts(self.prepared)
                    context = map_file_context(self.prepared)
                    result = self.apply(rule)
                    after = facts(self.prepared)
                    self.assertEqual(facts(self.original), original)
                    self.assertEqual(set(after), set(before))
                    self.assertEqual(after[MODEL][0], expected)
                    self.assertEqual(after[MODEL][1], MTIME)
                    for name in set(before) - {MODEL}:
                        self.assertEqual(after[name], before[name])
                    self.assertEqual(result.output_sha256, ((MODEL, sha(expected)),))
                    self.assertEqual(
                        map_file_context(self.prepared), expected_patched_context(context, (rule,))
                    )
                    self.assertEqual(self.apply(rule), result)
                    self.assertEqual(facts(self.prepared), after)
                    self.assertEqual(facts(self.original), original)

    def test_foreign_archive_or_build_refuses_before_writing(self):
        rule, _ = self.fixture()
        before, original = facts(self.prepared), facts(self.original)
        for archive, game in (("c" * 64, GAME), (ARCHIVE, "c" * 64)):
            with self.subTest(archive=archive, game=game), self.assertRaises(RuleError):
                self.apply(rule, archive, game)
            self.assertEqual(facts(self.prepared), before)
            self.assertEqual(facts(self.original), original)

    def test_changed_preimage_and_wrong_output_refuse_without_writes(self):
        rule, _ = self.fixture()
        original = facts(self.original)
        before = facts(self.prepared)
        wrong = replace(rule, patches=(replace(rule.patches[0], after_sha256="d" * 64),))
        with self.assertRaisesRegex(RuleError, "expected hash"):
            self.apply(wrong)
        self.assertEqual(facts(self.prepared), before)
        target = self.prepared / MODEL
        target.write_bytes(target.read_bytes() + b"planted wrong bytes")
        changed = facts(self.prepared)
        with self.assertRaisesRegex(RuleError, "preimage"):
            self.apply(rule)
        self.assertEqual(facts(self.prepared), changed)
        self.assertEqual(facts(self.original), original)

    def test_linked_model_refuses_without_following_source(self):
        rule, _ = self.fixture()
        original = facts(self.original)
        target = self.prepared / MODEL
        target.unlink()
        target.symlink_to(self.original / MODEL)
        with self.assertRaisesRegex(RuleError, "unsafe"):
            self.apply(rule)
        self.assertTrue(target.is_symlink())
        self.assertEqual(facts(self.original), original)


if __name__ == "__main__":
    unittest.main()
