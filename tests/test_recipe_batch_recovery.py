"""Explicit fixture-only resubmission after interrupted edition publication."""

from pathlib import Path
import unittest
from unittest.mock import patch

import test_recipe_batch_preparation as fixtures
from smr_launcher import application
from smr_launcher.activation import ORIGINAL


class RecipeBatchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RecipeBatchPreparationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app

    def assert_explicit_resubmission_preserves_publication(self, boundary):
        f = self.fixture
        original_profiles = {record.profile_id for record in f.originals}
        for record in f.originals:
            saved = self.app.profiles._profile_path(record.profile_id) / "Saves/old.sav"
            saved.write_bytes(b"original edition save remains attached")
        protected = {
            root: f.fixture.snapshot_files(root)
            for root in (
                self.app.originals,
                self.app.imports,
                self.app.profiles.live,
                *(self.app.profiles._profile_path(record.profile_id) for record in f.originals),
            )
        }
        atomic_json = application._atomic_json
        failures = []

        def interrupt(path, value):
            path = Path(path)
            if boundary == "before_receipt" and path.parent.name == "compatibility-preparations":
                failures.append(boundary)
                raise OSError("invented interruption before receipt publication")
            atomic_json(path, value)
            if boundary == "after_catalogue" and path == self.app.catalogue_file:
                failures.append(boundary)
                raise OSError("invented interruption after catalogue publication")

        with patch.object(application, "_atomic_json", side_effect=interrupt):
            interrupted = self.app.prepare_recipe_batch(f.requests)
        self.assertEqual(failures, [boundary])
        self.assertFalse(interrupted["all_prepared"])
        self.assertEqual(
            [job["status"] for job in interrupted["jobs"]], ["failed", "not_attempted"]
        )
        published = [
            path
            for path in self.app.profiles.profiles.iterdir()
            if path.name not in original_profiles
        ]
        self.assertEqual(len(published), 1)
        profile = published[0]
        future_save = profile / "Saves/future.sav"
        future_save.write_bytes(b"new save retained between explicit attempts")
        retained = f.fixture.snapshot_files(profile)

        # This is a separate caller request, never a retry inside the failed batch.
        resumed = self.app.prepare_recipe_batch(f.requests)
        self.assertTrue(resumed["all_prepared"])
        first, second = resumed["jobs"]
        self.assertEqual(first["edition"]["profile_id"], profile.name)
        self.assertEqual(first["edition"]["variant_id"], profile.name.removeprefix("map-"))
        self.assertNotEqual(first["edition"]["profile_id"], second["edition"]["profile_id"])
        self.assertEqual(f.fixture.snapshot_files(profile), retained)
        self.assertEqual(future_save.read_bytes(), b"new save retained between explicit attempts")
        self.assertEqual(first["receipt"]["parent_variant"], f.originals[0].variant_id)
        for root, expected in protected.items():
            self.assertEqual(f.fixture.snapshot_files(root), expected)
        self.assertEqual(self.app.profiles._state()["active"], ORIGINAL)
        self.assertFalse(self.app.profiles.journal.exists())
        self.assertFalse(self.app.verification_store.path.exists())

    def test_profile_published_before_receipt_failure_keeps_future_saves(self):
        self.assert_explicit_resubmission_preserves_publication("before_receipt")

    def test_catalogue_published_before_error_keeps_exact_edition(self):
        self.assert_explicit_resubmission_preserves_publication("after_catalogue")
