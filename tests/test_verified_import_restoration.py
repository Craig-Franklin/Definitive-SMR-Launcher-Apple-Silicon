"""Synthetic real import transactions, not native compatibility acceptance."""

from dataclasses import replace
from io import BytesIO
import hashlib
import os
import tarfile
import unittest
from unittest.mock import patch

import test_application as fixtures
from smr_launcher import recipe_service
from smr_launcher.application import ApplicationError
from smr_launcher.variants import assets_manifest_hash
from smr_launcher.native_backend import NativeLibrary
from smr_launcher.native_map_tools import dispatch
from smr_launcher.play_history import context


def sha(data):
    return hashlib.sha256(data).hexdigest()


class VerifiedImportRestorationTests(unittest.TestCase):
    def setUp(self):
        fixture = self.fixture = fixtures.ApplicationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.app = fixture.application
        exe = fixture.root / "executable"
        exe.write_bytes(b"test game")
        fixture.installation.executable = exe
        stock = self.stock = (
            fixture.installation.steamapps_root
            / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        )
        stock.mkdir(parents=True)
        industry_stock = b"<RRTIndustries><Industries><RRTIndustry><szName>Example Mill</szName></RRTIndustry></Industries></RRTIndustries>"
        depot_stock = b"<RRTDepots><Depot><szName>Depot</szName></Depot></RRTDepots>"
        (stock / "RRT_Industries.xml").write_bytes(industry_stock)
        (stock / "RRT_Depots.xml").write_bytes(depot_stock)
        self.industry_path = "UserMaps/Synthetic/RRT_Industries_Synthetic.xml"
        self.scenario_path = "UserMaps/Synthetic/RRT_Scenario_Synthetic.xml"
        self.before = (
            b"<RRTIndustries><Industries><RRTIndustry><szName>Example Mill </szName>"
            b"<bInCity>1</bInCity><bIsMountainous>0</bIsMountainous>"
            b"<szModel>authored.kfm</szModel><Production><Input>Crop</Input><Output>Food</Output></Production>"
            b"<Locations><Location><StartX>17</StartX></Location></Locations>"
            b"</RRTIndustry></Industries></RRTIndustries>"
        )
        self.intended = self.before.replace(b"Example Mill </szName>", b"Example Mill</szName>")
        self.scenario = (
            b"<Scenario><IndustriesXMLFile>RRT_Industries_Synthetic.xml</IndustriesXMLFile>"
            b"<OwnListItem><szObjectType>Industry</szObjectType><szObjectName>Example Mill</szObjectName>"
            b"</OwnListItem><Events>authored-events.xml</Events></Scenario>"
        )
        source = fixture.root / "authoring"
        files = {
            self.industry_path: self.before,
            self.scenario_path: self.scenario,
            "CustomAssets/authored-events.xml": b"<Events><Effect>keep</Effect></Events>",
        }
        with tarfile.open(fixture.archive, "w") as stream:
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                stream.addfile(entry, BytesIO(data))
                file = source / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
        self.recipe = recipe_service._IdentityRecipe(
            recipe_id="synthetic-sealed-import-v1",
            archive_sha256=sha(fixture.archive.read_bytes()),
            game_sha256=sha(b"test game"),
            assets_sha256=assets_manifest_hash(source),
            scenario_path=self.scenario_path,
            scenario_sha256=sha(self.scenario),
            industry_path=self.industry_path,
            industry_before=sha(self.before),
            industry_after=sha(self.intended),
            stock_industries_sha256=sha(industry_stock),
            stock_depots_sha256=sha(depot_stock),
            aliases=(("Example Mill ", 0, "Example Mill"),),
            native_acceptance="synthetic-only-not-native-proof",
            verified_stock_files_sha256=self.app._stock_file_context()["identity"],
            preserved_goal_path=("Scenario", "OwnListItem", "szObjectName"),
        )
        choice = recipe_service.RecipeChoice(self.recipe.recipe_id, "Synthetic", "Synthetic")
        for patch_context in (
            patch.dict(recipe_service._RECIPES, {self.recipe.recipe_id: self.recipe}),
            patch.dict(recipe_service._CHOICES, {self.recipe.recipe_id: choice}),
            patch.object(
                recipe_service,
                "extract_supported_industry_registry",
                return_value=("Example Mill",),
            ),
        ):
            patch_context.start()
            self.addCleanup(patch_context.stop)
        self.app.setup()
        self.live_before = fixture.snapshot_files(self.app.profiles.live)
        self.archive_before = fixture.archive.read_bytes()

    def test_import_and_reimport_preserve_original_and_create_one_unverified_separate_edition(self):
        original = self.app.import_archive(self.fixture.archive)
        editions = [r for r in self.app.catalogue() if r != original]
        self.assertEqual(len(editions), 1)
        edition = editions[0]
        self.assertIn(" — Restored sealed author intent", edition.name)
        self.assertEqual(
            (self.app._prepared_root(original) / self.industry_path).read_bytes(), self.before
        )
        self.assertEqual(
            (self.app._prepared_root(edition) / self.industry_path).read_bytes(), self.intended
        )
        self.assertEqual(
            (self.app._prepared_root(edition) / self.scenario_path).read_bytes(), self.scenario
        )
        self.assertEqual(self.fixture.snapshot_files(self.app.profiles.live), self.live_before)
        self.assertEqual(self.fixture.archive.read_bytes(), self.archive_before)
        self.assertIsNone(self.app.verification_for(edition))
        self.assertEqual(self.app.import_checks(edition)["runtime_acceptance"], "not_tested")
        self.assertEqual(self.app.import_archive(self.fixture.archive), original)
        self.assertEqual(len(self.app.catalogue()), 2)
        self.app._validate_recipe_context(edition, prepared_output=True)

    def test_changed_stock_withholds_restoration_after_preserving_original(self):
        (self.stock / "changed.kfm").write_bytes(b"new stock dependency")
        with self.assertRaisesRegex(ApplicationError, "Original imported and preserved.*withheld"):
            self.app.import_archive(self.fixture.archive)
        self.assertEqual(len(self.app.catalogue()), 1)
        self.assertTrue(self.app._is_original_variant(self.app.catalogue()[0]))
        self.assertEqual(self.fixture.snapshot_files(self.app.profiles.live), self.live_before)
        self.assertEqual(self.fixture.archive.read_bytes(), self.archive_before)

    def test_unknown_archive_never_runs_automatic_restoration(self):
        with self.fixture.archive.open("ab") as stream:
            stream.write(b"changed archive container")
        original = self.app.import_archive(self.fixture.archive)
        self.assertEqual(self.app.catalogue(), (original,))

    def test_experimental_recipe_is_never_automatically_applied(self):
        recipe_service._RECIPES[self.recipe.recipe_id] = replace(
            self.recipe,
            native_acceptance="",
            verified_stock_files_sha256="",
            preserved_goal_path=(),
        )
        original = self.app.import_archive(self.fixture.archive)
        self.assertEqual(self.app.catalogue(), (original,))

    def test_changed_source_refuses_reimport_and_preserves_existing_edition(self):
        original = self.app.import_archive(self.fixture.archive)
        edition = next(r for r in self.app.catalogue() if r != original)
        source = self.app.imports / original.imported_directory / self.industry_path
        source.parent.chmod(0o700)
        source.chmod(0o600)
        source.write_bytes(self.before + b" ")
        with self.assertRaisesRegex(ApplicationError, "restoration withheld"):
            self.app.import_archive(self.fixture.archive)
        self.assertEqual(len(self.app.catalogue()), 2)
        self.assertEqual(
            (self.app._prepared_root(edition) / self.industry_path).read_bytes(), self.intended
        )

    def test_changed_rule_or_compiler_invalidates_old_verified_binding(self):
        original = self.app.import_archive(self.fixture.archive)
        edition = next(r for r in self.app.catalogue() if r != original)
        with patch.object(recipe_service, "compiler_contract_digest", return_value="f" * 64):
            with self.assertRaisesRegex(ApplicationError, "revalidate"):
                self.app._validate_recipe_context(edition)
        recipe_service._RECIPES[self.recipe.recipe_id] = replace(
            self.recipe, native_acceptance="changed-review"
        )
        with self.assertRaisesRegex(ApplicationError, "revalidate"):
            self.app._validate_recipe_context(edition)

    def test_manual_first_preparation_keeps_acceptance_marker_and_refuses_revocation(self):
        with patch.object(self.app, "_restore_verified_import_locked"):
            original = self.app.import_archive(self.fixture.archive)
        edition = self.app.create_recipe_edition(original, self.recipe.recipe_id)
        receipt = self.app._validate_recipe_context(edition)
        self.assertEqual(receipt["verified_transformation"], self.recipe.native_acceptance)
        del recipe_service._RECIPES[self.recipe.recipe_id]
        with self.assertRaisesRegex(ApplicationError, "revalidate"):
            self.app._validate_recipe_context(edition)

    def test_real_inspector_explains_correction_and_refuses_stale_provenance(self):
        original = self.app.import_archive(self.fixture.archive)
        edition = next(r for r in self.app.catalogue() if r != original)
        params = dict(context(edition, self.recipe.game_sha256), profile_id=edition.profile_id)
        result = dispatch(NativeLibrary(), self.app, "maptools.inspect", params)
        self.assertIn("Recorded preparation", result["preparation_text"])
        self.assertIn(self.recipe.native_acceptance, result["preparation_text"])
        self.assertIn(self.recipe.industry_before, result["preparation_text"])
        self.assertIn(self.recipe.industry_after, result["preparation_text"])
        self.assertIn(
            "does not award this edition gameplay verification", result["preparation_text"]
        )
        self.assertNotIn("preparation_error", result)
        (self.stock / "changed.kfm").write_bytes(b"changed context")
        result = dispatch(NativeLibrary(), self.app, "maptools.inspect", params)
        self.assertNotIn("preparation_text", result)
        self.assertIn("stale or unavailable", result["preparation_error"])

    def test_stock_content_change_with_same_mtime_invalidates_prepared_edition(self):
        original = self.app.import_archive(self.fixture.archive)
        edition = next(r for r in self.app.catalogue() if r != original)
        stock = self.stock / "RRT_Industries.xml"
        before = stock.stat()
        stock.write_bytes(stock.read_bytes() + b" ")
        os.utime(stock, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ApplicationError, "revalidate"):
            self.app._validate_recipe_context(edition)


if __name__ == "__main__":
    unittest.main()
