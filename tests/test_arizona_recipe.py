"""Invented combined model/ownership recipe; no game assets or live application."""

from contextlib import ExitStack
from dataclasses import replace
import hashlib
import os
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smr_launcher import bank_model, recipe_service
from smr_launcher.compatibility_recipes import IdentityEdit
from smr_launcher.rules import PRESERVE_RESOURCE_MTIMES, RuleError, apply_rules
from smr_launcher.variants import assets_manifest_hash
from test_bank_model import synthetic_model
import test_recipe_service


def sha(data):
    return hashlib.sha256(data).hexdigest()


class CombinedRecipeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recipe_service.SyntheticRecipeServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.goal_path = ("Scenario", "OwnList", "OwnListItem", "szObjectName")
        self.scenario = (
            b"<Scenario><IndustriesXMLFile>RRT_Industries_Synthetic.xml</IndustriesXMLFile>"
            b"<Description>Keep Test Mill running</Description><OwnList>"
            b"<OwnListItem><szObjectType>Industry</szObjectType><szObjectName>Test Mill</szObjectName>"
            b"<szCity>Town One</szCity></OwnListItem>"
            b"<OwnListItem><szObjectType>Industry</szObjectType><szObjectName>Test Mill</szObjectName>"
            b"<szCity>Town Two</szCity></OwnListItem>"
            b"<OwnListItem><szObjectType>Industry</szObjectType><szObjectName>Other Factory</szObjectName>"
            b"</OwnListItem></OwnList></Scenario>"
        )
        (f.source / f.SCENARIO_PATH).write_bytes(self.scenario)
        self.model_path = "UserMaps/Synthetic/Warehouse/bank.nif"
        self.bank, _ = synthetic_model()
        self.target, _ = synthetic_model(modern=True)
        (f.source / self.model_path).write_bytes(self.bank)
        self.recipe = replace(
            f._recipe(scenario=self.scenario),
            scenario_after=sha(self.expected_scenario()),
            ownership_edits=(IdentityEdit("industry-goal", f.OLD, f.TARGET, 2, self.goal_path),),
            legacy_bank_path=self.model_path,
        )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Admit only this invented graph to the real exact-input converter.
        for name, value in (
            ("SOURCE_SHA256", sha(self.bank)),
            ("OUTPUT_SHA256", sha(self.target)),
            ("SOURCE_SIZE", len(self.bank)),
            ("OUTPUT_SIZE", len(self.target)),
        ):
            self.stack.enter_context(patch.object(bank_model, name, value))

    def expected_scenario(self):
        return self.scenario.replace(
            b"<szObjectName>Test Mill</szObjectName>",
            b"<szObjectName>Registered One</szObjectName>",
        )

    def snapshot(self, root):
        return {
            p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*")
            if p.is_file()
        }

    def test_combined_prepared_copy_preserves_features_source_mtimes_and_idempotence(self):
        f = self.fixture
        mtime = 1_700_000_000_123_456_789
        for p in f.source.rglob("*"):
            if p.is_file():
                os.utime(p, ns=(mtime, mtime))
        original = self.snapshot(f.source)
        rules, _ = f.resolve(self.recipe)
        prepared = f.root / "prepared"
        shutil.copytree(f.source, prepared, copy_function=shutil.copy2)
        apply_rules(prepared, f.ARCHIVE, f.GAME, rules, metadata_policy=PRESERVE_RESOURCE_MTIMES)
        expected = dict(original)
        expected[f.SCENARIO_PATH] = (self.expected_scenario(), mtime)
        expected[f.INDUSTRY_PATH] = (
            f.industry_data.replace(
                b"<szName>Test Mill</szName>", b"<szName>Registered One</szName>"
            ),
            mtime,
        )
        expected[self.model_path] = (self.target, mtime)
        self.assertEqual(self.snapshot(prepared), expected)
        self.assertEqual(self.snapshot(f.source), original)
        inodes = {p: p.stat().st_ino for p in prepared.rglob("*") if p.is_file()}
        apply_rules(prepared, f.ARCHIVE, f.GAME, rules, metadata_policy=PRESERVE_RESOURCE_MTIMES)
        self.assertEqual(self.snapshot(prepared), expected)
        self.assertEqual({p: p.stat().st_ino for p in inodes}, inodes)

    def test_ownership_type_count_alias_and_output_mismatch_refuse_without_writes(self):
        f = self.fixture
        wrong_type = self.scenario.replace(b"<szObjectType>Industry", b"<szObjectType>Good", 1)
        for scenario, edits, after in (
            (wrong_type, self.recipe.ownership_edits, sha(self.expected_scenario())),
            (
                self.scenario,
                (replace(self.recipe.ownership_edits[0], count=1),),
                self.recipe.scenario_after,
            ),
            (
                self.scenario,
                (replace(self.recipe.ownership_edits[0], target="Registered Two"),),
                self.recipe.scenario_after,
            ),
            (
                self.scenario,
                (
                    replace(
                        self.recipe.ownership_edits[0],
                        goal_path=("Scenario", "Other", "OwnListItem", "szObjectName"),
                    ),
                ),
                self.recipe.scenario_after,
            ),
            (self.scenario, self.recipe.ownership_edits, "f" * 64),
        ):
            with self.subTest(scenario=sha(scenario), edits=edits, after=after):
                (f.source / f.SCENARIO_PATH).write_bytes(scenario)
                recipe = replace(
                    self.recipe,
                    assets_sha256=assets_manifest_hash(f.source),
                    scenario_sha256=sha(scenario),
                    ownership_edits=edits,
                    scenario_after=after,
                )
                before = self.snapshot(f.source)
                with self.assertRaises(RuleError):
                    f.resolve(recipe)
                self.assertEqual(self.snapshot(f.source), before)

    def test_unknown_model_duplicate_provider_and_undeclared_reference_refuse(self):
        f = self.fixture
        cases = (
            (f.source / self.model_path, b"unknown model"),
            (f.source / "CustomAssets/bank.nif", self.bank),
            (f.source / "CustomAssets/extra.xml", b"<Extra><Name>Test Mill</Name></Extra>"),
        )
        for path, data in cases:
            with self.subTest(path=path.name):
                original = path.read_bytes() if path.exists() else None
                path.write_bytes(data)
                recipe = replace(self.recipe, assets_sha256=assets_manifest_hash(f.source))
                before = self.snapshot(f.source)
                with self.assertRaises(RuleError):
                    f.resolve(recipe)
                self.assertEqual(self.snapshot(f.source), before)
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)

    def test_unreviewed_scenario_occurrence_or_case_variant_refuses(self):
        f = self.fixture
        for data in (
            self.scenario.replace(b"</Scenario>", b"<Extra>Registered One</Extra></Scenario>"),
            self.scenario.replace(b"<szObjectName>Test Mill", b"<szObjectName>test mill", 1),
        ):
            with self.subTest(scenario=sha(data)):
                (f.source / f.SCENARIO_PATH).write_bytes(data)
                recipe = replace(
                    self.recipe,
                    assets_sha256=assets_manifest_hash(f.source),
                    scenario_sha256=sha(data),
                )
                before = self.snapshot(f.source)
                with self.assertRaises(RuleError):
                    f.resolve(recipe)
                self.assertEqual(self.snapshot(f.source), before)


