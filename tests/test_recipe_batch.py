"""Offline planner outcomes using the accepted synthetic resolver fixtures."""

from dataclasses import asdict, replace
import hashlib
import json
import shutil
import unittest
from unittest.mock import patch

import test_recipe_service as fixtures
from smr_launcher import cli, recipe_service
from smr_launcher.application import ApplicationError, LauncherApplication, MapRecord
from smr_launcher.recipe_identity import map_file_context


class RecipeBatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SyntheticRecipeServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.app.installation.profile_root = f.root / "live"
        f.app.installation.game_running = lambda: self.fail("Planner queried game process")
        self.app = LauncherApplication(f.app.installation, f.root / "library")
        self.app.imports, self.app.originals = f.imports, f.originals
        self.app.library.mkdir()
        self.app._stock_file_context = f.app._stock_file_context
        prepared = self.app.prepared / ("c" * 32)
        shutil.copytree(f.source, prepared, copy_function=shutil.copy2)
        identity = dict(
            schema=1,
            recipe="original",
            archive=f.ARCHIVE,
            game=f.GAME,
            assets=f.recipe.assets_sha256,
        )
        variant = fixtures.sha(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())
        self.record = MapRecord(
            "Synthetic",
            f.ARCHIVE,
            variant,
            "map-" + variant,
            (f.SCENARIO_PATH,),
            prepared.name,
            f.GAME,
            f.source.name,
        )
        self.write_records([self.record])
        self.request = dict(profile_id=self.record.profile_id, recipe_id=f.RECIPE_ID)
        self.addCleanup(patch.stopall)
        patch.object(recipe_service, "_RECIPES", {f.RECIPE_ID: f.recipe}).start()
        patch.object(
            recipe_service, "extract_supported_industry_registry", return_value=f.REGISTRY
        ).start()
        # Establish only the permitted coordination file before preservation snapshots.
        with self.app._locked(recover_removal=False):
            pass

    def write_records(self, records):
        self.app.catalogue_file.write_text(
            json.dumps(dict(schema=1, maps=[r.as_json() for r in records]))
        )

    def test_exact_report_repeat_and_preservation(self):
        f = self.fixture
        before = f.fixture_file_facts()
        output = self.app.plan_recipe_batch([self.request])
        recipe_digest = hashlib.sha256(
            json.dumps(asdict(f.recipe), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        expected = dict(
            schema=1,
            snapshot_only=True,
            requires_execution_revalidation=True,
            all_supported=True,
            jobs=[
                dict(
                    index=0,
                    **self.request,
                    status="supported",
                    original_variant_id=self.record.variant_id,
                    archive_sha256=f.ARCHIVE,
                    game_executable_sha256=f.GAME,
                    imported_directory=f.source.name,
                    binding=dict(
                        schema=1,
                        recipe_id=f.RECIPE_ID,
                        recipe_sha256=recipe_digest,
                        compiler_sha256=recipe_service.compiler_contract_digest(),
                        source_files_sha256=map_file_context(f.source)["identity"],
                        stock_files_sha256=f.app._stock_file_context()["identity"],
                        file_context_algorithm="sha256-resource-files-content-mtime-ns-v1",
                        metadata_policy="preserve-imported-resource-mtimes-v1",
                    ),
                    donor=None,
                    changes=[
                        dict(
                            kind="patch",
                            path=f.INDUSTRY_PATH,
                            before_sha256=fixtures.sha(f.industry_data),
                            after_sha256=fixtures.sha(
                                f.industry_data.replace(b"Test Mill", b"Registered One")
                            ),
                        )
                    ],
                )
            ],
        )
        self.assertEqual(output, expected)
        self.assertEqual(
            json.dumps(output, sort_keys=True),
            json.dumps(self.app.plan_recipe_batch([self.request]), sort_keys=True),
        )
        self.assertEqual(f.fixture_file_facts(), before)
        self.assertEqual(len(list(self.app.prepared.iterdir())), 1)
        self.assertFalse(self.app.profiles.state_file.exists())
        self.assertFalse(self.app.verification_store.path.exists())

    def test_mixed_unknown_and_duplicate_requests(self):
        bad = dict(self.request, recipe_id="unknown")
        missing = dict(self.request, profile_id="unknown")
        output = self.app.plan_recipe_batch([self.request, bad, missing, self.request])
        self.assertFalse(output["all_supported"])
        self.assertEqual(
            [j["status"] for j in output["jobs"]], ["supported", "refused", "refused", "refused"]
        )
        self.assertEqual(output["jobs"][1]["reason"], "Unknown compatibility recipe")
        self.assertEqual(output["jobs"][3]["duplicate_of"], 0)
        self.assertNotIn("binding", output["jobs"][1])

    def test_bounds_and_malformed_requests_refuse_before_lock(self):
        with patch.object(self.app, "_locked", side_effect=AssertionError("must not lock")):
            for requests in (
                [],
                [self.request] * 33,
                (),
                [dict(self.request, extra=True)],
                [dict(self.request, recipe_id="")],
                [dict(self.request, recipe_id=2)],
            ):
                with self.subTest(requests=requests), self.assertRaises(ApplicationError):
                    self.app.plan_recipe_batch(requests)
        output = self.app.plan_recipe_batch([self.request] * 32)
        self.assertEqual(len(output["jobs"]), 32)
        self.assertEqual(sum(j["status"] == "supported" for j in output["jobs"]), 1)

    def test_stale_non_original_source_stock_and_ambiguous_context_refuse(self):
        f = self.fixture
        self.write_records([replace(self.record, variant_id="d" * 64)])
        self.assertEqual(self.app.plan_recipe_batch([self.request])["jobs"][0]["status"], "refused")
        self.write_records([self.record])
        for path, data in (
            (f.source / f.INDUSTRY_PATH, b"changed"),
            (f.stock / "RRT_Depots.xml", b"changed"),
            (f.stock / "other/RRT_Depots.xml", f.stock_depots_data),
        ):
            with self.subTest(path=path):
                old = path.read_bytes() if path.exists() else None
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                before = f.fixture_file_facts()
                self.assertEqual(
                    self.app.plan_recipe_batch([self.request])["jobs"][0]["status"], "refused"
                )
                self.assertEqual(f.fixture_file_facts(), before)
                if old is None:
                    path.unlink()
                else:
                    path.write_bytes(old)

    def test_pending_removal_refuses_without_recovery(self):
        pending = self.app.library / "removal.json"
        pending.write_text("unowned pending transaction")
        before = self.fixture.fixture_file_facts()
        with patch(
            "smr_launcher.application.removal.resume",
            side_effect=AssertionError("must not recover"),
        ):
            with self.assertRaisesRegex(ApplicationError, "refuses recovery"):
                self.app.plan_recipe_batch([self.request])
        self.assertEqual(self.fixture.fixture_file_facts(), before)

    def test_donor_projection_and_later_member_refusal(self):
        f = self.fixture
        recipe, donor, root, members = f.donor_fixture()
        donor_record = replace(
            self.record,
            name=donor.name,
            archive_sha256=donor.archive_sha256,
            variant_id=donor.variant_id,
            profile_id=donor.profile_id,
            imported_directory=donor.imported_directory,
        )
        self.write_records([self.record, donor_record])
        with (
            patch.object(recipe_service, "_RECIPES", {f.RECIPE_ID: recipe}),
            patch.object(recipe_service, "_HILL_COWHIDE_DONOR_ARCHIVE", donor.archive_sha256),
        ):
            before = f.fixture_file_facts()
            job = self.app.plan_recipe_batch([self.request])["jobs"][0]
            self.assertEqual(job["status"], "supported")
            self.assertEqual(
                job["donor"],
                dict(
                    archive_sha256=donor.archive_sha256,
                    variant_id=donor.variant_id,
                    profile_id=donor.profile_id,
                    imported_directory=donor.imported_directory,
                    archive_bytes_verified=False,
                    member_bytes_verified=True,
                ),
            )
            additions = [change for change in job["changes"] if change["kind"] == "addition"]
            self.assertEqual(
                additions,
                [
                    dict(
                        kind="addition",
                        path=path.replace("/Donor/", "/Synthetic/"),
                        donor_path=path,
                        after_sha256=fixtures.sha(data),
                        size=len(data),
                        mtime_ns=1_700_000_000_123_456_789,
                    )
                    for path, data in members
                ],
            )
            self.assertEqual(f.fixture_file_facts(), before)
            (root / members[-1][0]).write_bytes(b"wrong later member")
            job = self.app.plan_recipe_batch([self.request])["jobs"][0]
            self.assertEqual(job["status"], "refused")
            self.assertNotIn("changes", job)

    def test_cli_uses_plan_without_status_or_runtime_probe(self):
        with (
            patch.object(LauncherApplication, "discover", return_value=self.app),
            patch.object(cli, "_status", side_effect=AssertionError("must not query status")),
        ):
            output = cli.run(
                ["plan-recipes", "--request", self.record.profile_id, self.fixture.RECIPE_ID]
            )
        self.assertTrue(output["all_supported"])
