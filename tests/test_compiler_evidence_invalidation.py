"""Compiler provenance separates editions even when invented engine inputs agree."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_recipe_batch_integration as batch_fixtures
import test_scenario_evidence as evidence_fixtures
from smr_launcher import recipe_service
from smr_launcher.application import MapRecord
from smr_launcher.recipe_identity import map_file_context
from smr_launcher.scenario_evidence import build_record, canonical, context_identity


# Complete outcomes fixed before this candidate; independent of reducer action lists.
UNKNOWN_ACTIONS = {
    "load": "unknown",
    "camera": "unknown",
    "track_construction": "unknown",
    "station_construction": "unknown",
    "train_purchase_routing": "unknown",
    "cargo_transport": "unknown",
    "bridge_construction": "unknown",
    "annex_construction": "unknown",
    "tunnel_construction": "unknown",
    "industry_construction_production": "unknown",
    "goals_events": "unknown",
    "era_transition": "unknown",
    "difficulty": "unknown",
    "ai_competition": "unknown",
    "autosave": "unknown",
    "manual_save": "unknown",
    "reload": "unknown",
    "continue_after_reload": "unknown",
    "extended_play": "unknown",
}


class CompilerEvidenceInvalidationTests(unittest.TestCase):
    def test_real_compiler_a_b_a_isolates_editions_and_preserves_saves(self):
        fixture = batch_fixtures.RecipeBatchIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        app, synthetic = fixture.app, fixture.synthetic
        requests = [fixture.requests[0]]
        preserved = fixture.preserved()
        editions, bindings, snapshots = [], [], []
        for index, contract in enumerate(
            (
                "synthetic-compiler-A",
                "synthetic-compiler-A",
                "synthetic-compiler-B",
                "synthetic-compiler-A",
            )
        ):
            with patch.object(recipe_service, "COMPILER_VERSION", contract):
                result = app.prepare_recipe_batch(requests)
                compiler = recipe_service.compiler_contract_digest()
            self.assertTrue(result["all_prepared"], result)
            self.assertFalse(result["partial"])
            job = result["jobs"][0]
            self.assertEqual(job["status"], "prepared")
            self.assertEqual(job["reused"], index in (1, 3))
            edition = MapRecord.from_json(job["edition"])
            binding = job["binding"]
            self.assertEqual(binding["compiler_sha256"], compiler)
            self.assertEqual(job["receipt"]["variant_id"], edition.variant_id)
            self.assertEqual(job["receipt"]["parent_variant"], fixture.originals[0].variant_id)
            self.assertEqual(
                edition.recipe_receipt_sha256, app._recipe_receipt_hash(job["receipt"])
            )
            profile = app.profiles._profile_path(edition.profile_id)
            expected = fixture.assets(app.imports / fixture.originals[0].imported_directory)
            expected[synthetic.INDUSTRY_PATH] = batch_fixtures.EXPECTED_INDUSTRY
            self.assertEqual(fixture.assets(profile), expected)
            self.assertEqual(fixture.assets(app.prepared / edition.prepared_directory), expected)
            self.assertEqual(binding["output_files_sha256"], map_file_context(profile)["identity"])
            for old, snapshot in zip(editions, snapshots):
                self.assertEqual(
                    fixture.fixture.snapshot_files(app.profiles._profile_path(old.profile_id)),
                    snapshot,
                )
            if index in (0, 2):
                self.assertEqual(fixture.fixture.snapshot_files(profile / "Saves"), {})
                fixture.seed_saves(edition.profile_id, index + 10)
                editions.append(edition)
                bindings.append(binding)
                snapshots.append(fixture.fixture.snapshot_files(profile))
            else:
                self.assertEqual(edition, editions[0])
                self.assertEqual(binding, bindings[0])
            self.assertEqual(fixture.preserved(), preserved)
            fixture.assert_no_acceptance()
        a, b = editions
        for field in ("variant_id", "profile_id", "prepared_directory", "recipe_receipt_sha256"):
            self.assertNotEqual(getattr(a, field), getattr(b, field))
        self.assertNotEqual(bindings[0]["compiler_sha256"], bindings[1]["compiler_sha256"])
        self.assertEqual(
            {k: v for k, v in bindings[0].items() if k != "compiler_sha256"},
            {k: v for k, v in bindings[1].items() if k != "compiler_sha256"},
        )
        self.assertEqual(set(app.catalogue()), set(fixture.originals + editions))

    def test_equal_complete_engine_context_cannot_transfer_compiler_edition_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            f = evidence_fixtures.Fixture(Path(temporary).resolve())
            # A and B are independently declared compiler-bound editions of one scenario.
            a, b = f.a, f.sibling
            self.assertEqual(
                {k: v for k, v in a.items() if k != "edition_id"},
                {k: v for k, v in b.items() if k != "edition_id"},
            )
            self.assertNotEqual(a["edition_id"], b["edition_id"])
            contract_a = f.contract()
            observation = f.record(contract_a)
            self.assertEqual(
                observation.payload["protocol"]["expected"],
                "camera reaches authored northwest marker",
            )
            self.assertEqual(
                observation.payload["actual_assertions"],
                {
                    "expected": "camera reaches authored northwest marker",
                    "satisfied": True,
                    "features": {
                        "geometry": "authored triangle is visible",
                        "materials": "triangle is blue",
                        "terrain": "authored ridge and river are visible",
                    },
                },
            )
            context_b = deepcopy(f.context)
            self.assertIsNotNone(context_identity(context_b))
            self.assertEqual(canonical(context_b), canonical(observation.payload["actual_context"]))
            raw_a = f.store.path.read_bytes()
            positive = dict(UNKNOWN_ACTIONS, camera="pass")
            # B initially lacks a build and contract, despite its inventory row.
            self.assertEqual(dict(f.status([b], context_b).scenarios[0].actions), UNKNOWN_ACTIONS)
            build_a = f.build
            f.build = build_record(
                f.inventory,
                b["archive_sha256"],
                b["edition_id"],
                context_b["prepared_output_sha256"],
                deepcopy(f.resources),
                context_b["game_sha256"],
                context_b["options"],
            )
            self.assertNotEqual(f.build.id, build_a.id)
            self.assertEqual(
                f.build.payload["resource_receipt"], build_a.payload["resource_receipt"]
            )
            self.assertEqual(f.build.payload["context"], build_a.payload["context"])
            f.store.append(f.build)
            self.assertEqual(dict(f.status([b], context_b).scenarios[0].actions), UNKNOWN_ACTIONS)
            contract_b = f.contract(b)
            self.assertNotEqual(contract_a.id, contract_b.id)
            for scenario, expected in ((a, positive), (b, UNKNOWN_ACTIONS)):
                status = f.status([scenario], context_b)
                self.assertEqual(status.status, "Not verified")
                self.assertEqual(status.scenarios[0].status, "Not verified")
                self.assertEqual(dict(status.scenarios[0].actions), expected)
            self.assertTrue(f.store.path.read_bytes().startswith(raw_a))
            self.assertIn(observation, f.store.records())
            missing = dict(b, edition_id=evidence_fixtures.h(b"uninventoried compiler C"))
            self.assertEqual(
                dict(f.status([missing], context_b).scenarios[0].actions), UNKNOWN_ACTIONS
            )