# Source compilation may be reviewed independently of activation admission.
class ArizonaChoiceTests(unittest.TestCase):
    RECIPE = "az-canyon-v02-bank-and-industry-identifiers-v1"
    GAME = "981ca7180759d8578babf54cd9a4f76b93db503b33a7f1938c6b1427b95680f0"

    def record(self, **changed):
        fields = dict(
            archive_sha256="399570b00bb4c3590dacd30b45322b65f72029237ee49ed25be0d4f5c637e5cf",
            game_executable_sha256=self.GAME,
            variant_id="53264553fb42395c05c750e3a6d9769adb6c05d4dfe16233d76f99ad89f760ab",
            scenarios=("UserMaps/AZCanyonV2/RRT_Scenario_User_AZCanyonV2.xml",),
        )
        fields.update(changed)
        return SimpleNamespace(**fields)

    def test_exact_original_offers_experimental_choice_with_unverified_gameplay(self):
        choices = recipe_service.recipe_choices(self.record(), self.GAME)
        self.assertEqual(tuple(c.recipe_id for c in choices), (self.RECIPE,))
        self.assertEqual(choices[0].title, "Arizona — experimental repair")
        self.assertIn(
            "Loading, saving and reopening this edition remain unverified", choices[0].description
        )
        for original, alias in (
            ("Steamboat Company", "Wooden Horse Factory"),
            ("Hotel", "Doll Factory"),
            ("Bank", "Bakery"),
            ("Air Force Base", "Toy Soldier Factory"),
        ):
            self.assertIn(original + " uses " + alias, choices[0].description)
        self.assertIn("Oil/Power Plant", choices[0].description)
        self.assertIn("events", choices[0].description)

    def test_other_archives_builds_scenarios_and_prepared_editions_are_not_offered(self):
        for changed in (
            dict(archive_sha256="0" * 64),
            dict(game_executable_sha256="0" * 64),
            dict(variant_id="0" * 64),
            dict(scenarios=("UserMaps/Other/Scenario.xml",)),
        ):
            with self.subTest(changed=changed):
                self.assertEqual(
                    recipe_service.recipe_choices(self.record(**changed), self.GAME), ()
                )
        self.assertEqual(recipe_service.recipe_choices(self.record(), "0" * 64), ())
