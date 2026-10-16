"""Finite original-to-cached Play through real synthetic recipe/profile services."""

from dataclasses import replace
from threading import Thread
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

import test_application
from smr_launcher import recipe_service
from smr_launcher.application import ApplicationError
from smr_launcher.native_backend import NativeLibrary
from smr_launcher.native_map_tools import dispatch
from smr_launcher.play_history import context


class FiniteCachedPlayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_application.ApplicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        (self.fixture.installation.profile_root / "Settings.ini").write_bytes(
            b"[User Settings]\nQuickstart = 0\n"
        )
        self.original, self.rule, self.binding, self.stock = self.fixture.compiled_fixture()
        self.app = self.fixture.application
        self.choice = SimpleNamespace(
            recipe_id=self.binding.recipe_id, title="Synthetic repair", description="Experimental"
        )
        self.enterContext(
            patch.object(self.app, "compatibility_recipes", return_value=(self.choice,))
        )
        self.enterContext(
            patch.object(
                recipe_service, "resolve_bundled_recipe", return_value=((self.rule,), self.binding)
            )
        )
        self.enterContext(
            patch.dict(
                recipe_service._RECIPES, {self.binding.recipe_id: SimpleNamespace(additions=())}
            )
        )
        self.library, self.launch = NativeLibrary(), Mock()
        self.params = dict(
            context(self.original, self.app.installation.executable_sha256),
            profile_id=self.original.profile_id,
            prepare_compatibility=True,
            launch_deadline=time.monotonic() + 30,
        )
        saves = self.app.profiles._profile_path(self.original.profile_id) / "Saves"
        (saves / "personal.sav").write_bytes(b"original save")

    def prepare_cache(self):
        edition = self.app.create_recipe_edition(self.original, self.binding.recipe_id)
        (self.app.profiles._profile_path(edition.profile_id) / "Saves/cached.sav").write_bytes(
            b"cached save"
        )
        return edition

    def preflight(self):
        return dispatch(self.library, self.app, "maptools.play_preflight", self.params)

    def commit(self, review):
        return dispatch(
            self.library,
            self.app,
            "maptools.play_commit",
            dict(
                self.params,
                ticket=review["ticket"],
                fingerprint=review["fingerprint"],
                confirmed=True,
            ),
            launch=self.launch,
        )

    def files(self):
        return self.fixture.snapshot_files(self.app.library)

    def test_current_cache_launches_exact_edition_without_compilation_or_file_changes(self):
        edition = self.prepare_cache()
        before = self.files()
        with patch(
            "smr_launcher.variants.prepare_recipe_variant",
            side_effect=AssertionError("Compiled during finite cached Play"),
        ):
            review = self.preflight()
            self.assertEqual(review["preparation"]["cached_profile_id"], edition.profile_id)
            self.assertEqual(review["target"]["profile_id"], self.original.profile_id)
            self.assertIn("saves remain on their own edition", review["warning"])
            result = self.commit(review)
        self.assertEqual(result["launched_profile_id"], edition.profile_id)
        self.launch.assert_called_once_with(
            edition.profile_id, launch_deadline=self.params["launch_deadline"]
        )
        self.assertEqual(self.files(), before)
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review)

    def test_expiry_after_cached_resolution_uses_real_native_gate_without_switch_or_launch(self):
        from test_native_backend import window

        self.prepare_cache()
        review = self.preflight()
        before = self.files()
        owner = window()
        owner.app = self.app
        owner.close_when_idle = False
        owner._play_with_crash_monitor = lambda profile, mutation_guard: self.app.play(
            profile, launch=self.launch, startup_seconds=1, mutation_guard=mutation_guard
        )

        def late_native_launch(profile, *, launch_deadline):
            with patch(
                "smr_launcher.native_map_tools.time.monotonic", return_value=launch_deadline
            ):
                return owner._native_launch(self.app, profile, launch_deadline=launch_deadline)

        result = dispatch(
            self.library,
            self.app,
            "maptools.play_commit",
            dict(
                self.params,
                ticket=review["ticket"],
                fingerprint=review["fingerprint"],
                confirmed=True,
            ),
            launch=late_native_launch,
        )
        self.assertIs(result["launch_dispatched"], False)
        self.assertNotIn("launched_profile_id", result)
        self.launch.assert_not_called()
        self.assertEqual(self.files(), before)
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review)

    def test_cache_miss_refuses_without_creating_an_edition(self):
        before = self.files()
        with patch(
            "smr_launcher.variants.prepare_recipe_variant",
            side_effect=AssertionError("Compiled on cache miss"),
        ):
            with self.assertRaisesRegex(ApplicationError, "one intact cached"):
                self.preflight()
        self.assertEqual(self.files(), before)
        self.launch.assert_not_called()

    def test_changed_cached_asset_refuses_and_preserves_changed_data(self):
        edition = self.prepare_cache()
        path = self.app.profiles._profile_path(edition.profile_id) / "CustomAssets/Example.txt"
        path.chmod(0o600)
        path.write_bytes(b"changed cache")
        before = self.files()
        with self.assertRaisesRegex(ApplicationError, "one intact cached"):
            self.preflight()
        self.assertEqual(self.files(), before)
        self.launch.assert_not_called()

    def test_cached_warning_change_consumes_ticket_and_never_launches(self):
        edition = self.prepare_cache()
        review = self.preflight()
        before = self.files()
        with patch.object(
            self.app,
            "verification_for",
            side_effect=lambda r, **kw: (
                SimpleNamespace(issue="Known cached problem") if r == edition else None
            ),
        ):
            with self.assertRaisesRegex(ValueError, "Cached edition has a known issue"):
                self.commit(review)
        self.launch.assert_not_called()
        self.assertEqual(self.files(), before)
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review)

    def test_changed_current_binding_refuses_cached_reuse_without_compilation(self):
        self.prepare_cache()
        changed = replace(self.binding, compiler_sha256="e" * 64)
        before = self.files()
        with patch.object(
            recipe_service, "resolve_bundled_recipe", return_value=((self.rule,), changed)
        ):
            with self.assertRaisesRegex(ApplicationError, "one intact cached"):
                self.preflight()
        self.assertEqual(self.files(), before)
        self.launch.assert_not_called()

    def test_readonly_cache_lookup_refuses_real_busy_lock_and_pending_journal(self):
        self.prepare_cache()
        errors = []

        def read():
            try:
                self.app.create_recipe_edition(
                    self.original, self.binding.recipe_id, reuse_only=True
                )
            except Exception as exc:
                errors.append(exc)

        with self.app._locked():
            thread = Thread(target=read)
            thread.start()
            thread.join(1)
            completed = not thread.is_alive()
        thread.join(2)
        self.assertTrue(completed, "Readonly cached lookup waited on real flock")
        self.assertIn("application is busy", str(errors[0]).lower())
        journal = self.app.profiles.journal
        journal.write_bytes(b"pending owner")
        before = self.files()
        with self.assertRaisesRegex(ApplicationError, "Pending activation"):
            self.app.create_recipe_edition(self.original, self.binding.recipe_id, reuse_only=True)
        self.assertEqual(self.files(), before)
        self.launch.assert_not_called()

    def test_ambiguous_current_cache_refuses_without_selecting_a_candidate(self):
        edition = self.prepare_cache()
        original_catalogue = self.app.catalogue
        # Two current catalogue rows claiming the same intact compiled receipt
        # cannot silently choose whichever is enumerated first.
        duplicate = replace(edition, name="Duplicate current edition")
        with patch.object(
            self.app, "catalogue", side_effect=lambda: (*original_catalogue(), duplicate)
        ):
            with self.assertRaisesRegex(ApplicationError, "one intact cached"):
                self.preflight()
        self.launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
