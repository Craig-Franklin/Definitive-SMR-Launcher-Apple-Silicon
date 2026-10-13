"""Independently specified bank-family source predicates; no runtime or game data."""

from contextlib import ExitStack
import hashlib
import os
import shutil
import unittest
from unittest.mock import patch

from smr_launcher import bank_applicability, bank_model, recipe_service
from smr_launcher.recipe_identity import expected_patched_context, map_file_context
from smr_launcher.resource_identity import ResourceFingerprintError
from smr_launcher.rules import CompatibilityRule, PRESERVE_RESOURCE_MTIMES, RuleError, apply_rules
from test_bank_model import synthetic_model
import test_recipe_service


def sha(data):
    return hashlib.sha256(data).hexdigest()


class BankApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recipe_service.SyntheticRecipeServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.scenario = (
            b"<RRTScenario><IndustriesXMLFile>RRT_Industries_Synthetic.xml</IndustriesXMLFile>"
            b"<Description>Keep every feature</Description></RRTScenario>"
        )
        self.industry = (
            b"<RRTIndustries><Industries><RRTIndustry><szName>Invented Mill</szName>"
            b"<szModel>bank.nif</szModel><Production>unchanged</Production></RRTIndustry>"
            b"<RRTIndustry><szName>Unrelated</szName><szModel>other.nif</szModel></RRTIndustry>"
            b"</Industries></RRTIndustries>"
        )
        self.model_path = "UserMaps/Synthetic/Warehouse/bank.nif"
        self.bank, _ = synthetic_model()
        self.expected, _ = synthetic_model(modern=True)
        (f.source / f.SCENARIO_PATH).write_bytes(self.scenario)
        (f.source / f.INDUSTRY_PATH).write_bytes(self.industry)
        (f.source / self.model_path).write_bytes(self.bank)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("SOURCE_SHA256", sha(self.bank)),
            ("SOURCE_SIZE", len(self.bank)),
            ("OUTPUT_SHA256", sha(self.expected)),
            ("OUTPUT_SIZE", len(self.expected)),
        ):
            self.stack.enter_context(patch.object(bank_model, name, value))
        self.stack.enter_context(patch.object(bank_applicability, "SUPPORTED_GAME_SHA256", f.GAME))

    def inputs(self):
        f = self.fixture
        return dict(
            archive_sha256=f.ARCHIVE,
            game_sha256=f.GAME,
            source_context=map_file_context(f.source),
            stock_context=f.app._stock_file_context(),
            scenario_path=f.SCENARIO_PATH,
            scenario_data=(f.source / f.SCENARIO_PATH).read_bytes(),
            industry_path=f.INDUSTRY_PATH,
            industry_data=(f.source / f.INDUSTRY_PATH).read_bytes(),
            model_path=self.model_path,
            model_data=(f.source / self.model_path).read_bytes(),
        )

    def assess(self, **changes):
        return bank_applicability.assess_bank_applicability(**(self.inputs() | changes))

    def inspect(self, record=None, **changes):
        f = self.fixture
        return recipe_service.inspect_bank_candidate(
            f.app,
            record or f.record,
            **(
                dict(
                    scenario_path=f.SCENARIO_PATH,
                    industry_path=f.INDUSTRY_PATH,
                    model_path=self.model_path,
                )
                | changes
            ),
        )

    def test_source_candidate_retains_every_full_map_unknown_and_service_agrees(self):
        result = self.assess()
        self.assertEqual(result.status, "source-candidate")
        self.assertEqual(result.exclusions, ())
        self.assertEqual(
            result.unknowns,
            (
                "native-provider-selection",
                "complete-scenario-dependency-context",
                "render-collision-feature-gameplay-save-reopen",
            ),
        )
        self.assertEqual(result.scenario_path, self.fixture.SCENARIO_PATH)
        self.assertEqual(result.scenario_sha256, sha(self.scenario))
        self.assertEqual(self.inspect(), result)
        self.assertFalse((self.fixture.root / "prepared").exists())

    def test_names_do_not_select_family_and_each_scenario_context_is_bound(self):
        first = self.assess()
        f = self.fixture
        (f.source / "UserMaps/Synthetic").rename(f.source / "UserMaps/EntirelyDifferent")
        supplied = dict(
            archive_sha256="c" * 64,
            game_sha256=f.GAME,
            source_context=map_file_context(f.source),
            stock_context=f.app._stock_file_context(),
            scenario_path=f.SCENARIO_PATH.replace("Synthetic/", "EntirelyDifferent/"),
            scenario_data=self.scenario,
            industry_path=f.INDUSTRY_PATH.replace("Synthetic/", "EntirelyDifferent/"),
            industry_data=self.industry,
            model_path=self.model_path.replace("Synthetic/", "EntirelyDifferent/"),
            model_data=self.bank,
        )
        second = bank_applicability.assess_bank_applicability(**supplied)
        self.assertEqual(first.status, second.status)
        self.assertEqual(first.unknowns, second.unknowns)
        self.assertNotEqual(first.binding_sha256, second.binding_sha256)

    def test_unsupported_build_and_changed_or_modern_model_are_excluded(self):
        self.assertIn("unsupported-game-build", self.assess(game_sha256="f" * 64).exclusions)
        f = self.fixture
        for content in (b"x" + self.bank[1:], self.expected):
            with self.subTest(content=sha(content)):
                (f.source / self.model_path).write_bytes(content)
                result = self.assess()
                self.assertEqual(result.status, "excluded")
                self.assertIn("unsupported-bank-content", result.exclusions)

    def test_missing_duplicate_nested_and_unselected_references_are_excluded(self):
        f = self.fixture
        for scenario, industry in (
            (self.scenario.replace(b"RRT_Industries_Synthetic.xml", b"other.xml"), self.industry),
            (
                self.scenario.replace(
                    b"</RRTScenario>",
                    b"<IndustriesXMLFile>RRT_Industries_Synthetic.xml</IndustriesXMLFile></RRTScenario>",
                ),
                self.industry,
            ),
            (
                self.scenario,
                self.industry.replace(
                    b"<szModel>bank.nif</szModel>", b"<szModel>other.nif</szModel>"
                ),
            ),
            (
                self.scenario,
                self.industry.replace(
                    b"<szModel>bank.nif</szModel>", b"<Nested><szModel>bank.nif</szModel></Nested>"
                ),
            ),
            (
                self.scenario,
                self.industry.replace(
                    b"<szModel>bank.nif</szModel>", b"<szModel><Value>bank.nif</Value></szModel>"
                ),
            ),
            (
                self.scenario,
                self.industry.replace(
                    b"<szModel>bank.nif</szModel>",
                    b"<szModel>other.nif</szModel><szModel>bank.nif</szModel>",
                ),
            ),
            (b"<!DOCTYPE x><RRTScenario/>", self.industry),
        ):
            with self.subTest(scenario=sha(scenario), industry=sha(industry)):
                (f.source / f.SCENARIO_PATH).write_bytes(scenario)
                (f.source / f.INDUSTRY_PATH).write_bytes(industry)
                self.assertEqual(self.assess().status, "excluded")

    def test_case_packed_and_duplicate_loose_providers_stay_unknown(self):
        f = self.fixture
        cases = (
            (
                f.source / f.INDUSTRY_PATH,
                self.industry.replace(b"bank.nif", b"Bank.nif"),
                "reference-case-szModel",
            ),
            (f.source / "CustomAssets/opaque.FPK", b"invented packed provider", "packed-providers"),
            (f.stock / "bank.NIF", self.bank, "ambiguous-loose-bank.nif"),
            (
                f.source / "CustomAssets/RRT_Industries_Synthetic.xml",
                self.industry,
                "ambiguous-loose-RRT_Industries_Synthetic.xml",
            ),
        )
        for path, data, reason in cases:
            with self.subTest(reason=reason):
                original = path.read_bytes() if path.exists() else None
                path.write_bytes(data)
                result = self.assess()
                self.assertEqual(result.status, "unknown")
                self.assertIn(reason, result.unknowns)
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)

    def test_all_context_metadata_and_unaffected_content_bind_without_conversion(self):
        first = self.assess()
        f = self.fixture
        unrelated = f.source / "CustomAssets/unrelated.dds"
        unrelated.write_bytes(b"unchanged resource")
        second = self.assess()
        os.utime(unrelated, ns=(1_700_000_000_123_456_789,) * 2)
        third = self.assess()
        (f.stock / "other.dds").write_bytes(b"invented stock")
        fourth = self.assess()
        self.assertEqual(len({r.binding_sha256 for r in (first, second, third, fourth)}), 4)
        self.assertEqual({r.status for r in (first, second, third, fourth)}, {"source-candidate"})
        with patch.object(bank_model, "convert_legacy_bank", side_effect=AssertionError):
            self.assess()

    def test_stale_snapshot_or_bytes_refuse(self):
        with self.assertRaisesRegex(RuleError, "exact archive"):
            self.assess(archive_sha256="invalid")
        supplied = self.inputs()
        supplied["source_context"]["roots"][0]["files"].append({"planted": True})
        with self.assertRaisesRegex(RuleError, "unchanged file context"):
            bank_applicability.assess_bank_applicability(**supplied)
        with self.assertRaisesRegex(RuleError, "bytes differ"):
            self.assess(model_data=b"planted")

    def test_inspector_refuses_missing_selected_scenario_foreign_archive_build_and_links(self):
        f = self.fixture
        for changes in (
            dict(scenario_path="UserMaps/Synthetic/unselected.xml"),
            dict(model_path="UserMaps/../game"),
        ):
            with self.subTest(changes=changes), self.assertRaises(RuleError):
                self.inspect(**changes)
        foreign = type(f.record)(**(vars(f.record) | {"game_executable_sha256": "f" * 64}))
        with self.assertRaisesRegex(RuleError, "recorded build differs"):
            self.inspect(foreign)
        archive = f.originals / (f.ARCHIVE + ".7z")
        archive.write_bytes(b"foreign archive")
        with self.assertRaisesRegex(RuleError, "hash differs"):
            self.inspect()
        archive.write_bytes(b"synthetic archive")
        model = f.source / self.model_path
        model.unlink()
        model.symlink_to(f.executable)
        with self.assertRaises(ResourceFingerprintError):
            self.inspect()

    def test_inspector_detects_context_drift(self):
        f = self.fixture
        read = recipe_service._read

        def change_after_read(path, *args, **kwargs):
            result = read(path, *args, **kwargs)
            if path == f.source / self.model_path:
                (f.source / "CustomAssets/added.dds").write_bytes(b"external change")
            return result

        with patch.object(recipe_service, "_read", side_effect=change_after_read):
            with self.assertRaisesRegex(RuleError, "changed during inspection"):
                self.inspect()

    def test_shared_patch_prepares_only_exact_model_preserves_original_and_metadata(self):
        f = self.fixture
        before = {
            p.relative_to(f.source).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in f.source.rglob("*")
            if p.is_file()
        }
        model_patch = bank_applicability.compile_bank_patch(path=self.model_path, data=self.bank)
        self.assertEqual(model_patch.new, self.expected)
        rule = CompatibilityRule(
            "invented-bank-family",
            1,
            "Synthetic",
            "Mechanics only",
            f.ARCHIVE,
            f.GAME,
            (model_patch,),
        )
        prepared = f.root / "prepared"
        shutil.copytree(f.source, prepared, copy_function=shutil.copy2)
        source_context = map_file_context(f.source)
        apply_rules(prepared, f.ARCHIVE, f.GAME, (rule,), metadata_policy=PRESERVE_RESOURCE_MTIMES)
        self.assertEqual(
            map_file_context(prepared), expected_patched_context(source_context, (rule,))
        )
        expected = before | {self.model_path: (self.expected, before[self.model_path][1])}
        after = {
            p.relative_to(prepared).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in prepared.rglob("*")
            if p.is_file()
        }
        self.assertEqual(after, expected)
        self.assertEqual(
            {
                p.relative_to(f.source).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in f.source.rglob("*")
                if p.is_file()
            },
            before,
        )
        with self.assertRaisesRegex(RuleError, "conversion refused"):
            bank_applicability.compile_bank_patch(path=self.model_path, data=b"unknown")
        with patch.object(bank_model, "OUTPUT_SHA256", "f" * 64):
            with self.assertRaisesRegex(RuleError, "conversion refused"):
                bank_applicability.compile_bank_patch(path=self.model_path, data=self.bank)

    def test_source_binding_includes_conversion_contract(self):
        current = self.assess()
        with patch.object(bank_model, "OUTPUT_VERSION", 0x0A000101):
            self.assertNotEqual(current.binding_sha256, self.assess().binding_sha256)


if __name__ == "__main__":
    unittest.main()
