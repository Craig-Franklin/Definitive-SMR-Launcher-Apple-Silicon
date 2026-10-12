"""Native launch review with fake verification/protected launch; no game or monitors."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from test_native_backend import app, record, window
from smr_launcher.native_backend import NativeLibrary
from smr_launcher.native_map_tools import dispatch
from smr_launcher.play_history import context


class NativePlayTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.app = app(Path(folder.name).resolve())
        self.app._stopped = Mock()
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
