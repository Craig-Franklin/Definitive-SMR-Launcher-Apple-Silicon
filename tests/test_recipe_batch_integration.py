"""Real synthetic batch compilation; no native or gameplay acceptance implied."""

from dataclasses import asdict, replace
from io import BytesIO
import json
import tarfile
import unittest
from unittest.mock import patch

import test_application as application_fixtures
import test_recipe_service as recipe_fixtures
from smr_launcher import recipe_service
from smr_launcher.activation import ORIGINAL
from smr_launcher.application import MapRecord
from smr_launcher.recipe_identity import file_context, map_file_context
from smr_launcher.variants import assets_manifest_hash


# Frozen independently in the coordinator's expectations.json before this test existed.
EXPECTED_INDUSTRY = (
    b"<RRTIndustries><Industries><RRTIndustry><szName>Registered One</szName>"
    b"<bInCity>1</bInCity><bIsMountainous>0</bIsMountainous>"
    b"<szSecondaryModel>test_stockpile.kfm</szSecondaryModel>"
    b"</RRTIndustry></Industries></RRTIndustries>"
)


class RecipeBatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.synthetic = recipe_fixtures.SyntheticRecipeServiceTests()
        self.synthetic.setUp()
        self.addCleanup(self.synthetic.doCleanups)
        self.fixture = application_fixtures.ApplicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f, installation = self.synthetic, self.fixture.installation
        # Establish the actual invented executable/context before baseline enrollment.
        installation.executable = f.executable
        installation.executable_sha256 = f.GAME
        installation.steamapps_root = f.root / "stock"
        self.app = self.fixture.application
        self.app.setup()
        self.originals, self.recipes, self.archives = [], {}, []
        for index in range(2):
            scenario = f.scenario_data.replace(
                b"<Scenario>", f'<Scenario fixture="{index}">'.encode()
            )
            archive = self.fixture.root / f"Synthetic-{index}.tar"
            members = self.fixture.snapshot_files(f.source)
            scenario_path = "UserMaps/Synthetic/RRT_Scenario_User_Synthetic.xml"
            del members[f.SCENARIO_PATH]
            members[scenario_path] = scenario
            members["CustomAssets/Fixture.xml"] = b"<Fixture/>"
            with tarfile.open(archive, "w") as stream:
                for name, data in sorted(members.items()):
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    entry.mtime = 1_700_000_000
                    stream.addfile(entry, BytesIO(data))
            original = self.app.import_archive(archive)
            recipe = replace(
                f.recipe,
                recipe_id=f"synthetic-batch-{index}",
                archive_sha256=original.archive_sha256,
                assets_sha256=assets_manifest_hash(self.app.imports / original.imported_directory),
                scenario_path=scenario_path,
                scenario_sha256=recipe_fixtures.sha(scenario),
                industry_after=recipe_fixtures.sha(EXPECTED_INDUSTRY),
            )
            self.originals.append(original)
            self.recipes[recipe.recipe_id] = recipe
            self.archives.append(archive)
            self.seed_saves(original.profile_id, index)
        self.requests = [
            dict(profile_id=original.profile_id, recipe_id=recipe_id)
            for original, recipe_id in zip(self.originals, self.recipes)
        ]
        catalogue_patch = patch.object(recipe_service, "_RECIPES", self.recipes)
        registry_patch = patch.object(
            recipe_service, "extract_supported_industry_registry", return_value=f.REGISTRY
        )
        for mocked in (catalogue_patch, registry_patch):
            mocked.start()
            self.addCleanup(mocked.stop)

    def seed_saves(self, profile_id, index):
        saves = self.app.profiles._profile_path(profile_id) / "Saves"
        for name in ("current.sav", "future.sav"):
            (saves / name).write_bytes(f"invented {index} {name}".encode())

    def assets(self, root):
        return {
            name: data
            for name, data in self.fixture.snapshot_files(root).items()
            if name.split("/", 1)[0] in ("UserMaps", "CustomAssets")
        }

    def preserved(self):
        roots = [
            self.app.imports,
            self.app.originals,
            self.fixture.installation.profile_root,
            self.synthetic.stock,
        ]
        roots.extend(self.app.prepared / record.prepared_directory for record in self.originals)
        roots.extend(
            self.app.profiles._profile_path(record.profile_id) for record in self.originals
        )
        return (
            [self.fixture.snapshot_files(root) for root in roots],
            [archive.read_bytes() for archive in self.archives],
            self.synthetic.executable.read_bytes(),
        )

    def assert_no_acceptance(self):
        self.assertEqual(self.app.active(), ORIGINAL)
        self.assertFalse(self.app.verification_store.path.exists())
        self.assertTrue(
            all(self.app.verification_for(record) is None for record in self.app.catalogue())
        )

    def test_two_real_compilations_exact_outputs_receipt_joins_and_repeat_save_reuse(self):
        before = self.preserved()
        plan = self.app.plan_recipe_batch(self.requests)
        self.assertTrue(plan["all_supported"], plan)
        self.assertEqual(self.preserved(), before)
        result = self.app.prepare_recipe_batch(self.requests)
        self.assertTrue(result["all_prepared"], result)
        self.assertFalse(result["partial"])
        editions = []
        stock_identity = file_context(
            {"installed_assets": self.synthetic.stock}, required_roles=("installed_assets",)
        )["identity"]
        for index, (original, job, planned) in enumerate(
            zip(self.originals, result["jobs"], plan["jobs"])
        ):
            self.assertEqual(job["status"], "prepared")
            self.assertFalse(job["reused"])
            edition = MapRecord.from_json(job["edition"])
            editions.append(edition)
            source = self.app.imports / original.imported_directory
            expected_assets = self.assets(source)
            expected_assets[self.synthetic.INDUSTRY_PATH] = EXPECTED_INDUSTRY
            self.assertEqual(
                self.assets(self.app.prepared / edition.prepared_directory), expected_assets
            )
            profile = self.app.profiles._profile_path(edition.profile_id)
            self.assertEqual(self.assets(profile), expected_assets)
            self.assertEqual(self.fixture.snapshot_files(profile / "Saves"), {})
            receipt_path = (
                self.app.library / "compatibility-preparations" / (edition.variant_id + ".json")
            )
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(job["receipt"], receipt)
            self.assertEqual(receipt["parent_variant"], original.variant_id)
            self.assertEqual(receipt["variant_id"], edition.variant_id)
            self.assertEqual(receipt["archive_sha256"], original.archive_sha256)
            self.assertEqual(receipt["game_executable_sha256"], self.synthetic.GAME)
            self.assertEqual(receipt["assets_sha256"], assets_manifest_hash(profile))
            self.assertEqual(edition.recipe_receipt_sha256, self.app._recipe_receipt_hash(receipt))
            binding = job["binding"]
            recipe = self.recipes[self.requests[index]["recipe_id"]]
            self.assertEqual(binding["recipe_id"], recipe.recipe_id)
            self.assertEqual(binding["source_files_sha256"], map_file_context(source)["identity"])
            self.assertEqual(binding["stock_files_sha256"], stock_identity)
            self.assertEqual(binding["compiler_sha256"], recipe_service.compiler_contract_digest())
            self.assertEqual(
                binding["recipe_sha256"],
                recipe_fixtures.sha(
                    json.dumps(asdict(recipe), sort_keys=True, separators=(",", ":")).encode()
                ),
            )
            self.assertEqual(binding["output_files_sha256"], map_file_context(profile)["identity"])
            self.assertEqual({key: binding[key] for key in planned["binding"]}, planned["binding"])
            self.assertEqual(
                receipt["rules"][0]["files"],
                [
                    dict(
                        path=self.synthetic.INDUSTRY_PATH,
                        before=recipe_fixtures.sha(self.synthetic.industry_data),
                        after=recipe_fixtures.sha(EXPECTED_INDUSTRY),
                    )
                ],
            )
            self.seed_saves(edition.profile_id, index + 10)
        for field in ("variant_id", "profile_id", "prepared_directory"):
            self.assertEqual(
                len({getattr(record, field) for record in editions + self.originals}), 4
            )
        catalogue = self.app.catalogue()
        prepared = set(self.app.prepared.iterdir())
        profiles_before = [
            self.fixture.snapshot_files(self.app.profiles._profile_path(e.profile_id))
            for e in editions
        ]
        repeated = self.app.prepare_recipe_batch(self.requests)
        self.assertTrue(repeated["all_prepared"], repeated)
        self.assertTrue(all(job["reused"] for job in repeated["jobs"]))
        self.assertEqual(
            [job["edition"] for job in repeated["jobs"]], [e.as_json() for e in editions]
        )
        self.assertEqual(self.app.catalogue(), catalogue)
        self.assertEqual(set(self.app.prepared.iterdir()), prepared)
        self.assertEqual(
            [
                self.fixture.snapshot_files(self.app.profiles._profile_path(e.profile_id))
                for e in editions
            ],
            profiles_before,
        )
        self.assertEqual(self.preserved(), before)
        self.assert_no_acceptance()

    def test_later_unknown_recipe_preflight_publishes_no_editions(self):
        before = self.preserved()
        catalogue = self.app.catalogue()
        prepared = set(self.app.prepared.iterdir())
        result = self.app.prepare_recipe_batch(
            [self.requests[0], dict(self.requests[1], recipe_id="unknown-synthetic-recipe")]
        )
        self.assertFalse(result["all_prepared"])
        self.assertEqual([job["status"] for job in result["jobs"]], ["not_attempted", "refused"])
        self.assertEqual(result["jobs"][1]["reason"], "Unknown compatibility recipe")
        self.assertEqual(self.app.catalogue(), catalogue)
        self.assertEqual(set(self.app.prepared.iterdir()), prepared)
        self.assertFalse((self.app.library / "compatibility-preparations").exists())
        self.assertEqual(self.preserved(), before)
        self.assert_no_acceptance()

    def test_changed_stock_reader_refuses_before_reusing_intact_editions(self):
        first = self.app.prepare_recipe_batch(self.requests)
        self.assertTrue(first["all_prepared"], first)
        for index, job in enumerate(first["jobs"]):
            self.seed_saves(job["edition"]["profile_id"], index + 10)
        stock = self.synthetic.stock / "RRT_Industries.xml"
        stock.write_bytes(b"<RRTIndustries fixture='changed-provider'/>")
        before = self.preserved()
        library_before = self.fixture.snapshot_files(self.app.library)
        result = self.app.prepare_recipe_batch(self.requests)
        self.assertFalse(result["all_prepared"])
        self.assertEqual([job["status"] for job in result["jobs"]], ["refused", "refused"])
        for job in result["jobs"]:
            self.assertEqual(
                job["reason"],
                "Recipe reader basename is missing, ambiguous or has unexpected content",
            )
            self.assertNotIn("edition", job)
        self.assertEqual(self.fixture.snapshot_files(self.app.library), library_before)
        self.assertEqual(self.preserved(), before)
        self.assert_no_acceptance()
