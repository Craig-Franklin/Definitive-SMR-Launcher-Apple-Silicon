import hashlib
import json
from dataclasses import asdict, replace
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from smr_launcher import recipe_service
from smr_launcher.activation import ActivationError
from smr_launcher.recipe_identity import file_context
from smr_launcher.rules import PRESERVE_RESOURCE_MTIMES, RuleError, apply_rules
from smr_launcher.variants import assets_manifest_hash


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SyntheticRecipeServiceTests(unittest.TestCase):
    RECIPE_ID = "synthetic-identities-v1"
    ARCHIVE = hashlib.sha256(b"synthetic archive").hexdigest()
    GAME = hashlib.sha256(b"synthetic executable").hexdigest()
    REGISTRY = ("Registered One", "Registered Two", "Registered Three")
    SCENARIO_PATH = "UserMaps/Synthetic/RRT_Scenario.xml"
    INDUSTRY_PATH = "UserMaps/Synthetic/RRT_Industries_Synthetic.xml"
    OLD = "Test Mill"
    TARGET = "Registered One"

    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.imports = self.root / "imports"
        self.source = self.imports / ("a" * 32)
        (self.source / "UserMaps/Synthetic").mkdir(parents=True)
        (self.source / "CustomAssets").mkdir()
        self.stock = self.root / "stock" / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        self.stock.mkdir(parents=True)
        self.executable = self.root / "game"
        self.executable.write_bytes(b"synthetic executable")
        self.originals = self.root / "originals"
        self.originals.mkdir()
        (self.originals / (self.ARCHIVE + ".7z")).write_bytes(b"synthetic archive")
        self.scenario_data = (
            b"<Scenario><IndustriesXMLFile>RRT_Industries_Synthetic.xml</IndustriesXMLFile>"
            b"</Scenario>"
        )
        self.industry_data = (
            b"<RRTIndustries><Industries><RRTIndustry>"
            b"<szName>Test Mill</szName><bInCity>1</bInCity>"
            b"<bIsMountainous>0</bIsMountainous><szSecondaryModel>test_stockpile.kfm</szSecondaryModel>"
            b"</RRTIndustry></Industries></RRTIndustries>"
        )
        self.stock_industries_data = (
            b"<RRTIndustries><Industries><RRTIndustry><szName>Stock Factory</szName>"
            b"</RRTIndustry></Industries></RRTIndustries>"
        )
        self.stock_depots_data = (
            b"<RRTDepots><Depot><szName>Stock Depot</szName></Depot></RRTDepots>"
        )
        (self.source / self.SCENARIO_PATH).write_bytes(self.scenario_data)
        (self.source / self.INDUSTRY_PATH).write_bytes(self.industry_data)
        (self.stock / "RRT_Industries.xml").write_bytes(self.stock_industries_data)
        (self.stock / "RRT_Depots.xml").write_bytes(self.stock_depots_data)
        (self.source / "UserMaps/Synthetic/Warehouse").mkdir()
        (self.source / "UserMaps/Synthetic/Warehouse/test_stockpile.kfm").write_bytes(
            b"KFM synthetic"
        )
        self.recipe = self._recipe()
        self.app = self._app()
        self.record = SimpleNamespace(
            archive_sha256=self.ARCHIVE,
            game_executable_sha256=self.GAME,
            imported_directory=self.source.name,
            scenarios=(self.SCENARIO_PATH,),
        )

    def _recipe(
        self,
        *,
        scenario=None,
        industry=None,
        aliases=None,
        stock_industries=None,
        stock_depots=None,
        recipe_id=None,
    ):
        scenario = self.scenario_data if scenario is None else scenario
        industry = self.industry_data if industry is None else industry
        stock_industries = (
            self.stock_industries_data if stock_industries is None else stock_industries
        )
        stock_depots = self.stock_depots_data if stock_depots is None else stock_depots
        output = industry.replace(b"<szName>Test Mill</szName>", b"<szName>Registered One</szName>")
        return recipe_service._IdentityRecipe(
            recipe_id or self.RECIPE_ID,
            self.ARCHIVE,
            self.GAME,
            assets_manifest_hash(self.source),
            self.SCENARIO_PATH,
            sha(scenario),
            self.INDUSTRY_PATH,
            sha(industry),
            sha(output),
            sha(stock_industries),
            sha(stock_depots),
            aliases or ((self.OLD, 0, self.TARGET),),
        )

    def _app(self):
        installation = SimpleNamespace(
            executable=self.executable,
            executable_sha256=self.GAME,
            steamapps_root=self.root / "stock",
        )

        class FakeApplication:
            def __init__(inner_self):
                inner_self.installation = installation
                inner_self.imports = self.imports
                inner_self.originals = self.originals

            def _stock_file_context(inner_self):
                return file_context(
                    {"installed_assets": self.stock}, required_roles=("installed_assets",)
                )

        return FakeApplication()

    def resolve(self, recipe=None, record=None):
        with patch.object(
            recipe_service, "extract_supported_industry_registry", return_value=self.REGISTRY
        ):
            return recipe_service._resolve(self.app, record or self.record, recipe or self.recipe)

    def test_compiles_exact_reviewed_patch_and_binds_source_stock_and_compiler(self):
        rules, binding = self.resolve()
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        patch_result = rule.patches[0]
        expected = self.industry_data.replace(
            b"<szName>Test Mill</szName>", b"<szName>Registered One</szName>"
        )
        self.assertEqual(rule.rule_id, self.RECIPE_ID)
        self.assertEqual(rule.version, 2)
        self.assertEqual(rule.archive_sha256, self.ARCHIVE)
        self.assertEqual(rule.game_executable_sha256, self.GAME)
        self.assertEqual(patch_result.path, self.INDUSTRY_PATH)
        self.assertEqual(patch_result.old, self.industry_data)
        self.assertEqual(patch_result.new, expected)
        self.assertIn(b"<szSecondaryModel>test_stockpile.kfm</szSecondaryModel>", patch_result.new)
        self.assertEqual(
            patch_result.new.replace(
                b"<szName>Registered One</szName>", b"<szName>Test Mill</szName>"
            ),
            patch_result.old,
        )
        self.assertEqual(patch_result.before_sha256, sha(self.industry_data))
        self.assertEqual(patch_result.after_sha256, sha(expected))
        self.assertEqual(binding.recipe_id, self.RECIPE_ID)
        self.assertEqual(
            binding.source_files_sha256,
            file_context(
                {
                    "user_maps": self.source / "UserMaps",
                    "custom_assets": self.source / "CustomAssets",
                }
            )["identity"],
        )
        self.assertEqual(binding.stock_files_sha256, self.app._stock_file_context()["identity"])
        self.assertEqual(
            binding.recipe_sha256,
            recipe_service._sha(
                json.dumps(asdict(self.recipe), sort_keys=True, separators=(",", ":")).encode()
            ),
        )
        self.assertEqual(binding.compiler_sha256, recipe_service.compiler_contract_digest())
        binding_json = binding.as_json()
        self.assertEqual(binding_json["recipe_id"], self.RECIPE_ID)
        self.assertEqual(
            binding_json["file_context_algorithm"], "sha256-resource-files-content-mtime-ns-v1"
        )
        self.assertEqual(binding_json["metadata_policy"], "preserve-imported-resource-mtimes-v1")

    def test_public_resolver_rejects_unknown_ids_without_touching_app(self):
        with self.assertRaisesRegex(RuleError, "Unknown compatibility recipe"):
            recipe_service.resolve_bundled_recipe(None, None, "not-bundled")
        with self.assertRaisesRegex(RuleError, "Unknown compatibility recipe"):
            recipe_service.resolve_bundled_recipe(None, None, object())

    def application_fixture(self):
        tests_directory = str(Path(__file__).resolve().parent)
        sys.path.insert(0, tests_directory)
        try:
            from test_application import ApplicationTests
        finally:
            sys.path.remove(tests_directory)
        fixture = ApplicationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_public_named_api_rejects_unknown_recipe_without_publication(self):
        fixture = self.application_fixture()
        app = fixture.application
        app.setup()
        original = app.import_archive(fixture.archive)
        catalogue_before = app.catalogue()
        prepared_before = set(app.prepared.iterdir())

        with self.assertRaisesRegex(RuleError, "Unknown compatibility recipe"):
            app.create_recipe_edition(original, "unknown-synthetic-recipe")

        self.assertEqual(app.catalogue(), catalogue_before)
        self.assertEqual(set(app.prepared.iterdir()), prepared_before)
        self.assertFalse((app.library / "compatibility-preparations").exists())

    def test_public_named_api_prepares_and_activates_a_compiled_synthetic_recipe(self):
        fixture = self.application_fixture()
        original, rule, binding, _stock = fixture.compiled_fixture()
        app = fixture.application
        imported = app.imports / original.imported_directory
        source_before = fixture.snapshot_files(imported)
        archive_before = fixture.archive.read_bytes()

        with patch.object(
            recipe_service, "resolve_bundled_recipe", return_value=((rule,), binding)
        ) as resolve:
            compiled = app.create_recipe_edition(original, binding.recipe_id)

        resolve.assert_called_once_with(app, original, binding.recipe_id)
        self.assertIn(compiled, app.catalogue())
        self.assertIn("Experimental " + binding.recipe_id, compiled.name)
        self.assertEqual(compiled.archive_sha256, original.archive_sha256)
        self.assertTrue(compiled.recipe_receipt_sha256)
        self.assertEqual(fixture.snapshot_files(imported), source_before)
        self.assertEqual(fixture.archive.read_bytes(), archive_before)
        self.assertEqual(
            (app.prepared / compiled.prepared_directory / "CustomAssets/Example.txt").read_bytes(),
            b"compatible map asset",
        )
        self.assertFalse(app.verification_for(compiled))

        app.activate(compiled.profile_id)
        self.assertEqual(app.active(), compiled.profile_id)
        self.assertIsNone(app.verification_for(compiled))

    def test_wrong_archive_game_and_scenario_are_rejected(self):
        bad_records = (
            SimpleNamespace(**{**vars(self.record), "archive_sha256": "c" * 64}),
            SimpleNamespace(**{**vars(self.record), "game_executable_sha256": "c" * 64}),
            SimpleNamespace(**{**vars(self.record), "scenarios": ("UserMaps/Other/Scenario.xml",)}),
        )
        for bad in bad_records:
            with self.subTest(record=bad), self.assertRaisesRegex(RuleError, "does not apply"):
                self.resolve(record=bad)
        self.app.installation.executable_sha256 = "d" * 64
        with self.assertRaisesRegex(RuleError, "does not apply"):
            self.resolve()

    def test_changed_source_inventory_is_rejected(self):
        (self.source / "UserMaps/Synthetic/extra.bin").write_bytes(b"added after recipe review")
        with self.assertRaisesRegex(RuleError, "asset inventory differs"):
            self.resolve()

    def test_tampered_retained_archive_is_rejected(self):
        (self.originals / (self.ARCHIVE + ".7z")).write_bytes(b"tampered synthetic archive")
        with self.assertRaisesRegex(RuleError, "input hash differs"):
            self.resolve()

    def test_missing_or_duplicate_stock_default_reader_is_rejected(self):
        stock_industries = self.stock / "RRT_Industries.xml"
        stock_industries.unlink()
        with self.assertRaisesRegex(RuleError, "reader basename"):
            self.resolve()

        stock_industries.write_bytes(self.stock_industries_data)
        (self.source / "UserMaps/Synthetic/RRT_Industries.xml").write_bytes(b"duplicate basename")
        self.recipe = self._recipe()
        with self.assertRaisesRegex(RuleError, "reader basename"):
            self.resolve()

    def test_fpk_provider_is_rejected_even_when_recipe_inputs_match(self):
        (self.source / "CustomAssets/opaque.FPK").write_bytes(b"opaque packed resource")
        self.recipe = self._recipe()
        with self.assertRaisesRegex(RuleError, "packed providers"):
            self.resolve()

    def test_stock_inherited_target_collision_is_rejected(self):
        collision = (
            b"<RRTIndustries><Industries><RRTIndustry>"
            b"<szName>Stock Factory</szName></RRTIndustry><RRTIndustry>"
            b"<szName>Registered One</szName></RRTIndustry></Industries></RRTIndustries>"
        )
        (self.stock / "RRT_Industries.xml").write_bytes(collision)
        self.recipe = self._recipe(stock_industries=collision)
        with self.assertRaisesRegex(RuleError, "collision or stock-absent"):
            self.resolve()

    def test_missing_or_malformed_identity_flags_are_rejected(self):
        cases = (
            (
                self.industry_data.replace(b"<bInCity>1</bInCity>", b""),
                "explicit reviewed city/mountain fields",
            ),
            (
                self.industry_data.replace(b"<bIsMountainous>0</bIsMountainous>", b""),
                "explicit reviewed city/mountain fields",
            ),
            (
                self.industry_data.replace(b"<bInCity>1</bInCity>", b"<bInCity>true</bInCity>"),
                "explicit reviewed city/mountain fields",
            ),
            (
                self.industry_data.replace(
                    b"<bIsMountainous>0</bIsMountainous>",
                    b"<bIsMountainous>0</bIsMountainous><bIsMountainous>0</bIsMountainous>",
                ),
                "explicit reviewed city/mountain fields",
            ),
            (self.industry_data.replace(b"</RRTIndustries>", b""), "not well formed"),
        )
        for index, (data, message) in enumerate(cases):
            with self.subTest(index=index):
                (self.source / self.INDUSTRY_PATH).write_bytes(data)
                self.recipe = self._recipe(industry=data)
                with self.assertRaisesRegex(RuleError, message):
                    self.resolve()

    def test_empty_identity_flags_refuse_without_mutating_any_fixture(self):
        cases = (
            (b"<bInCity>1</bInCity>", b"<bInCity/>"),
            (b"<bInCity>1</bInCity>", b"<bInCity></bInCity>"),
            (b"<bIsMountainous>0</bIsMountainous>", b"<bIsMountainous/>"),
            (b"<bIsMountainous>0</bIsMountainous>", b"<bIsMountainous></bIsMountainous>"),
        )
        for explicit, empty in cases:
            with self.subTest(empty=empty):
                data = self.industry_data.replace(explicit, empty)
                (self.source / self.INDUSTRY_PATH).write_bytes(data)
                self.recipe = self._recipe(industry=data)
                before = self.fixture_file_facts()
                with self.assertRaisesRegex(RuleError, "explicit reviewed city/mountain fields"):
                    self.resolve()
                self.assertEqual(self.fixture_file_facts(), before)
                self.assertFalse((self.root / "prepared").exists())

    def test_other_xml_alias_occurrence_and_case_variant_goal_are_rejected(self):
        exact_alias = b"<RRTScenario><Label>Test Mill</Label></RRTScenario>"
        path = self.source / "UserMaps/Synthetic/other.xml"
        path.write_bytes(exact_alias)
        self.recipe = self._recipe()
        with self.assertRaisesRegex(RuleError, "unreviewed identity occurrence"):
            self.resolve()

        path.write_bytes(
            b"<RRTScenario><Goals><OwnListItem><szObjectType>Industry</szObjectType>"
            b"<szObjectName>TEST MILL</szObjectName></OwnListItem></Goals></RRTScenario>"
        )
        self.recipe = self._recipe()
        with self.assertRaisesRegex(RuleError, "unreviewed identity occurrence"):
            self.resolve()

    def test_changed_context_is_rejected_before_any_publication(self):
        original = recipe_service.map_file_context
        calls = 0

        def change_after_read(root):
            nonlocal calls
            calls += 1
            context = original(root)
            if calls == 2:
                context = dict(context, identity="f" * 64)
            return context

        with (
            patch.object(recipe_service, "map_file_context", side_effect=change_after_read),
            patch.object(
                recipe_service, "extract_supported_industry_registry", return_value=self.REGISTRY
            ),
        ):
            with self.assertRaisesRegex(RuleError, "changed during compilation"):
                recipe_service._resolve(self.app, self.record, self.recipe)
        self.assertEqual(calls, 2)
        self.assertEqual((self.source / self.INDUSTRY_PATH).read_bytes(), self.industry_data)
        self.assertFalse((self.root / "prepared").exists())

    def donor_fixture(self):
        """Invented members and identities; no real donor archive or asset data."""
        donor = self.imports / ("b" * 32)
        members = (
            ("UserMaps/Donor/one.nif", b"invented model\0\xff"),
            ("UserMaps/Donor/two.kfm", b"invented animation\0\xfe"),
        )
        additions = []
        for path, data in members:
            file = donor / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
            os.utime(file, ns=(1_700_000_000_123_456_789,) * 2)
            destination = path.replace("/Donor/", "/Synthetic/")
            additions.append((destination, path, sha(data)))
        archive, variant = sha(b"invented donor archive"), sha(b"invented donor variant")
        record = SimpleNamespace(
            archive_sha256=archive,
            variant_id=variant,
            profile_id="map-" + variant,
            imported_directory=donor.name,
            name="Side_to_Side_2_v1_00",
        )
        self.app.catalogue = lambda: [record]
        return replace(self.recipe, additions=tuple(additions)), record, donor, members

    def fixture_file_facts(self):
        """Compare file and link facts independently; exclude read-sensitive atime."""
        result = {}
        for path in self.root.rglob("*"):
            info = path.lstat()
            if path.is_symlink():
                data = ("link", os.readlink(path))
            elif path.is_file():
                data = ("file", path.read_bytes())
            else:
                continue
            result[path.relative_to(self.root).as_posix()] = (
                data,
                info.st_ino,
                info.st_mtime_ns,
                info.st_mode,
            )
        return result

    def test_exact_donor_members_compile_without_mutating_any_fixture(self):
        recipe, donor_record, _, members = self.donor_fixture()
        before = self.fixture_file_facts()
        with patch.object(
            recipe_service, "_HILL_COWHIDE_DONOR_ARCHIVE", donor_record.archive_sha256
        ):
            rules, binding = self.resolve(recipe)
        self.assertEqual(len(rules), 1)
        self.assertEqual(len(rules[0].additions), 2)
        for addition, (path, data) in zip(rules[0].additions, members):
            self.assertEqual(addition.path, path.replace("/Donor/", "/Synthetic/"))
            self.assertEqual(addition.data, data)
            self.assertEqual(addition.after_sha256, sha(data))
            self.assertEqual(addition.mtime_ns, 1_700_000_000_123_456_789)
        self.assertEqual(rules[0].patches[0].old, self.industry_data)
        self.assertEqual(
            rules[0].patches[0].new,
            self.industry_data.replace(
                b"<szName>Test Mill</szName>", b"<szName>Registered One</szName>"
            ),
        )
        self.assertEqual(binding.recipe_id, recipe.recipe_id)
        self.assertEqual(self.fixture_file_facts(), before)
        self.assertFalse((self.root / "prepared").exists())

    def existing_donor_target(self):
        recipe, donor_record, donor, members = self.donor_fixture()
        destination, _, _ = recipe.additions[0]
        target = self.source / destination
        target.write_bytes(members[0][1])
        os.utime(target, ns=(1_600_000_000_987_654_321,) * 2)
        recipe = replace(recipe, assets_sha256=assets_manifest_hash(self.source))
        return recipe, donor_record, donor, members, target

    def test_existing_donor_target_keeps_own_mtime_and_absent_target_uses_donor(self):
        recipe, donor_record, donor, members, target = self.existing_donor_target()
        before = self.fixture_file_facts()
        with patch.object(
            recipe_service, "_HILL_COWHIDE_DONOR_ARCHIVE", donor_record.archive_sha256
        ):
            rules, _ = self.resolve(recipe)
        self.assertEqual(self.fixture_file_facts(), before)
        apply_rules(
            self.source,
            self.ARCHIVE,
            self.GAME,
            rules,
            metadata_policy=PRESERVE_RESOURCE_MTIMES,
        )
        self.assertEqual(target.read_bytes(), members[0][1])
        self.assertEqual(target.stat().st_mtime_ns, 1_600_000_000_987_654_321)
        missing = self.source / recipe.additions[1][0]
        self.assertEqual(missing.read_bytes(), members[1][1])
        self.assertEqual(missing.stat().st_mtime_ns, (donor / members[1][0]).stat().st_mtime_ns)

    def test_existing_donor_target_wrong_bytes_or_unsafe_type_refuses(self):
        recipe, donor_record, _, members, target = self.existing_donor_target()
        for kind in ("wrong-bytes", "directory", "link", "fifo"):
            with self.subTest(kind=kind):
                target.unlink()
                if kind == "wrong-bytes":
                    target.write_bytes(b"x" * len(members[0][1]))
                elif kind == "directory":
                    target.mkdir()
                elif kind == "link":
                    target.symlink_to(self.source / self.INDUSTRY_PATH)
                else:
                    os.mkfifo(target)
                # Wrong bytes are admitted by the inventory only, never the donor contract.
                candidate = (
                    replace(recipe, assets_sha256=assets_manifest_hash(self.source))
                    if kind in ("wrong-bytes", "directory")
                    else recipe
                )
                before = self.fixture_file_facts()
                try:
                    with (
                        patch.object(
                            recipe_service,
                            "_HILL_COWHIDE_DONOR_ARCHIVE",
                            donor_record.archive_sha256,
                        ),
                        self.assertRaises((RuleError, ActivationError)),
                    ):
                        self.resolve(candidate)
                    self.assertEqual(self.fixture_file_facts(), before)
                finally:
                    if kind == "directory":
                        target.rmdir()
                    else:
                        target.unlink()
                    target.write_bytes(members[0][1])

    def test_existing_donor_target_mtime_change_during_compilation_refuses(self):
        recipe, donor_record, _, _, target = self.existing_donor_target()
        read = recipe_service._read

        def change_target_after_donor_read(path, *args, **kwargs):
            data = read(path, *args, **kwargs)
            if path.name == "two.kfm":
                os.utime(target, ns=(1_600_000_001_987_654_321,) * 2)
            return data

        with (
            patch.object(
                recipe_service, "_HILL_COWHIDE_DONOR_ARCHIVE", donor_record.archive_sha256
            ),
            patch.object(recipe_service, "_read", side_effect=change_target_after_donor_read),
            self.assertRaisesRegex(RuleError, "changed during compilation"),
        ):
            self.resolve(recipe)
        self.assertEqual(target.read_bytes(), b"invented model\0\xff")
        self.assertEqual(target.stat().st_mtime_ns, 1_600_000_001_987_654_321)

    def test_existing_target_policy_invalidates_previous_compiler_contract(self):
        current = recipe_service.compiler_contract_digest()
        with patch.object(
            recipe_service,
            "RECIPE_SERVICE_VERSION",
            "reviewed-stock-absent-city-v3-ownership-exact-bank-v1",
        ):
            previous = recipe_service.compiler_contract_digest()
        self.assertNotEqual(current, previous)

    def test_missing_foreign_or_ambiguous_donor_refuses_without_mutation(self):
        recipe, record, _, _ = self.donor_fixture()
        foreign = SimpleNamespace(**{**vars(record), "archive_sha256": sha(b"foreign archive")})
        before = self.fixture_file_facts()
        for candidates in ([], [foreign], [record, record]):
            self.app.catalogue = lambda: candidates
            with (
                self.subTest(candidates=len(candidates)),
                patch.object(recipe_service, "_HILL_COWHIDE_DONOR_ARCHIVE", record.archive_sha256),
                self.assertRaisesRegex(RuleError, "donor import is unavailable"),
            ):
                self.resolve(recipe)
            self.assertEqual(self.fixture_file_facts(), before)
            self.assertFalse((self.root / "prepared").exists())

    def test_missing_changed_or_linked_later_donor_member_refuses_without_mutation(self):
        recipe, record, donor, members = self.donor_fixture()
        path, original = members[1]
        target = donor / path
        for kind in ("missing", "changed", "linked"):
            with self.subTest(kind=kind):
                target.unlink()
                if kind == "changed":
                    target.write_bytes(b"planted wrong donor bytes")
                elif kind == "linked":
                    target.symlink_to(donor / members[0][0])
                before = self.fixture_file_facts()
                # Tree validation rejects links before the recipe-specific reader.
                error = ActivationError if kind == "linked" else RuleError
                with (
                    patch.object(
                        recipe_service, "_HILL_COWHIDE_DONOR_ARCHIVE", record.archive_sha256
                    ),
                    self.assertRaises(error),
                ):
                    self.resolve(recipe)
                self.assertEqual(self.fixture_file_facts(), before)
                self.assertFalse((self.root / "prepared").exists())
                if target.is_symlink() or target.exists():
                    target.unlink()
                target.write_bytes(original)
                os.utime(target, ns=(1_700_000_000_123_456_789,) * 2)


if __name__ == "__main__":
    unittest.main()
