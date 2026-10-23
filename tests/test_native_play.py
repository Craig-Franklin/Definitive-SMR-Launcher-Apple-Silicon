"""Native launch review with fake verification/protected launch; no game or monitors."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from threading import Thread
import time

from test_native_backend import app, record, window
from smr_launcher.native_backend import NativeLibrary
from smr_launcher.native_map_tools import dispatch
from smr_launcher.play_history import context
from smr_launcher.gui import LaunchCancelledBeforeDispatch


class FinitePlayVerificationLocks(TestCase):
    def setUp(self):
        from test_application import ApplicationTests
        import hashlib
        import json

        self.fixture = ApplicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.application
        self.app.setup()
        self.record = self.app.import_archive(self.fixture.archive)
        stock = (
            self.app.installation.steamapps_root
            / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        )
        stock.mkdir(parents=True)
        (stock / "synthetic.xml").write_bytes(b"<Stock/>")
        root = self.app.prepared / self.record.prepared_directory
        inventory = self.fixture.root / "inventory.json"
        inventory.write_text(
            json.dumps(
                {
                    "schema": "smr-e01-inventory-v1",
                    "editions": [
                        {
                            "archive_sha256": self.record.archive_sha256,
                            "edition_id": self.record.variant_id,
                            "edition_kind": "original",
                            "scenarios": [
                                {
                                    "scenario_relative_path": name,
                                    "scenario_sha256": hashlib.sha256(
                                        (root / name).read_bytes()
                                    ).hexdigest(),
                                }
                                for name in self.record.scenarios
                            ],
                        }
                    ],
                }
            )
        )
        self.app.bind_scenario_inventory(
            inventory, hashlib.sha256(inventory.read_bytes()).hexdigest()
        )
        self.library, self.launch = NativeLibrary(), Mock()
        self.params = dict(
            context(self.record, self.app.installation.executable_sha256),
            profile_id=self.record.profile_id,
            prepare_compatibility=False,
            launch_deadline=time.monotonic() + 20,
        )

    def preflight(self):
        return dispatch(self.library, self.app, "maptools.play_preflight", self.params)

    def assert_busy_without_wait(self, call):
        errors = []

        def run():
            try:
                call()
            except BaseException as exc:
                errors.append(exc)

        thread = Thread(target=run)
        with self.app._locked():
            thread.start()
            thread.join(1)
            completed_under_lock = not thread.is_alive()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(
            completed_under_lock, "Finite evidence revalidation waited on the real app flock"
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("application is busy", str(errors[0]).lower())
        self.launch.assert_not_called()

    def test_finite_preflight_with_real_inventory_refuses_busy_lock(self):
        self.assertEqual(self.preflight()["launch_deadline"], self.params["launch_deadline"])
        self.assert_busy_without_wait(self.preflight)

    def test_finite_commit_revalidation_refuses_busy_lock_and_consumes_ticket(self):
        review = self.preflight()
        params = dict(
            self.params, ticket=review["ticket"], fingerprint=review["fingerprint"], confirmed=True
        )
        call = lambda: dispatch(
            self.library, self.app, "maptools.play_commit", params, launch=self.launch
        )
        before = self.fixture.snapshot_files(self.app.profiles.live)
        self.assert_busy_without_wait(call)
        self.assertEqual(self.fixture.snapshot_files(self.app.profiles.live), before)
        with self.assertRaisesRegex(ValueError, "was used"):
            call()


class NativePlayTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.app = app(Path(folder.name).resolve())
        self.app._stopped = Mock()
        self.app.compatibility_recipes = Mock(return_value=())
        self.app.profiles = SimpleNamespace(
            journal=SimpleNamespace(exists=Mock(return_value=False))
        )
        self.library = NativeLibrary()
        self.launch = Mock()
        self.params = {**context(record(), "c" * 64), "profile_id": record().profile_id}

    def preflight(self, **fields):
        return dispatch(
            self.library, self.app, "maptools.play_preflight", {**self.params, **fields}
        )

    def commit(self, review, **fields):
        return dispatch(
            self.library,
            self.app,
            "maptools.play_commit",
            {
                **self.params,
                "ticket": review["ticket"],
                "fingerprint": review["fingerprint"],
                "confirmed": True,
                **fields,
            },
            launch=self.launch,
        )

    def test_normal_no_warning_play_and_explicit_warning_consent(self):
        review = self.preflight()
        self.assertEqual(review["warning"], "")
        self.assertEqual(review["target"]["variant_id"], "b" * 64)
        self.commit(review)
        self.launch.assert_called_once_with(record().profile_id)
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review)
        self.launch.assert_called_once()
        self.app.verification_for.return_value = SimpleNamespace(issue="Known synthetic issue")
        review = self.preflight()
        self.assertEqual(review["warning"], "Known synthetic issue")
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.commit(review, confirmed=False)
        self.launch.assert_called_once()
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review)
        self.commit(self.preflight())
        self.assertEqual(self.launch.call_count, 2)

    def test_changed_warning_requires_new_review_including_new_or_removed_warning(self):
        for before, after in (
            ("Known issue A", "Known issue B"),
            ("", "New issue"),
            ("Old issue", ""),
        ):
            self.app.verification_for.return_value = SimpleNamespace(issue=before)
            review = self.preflight()
            self.app.verification_for.return_value = SimpleNamespace(issue=after)
            with self.subTest(before=before), self.assertRaisesRegex(ValueError, "warning changed"):
                self.commit(review)
            with self.assertRaisesRegex(ValueError, "was used"):
                self.commit(review)
        self.launch.assert_not_called()

    def test_changed_missing_edition_build_and_cross_target_ticket_cannot_launch(self):
        review = self.preflight()
        self.app.catalogue.return_value = (replace(record(), variant_id="d" * 64),)
        with self.assertRaisesRegex(ValueError, "edition or game build changed"):
            self.commit(review)
        self.app.catalogue.return_value = (record(),)
        review = self.preflight()
        self.app.catalogue.return_value = ()
        with self.assertRaisesRegex(ValueError, "no longer"):
            self.commit(review)
        self.app.catalogue.return_value = (record(),)
        review = self.preflight()
        self.app.installation.executable_sha256 = "e" * 64
        with self.assertRaisesRegex(ValueError, "build changed"):
            self.commit(review)
        self.app.installation.executable_sha256 = "c" * 64
        review = self.preflight()
        changed = replace(record(), variant_id="f" * 64)
        self.app.catalogue.return_value = (changed,)
        with self.assertRaisesRegex(ValueError, "target or warning changed"):
            self.commit(review, profile_id=changed.profile_id, variant_id=changed.variant_id)
        self.launch.assert_not_called()

    def test_recovery_stopped_and_verification_errors_fail_before_launch(self):
        self.app._stopped.side_effect = RuntimeError("Synthetic running game")
        with self.assertRaisesRegex(RuntimeError, "running game"):
            self.preflight()
        self.app._stopped.side_effect = None
        review = self.preflight()
        self.app.profiles.journal.exists.return_value = True
        with self.assertRaisesRegex(ValueError, "needs recovery"):
            self.commit(review)
        self.app.profiles.journal.exists.return_value = False
        self.app.verification_for.side_effect = ValueError("Synthetic verification store error")
        with self.assertRaisesRegex(ValueError, "store error"):
            self.preflight()
        self.launch.assert_not_called()

    def test_failed_protected_operation_is_unknown_and_ticket_cannot_replay(self):
        review = self.preflight()
        self.launch.side_effect = OSError("Synthetic launch failure after profile work")
        with self.assertRaisesRegex(RuntimeError, "Native launch outcome unknown"):
            self.commit(review)
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review)
        self.launch.assert_called_once()

    def test_known_predispatch_cancellation_is_not_launch_success_or_unknown(self):
        review = self.preflight()
        self.launch.side_effect = LaunchCancelledBeforeDispatch("Owner closed")
        result = self.commit(review)
        self.assertFalse(result["launch_dispatched"])
        self.assertNotIn("launched_profile_id", result)
        self.assertIn("cancelled before dispatch", result["message"])
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review)

    def test_ticket_is_bounded_owner_bound_and_fingerprint_cannot_be_substituted(self):
        reviews = [self.preflight() for _ in range(33)]
        self.assertEqual(len(self.library.play_requests.tickets), 32)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.commit(reviews[0])
        with self.assertRaisesRegex(ValueError, "warning changed"):
            self.commit(reviews[-1], fingerprint="f" * 64)
        review = self.preflight()
        other = app(self.app.library)
        with self.assertRaisesRegex(ValueError, "expired or was used"):
            dispatch(
                self.library,
                other,
                "maptools.play_commit",
                {
                    **self.params,
                    "ticket": review["ticket"],
                    "fingerprint": review["fingerprint"],
                    "confirmed": True,
                },
                launch=self.launch,
            )
        self.launch.assert_not_called()

    def test_original_play_is_build_bound_and_uses_same_protected_callback(self):
        review = self.preflight(profile_id="original-game", archive_sha256="", variant_id="")
        self.assertEqual(review["target"]["archive_sha256"], "")
        self.commit(review, profile_id="original-game", archive_sha256="", variant_id="")
        self.launch.assert_called_once_with("original-game")
        self.app.verification_for.assert_not_called()

    def preparation(self):
        choice = SimpleNamespace(
            recipe_id="reviewed-test-repair",
            title="Test compatibility edition",
            description="Experimental repair; gameplay unverified.",
        )
        self.app.compatibility_recipes.return_value = (choice,)
        self.app.plan_recipe_batch = Mock(
            return_value=dict(
                all_supported=True,
                jobs=[dict(status="supported", binding="current-resource-binding")],
            )
        )
        edition = replace(record(), profile_id="synthetic-compiled", variant_id="d" * 64)
        self.app.create_recipe_edition = Mock(return_value=edition)
        return edition

    def test_preflight_does_not_prepare_and_commit_uses_maintained_service_then_actual_edition(
        self,
    ):
        edition = self.preparation()
        review = self.preflight()
        self.assertIn("Experimental", review["warning"])
        self.assertIn("saves remain on their own edition", review["warning"])
        self.app.create_recipe_edition.assert_not_called()
        self.launch.assert_not_called()
        self.launch.side_effect = lambda profile: (
            self.app.create_recipe_edition.assert_called_once_with(
                record(),
                "reviewed-test-repair",
                expected_plan={"binding": "current-resource-binding"},
            )
        )
        result = self.commit(review)
        self.launch.assert_called_once_with(edition.profile_id)
        self.assertEqual(result["target"]["profile_id"], record().profile_id)
        self.assertEqual(result["launched_profile_id"], edition.profile_id)
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review)

    def test_original_choice_is_reviewed_separately_and_never_prepares(self):
        self.preparation()
        review = self.preflight()
        with self.assertRaisesRegex(ValueError, "target or warning changed"):
            self.commit(review, prepare_compatibility=False)
        review = self.preflight(prepare_compatibility=False)
        self.assertIsNone(review["preparation"])
        self.assertEqual(review["warning"], "")
        self.commit(review, prepare_compatibility=False)
        self.app.create_recipe_edition.assert_not_called()
        self.launch.assert_called_once_with(record().profile_id)

    def test_changed_plan_or_unconfirmed_preparation_never_compiles_or_launches(self):
        self.preparation()
        review = self.preflight()
        self.app.plan_recipe_batch.return_value["jobs"][0]["binding"] = "changed-stock-or-donor"
        with self.assertRaisesRegex(ValueError, "target or warning changed"):
            self.commit(review)
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.commit(self.preflight(), confirmed=False)
        self.app.create_recipe_edition.assert_not_called()
        self.launch.assert_not_called()

    def test_unavailable_or_ambiguous_recipe_is_visible_original_launch_only(self):
        self.preparation()
        self.app.plan_recipe_batch.return_value = dict(
            all_supported=False, jobs=[dict(reason="Donor unavailable")]
        )
        review = self.preflight()
        self.assertIsNone(review["preparation"])
        self.assertIn("Donor unavailable", review["warning"])
        self.assertIn("uses the original edition", review["warning"])
        self.commit(review)
        choice = self.app.compatibility_recipes.return_value[0]
        self.app.compatibility_recipes.return_value = (choice, choice)
        review = self.preflight()
        self.assertIsNone(review["preparation"])
        self.assertIn("Multiple compatibility editions", review["warning"])
        self.app.create_recipe_edition.assert_not_called()

    def test_preparation_failure_retains_unknown_consumed_ticket_without_launch(self):
        self.preparation()
        review = self.preflight()
        self.app.create_recipe_edition.side_effect = OSError("Synthetic late preparation failure")
        with self.assertRaisesRegex(RuntimeError, "Native launch outcome unknown"):
            self.commit(review)
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review)
        self.app.create_recipe_edition.assert_called_once()
        self.launch.assert_not_called()

    def test_preparation_option_must_be_boolean(self):
        for value in ("false", 0, 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Boolean"):
                self.preflight(prepare_compatibility=value)

    def test_finite_deadline_is_fixed_consumed_and_existing_edition_only(self):
        deadline = time.monotonic() + 20
        with self.assertRaisesRegex(ValueError, "cached compatibility edition"):
            self.preflight(launch_deadline=deadline)
        review = self.preflight(launch_deadline=deadline, prepare_compatibility=False)
        self.assertEqual(review["launch_deadline"], deadline)
        with self.assertRaisesRegex(ValueError, "target or warning changed"):
            self.commit(review, launch_deadline=deadline + 1, prepare_compatibility=False)
        self.launch.assert_not_called()
        review = self.preflight(launch_deadline=deadline, prepare_compatibility=False)
        self.commit(review, launch_deadline=deadline, prepare_compatibility=False)
        self.launch.assert_called_once_with(record().profile_id, launch_deadline=deadline)

    def test_finite_deadline_refuses_bad_values_and_expiry_without_dispatch(self):
        now = time.monotonic()
        for value in (
            None,
            True,
            "future",
            now - 1,
            now + 481,
            float("nan"),
            float("inf"),
            10**1000,
        ):
            with (
                self.subTest(value=str(value)[:30]),
                self.assertRaisesRegex(ValueError, "deadline"),
            ):
                self.preflight(launch_deadline=value, prepare_compatibility=False)
        deadline = now + 20
        review = self.preflight(launch_deadline=deadline, prepare_compatibility=False)
        with patch("smr_launcher.native_map_tools.time.monotonic", return_value=deadline):
            with self.assertRaisesRegex(ValueError, "deadline expired"):
                self.commit(review, launch_deadline=deadline, prepare_compatibility=False)
        with self.assertRaisesRegex(ValueError, "was used"):
            self.commit(review, launch_deadline=deadline, prepare_compatibility=False)
        self.launch.assert_not_called()

    def test_real_preparation_cache_preserves_original_and_sibling_saves(self):
        from test_application import ApplicationTests
        from smr_launcher import recipe_service

        fixture = ApplicationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original, rule, binding, _stock = fixture.compiled_fixture()
        application = fixture.application
        saves = application.profiles._profile_path(original.profile_id) / "Saves"
        (saves / "original.sav").write_bytes(b"original user save")
        source_before = fixture.snapshot_files(application.imports / original.imported_directory)
        archive_before = fixture.archive.read_bytes()
        params = {
            **context(original, application.installation.executable_sha256),
            "profile_id": original.profile_id,
        }
        choice = SimpleNamespace(
            recipe_id=binding.recipe_id, title="Synthetic repair", description="Experimental"
        )
        library, launched = NativeLibrary(), []
        with (
            patch.object(application, "compatibility_recipes", return_value=(choice,)),
            patch.object(recipe_service, "resolve_bundled_recipe", return_value=((rule,), binding)),
            patch.dict(
                recipe_service._RECIPES,
                {binding.recipe_id: SimpleNamespace(additions=(), native_acceptance="")},
            ),
        ):
            for index in (1, 2):
                review = dispatch(library, application, "maptools.play_preflight", params)
                self.assertNotIn(
                    "expected_plan", review, "Resource paths stay inside backend tickets"
                )
                result = dispatch(
                    library,
                    application,
                    "maptools.play_commit",
                    dict(
                        params,
                        ticket=review["ticket"],
                        fingerprint=review["fingerprint"],
                        confirmed=True,
                    ),
                    launch=launched.append,
                )
                edition = next(
                    r
                    for r in application.catalogue()
                    if r.profile_id == result["launched_profile_id"]
                )
                application._validate_recipe_context(edition, prepared_output=True)
                edition_saves = application.profiles._profile_path(edition.profile_id) / "Saves"
                if index == 1:
                    (edition_saves / "compiled.sav").write_bytes(b"compiled user save")
                self.assertEqual(
                    (edition_saves / "compiled.sav").read_bytes(), b"compiled user save"
                )
            self.assertEqual(launched, [edition.profile_id, edition.profile_id])
            self.assertEqual(len(application.catalogue()), 2)
            self.assertEqual((saves / "original.sav").read_bytes(), b"original user save")
            self.assertEqual(
                fixture.snapshot_files(application.imports / original.imported_directory),
                source_before,
            )
            self.assertEqual(fixture.archive.read_bytes(), archive_before)
            # A cached edition has independent warning history. Preparing it
            # cannot borrow consent to the original's different warning state.
            with patch.object(
                application,
                "verification_for",
                side_effect=lambda r: (
                    SimpleNamespace(issue="Known cached-edition crash")
                    if r.profile_id == edition.profile_id
                    else None
                ),
            ):
                review = dispatch(library, application, "maptools.play_preflight", params)
                result = dispatch(
                    library,
                    application,
                    "maptools.play_commit",
                    dict(
                        params,
                        ticket=review["ticket"],
                        fingerprint=review["fingerprint"],
                        confirmed=True,
                    ),
                    launch=launched.append,
                )
                self.assertNotIn("launched_profile_id", result)
                self.assertEqual(result["prepared_profile_id"], edition.profile_id)
                self.assertIn("Known cached-edition crash", result["message"])
                self.assertEqual(launched, [edition.profile_id, edition.profile_id])
                with self.assertRaisesRegex(ValueError, "was used"):
                    dispatch(
                        library,
                        application,
                        "maptools.play_commit",
                        dict(
                            params,
                            ticket=review["ticket"],
                            fingerprint=review["fingerprint"],
                            confirmed=True,
                        ),
                        launch=launched.append,
                    )
                # Selecting the actual edition uses the unchanged warning review.
                actual_params = {
                    **context(edition, application.installation.executable_sha256),
                    "profile_id": edition.profile_id,
                }
                with patch.object(application, "compatibility_recipes", return_value=()):
                    actual_review = dispatch(
                        library, application, "maptools.play_preflight", actual_params
                    )
                    self.assertEqual(actual_review["warning"], "Known cached-edition crash")
                    dispatch(
                        library,
                        application,
                        "maptools.play_commit",
                        dict(
                            actual_params,
                            ticket=actual_review["ticket"],
                            fingerprint=actual_review["fingerprint"],
                            confirmed=True,
                        ),
                        launch=launched.append,
                    )
                    self.assertEqual(launched[-1], edition.profile_id)
            self.assertEqual((saves / "original.sav").read_bytes(), b"original user save")
            self.assertEqual((edition_saves / "compiled.sav").read_bytes(), b"compiled user save")
            before = application.catalogue_file.read_bytes()
            with self.assertRaisesRegex(Exception, "Compatibility inputs changed"):
                application.create_recipe_edition(
                    original, binding.recipe_id, expected_plan={"changed": True}
                )
            self.assertEqual(application.catalogue_file.read_bytes(), before)
            application.profiles.journal.write_text("unresolved owner journal")
            try:
                with self.assertRaisesRegex(Exception, "Pending activation requires its owner"):
                    application.create_recipe_edition(
                        original, binding.recipe_id, expected_plan={"changed": True}
                    )
                self.assertEqual(
                    application.profiles.journal.read_text(), "unresolved owner journal"
                )
                self.assertEqual(application.catalogue_file.read_bytes(), before)
            finally:
                application.profiles.journal.unlink()

    def test_native_dispatch_delivers_verification_and_launch_errors_without_tk_dialogs(self):
        owner = window()
        owner.close_when_idle = False
        owner.app = self.app
        owner.native_library = self.library
        owner._play_with_crash_monitor = self.launch

        def background(operation, done, **_):
            try:
                value = operation()
            except Exception as exc:
                done(False, exc)
            else:
                done(True, value)

        owner._background = background
        with (
            patch("smr_launcher.gui.messagebox.askyesno", side_effect=AssertionError("Tk warning")),
            patch("smr_launcher.gui.messagebox.showerror", side_effect=AssertionError("Tk error")),
        ):
            self.app.verification_for.side_effect = ValueError("Synthetic verification error")
            owner._native_dispatch(
                {
                    "id": "preflight-error",
                    "method": "maptools.play_preflight",
                    "params": self.params,
                }
            )
            reply = owner._emit_native.call_args.args[0]
            self.assertFalse(reply["ok"])
            self.assertIn("verification error", reply["error"])
            self.app.verification_for.side_effect = None
            review = self.preflight()
            self.launch.side_effect = RuntimeError("Synthetic launch error")
            owner._native_dispatch(
                {
                    "id": "commit-error",
                    "method": "maptools.play_commit",
                    "params": {
                        **self.params,
                        "ticket": review["ticket"],
                        "fingerprint": review["fingerprint"],
                        "confirmed": True,
                    },
                }
            )
            reply = owner._emit_native.call_args.args[0]
            self.assertFalse(reply["ok"])
            self.assertIn("Native launch outcome unknown", reply["error"])

    def test_owner_lifetime_and_initializing_checks_do_not_call_protected_operation(self):
        owner = window()
        owner.close_when_idle = False
        owner.app = self.app
        owner.native_library = self.library
        owner._play_with_crash_monitor = self.launch
        for field in ("closed", "close_when_idle", "initializing"):
            setattr(owner, field, True)
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "owner changed or is closing"),
            ):
                owner._native_launch(self.app, record().profile_id)
            setattr(owner, field, False)
        owner.app = object()
        with self.assertRaises(ValueError):
            owner._native_launch(self.app, record().profile_id)
        self.launch.assert_not_called()
