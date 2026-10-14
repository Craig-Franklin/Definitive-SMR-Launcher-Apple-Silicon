"""Exact filesystem identity checks with temporary synthetic files only."""

from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/research")]
import unique_save_file as save
from smr_launcher.resource_identity import ResourceFingerprintError


REFUSALS = (ValueError, OSError, ResourceFingerprintError)


class SaveFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.profile = self.root / "diagnostic-profile"
        self.directory = self.profile / "observed-directory"
        self.directory.mkdir(parents=True)
        self.keep = self.directory / "keep.original"
        self.keep.write_bytes(b"original synthetic save must survive")
        self.target = self.directory / "Exact.observed-filename"
        self.scope = save.SaveScope(
            job="synthetic-job",
            archive_sha256="a" * 64,
            variant_id="b" * 64,
            scenario="synthetic/scenario",
            game_executable_sha256="c" * 64,
            diagnostic_profile="synthetic-diagnostic",
            owned_profile_path=str(self.profile),
        )
        self.intent = save.SaveIntent(str(self.directory), self.target.name, "d" * 64)
        self.clock = [100.0]
        self.enterContext(patch.object(save.time, "monotonic", side_effect=lambda: self.clock[0]))
        self.original = self.keep.read_bytes()

    def before(self, **kwargs):
        return save.capture_before(self.scope, self.intent, **kwargs)

    def new_sample(self, before):
        self.target.write_bytes(b"complete synthetic exact new file")
        return save.inspect_new(before)

    def bound(self):
        before = self.before()
        first = self.new_sample(before)
        self.clock[0] += 1
        return save.bind_stable(before, first, minimum_stable_seconds=0.5, deadline=110)


class UniqueSaveFileTests(SaveFixture):
    def test_exact_new_file_binds_actual_inode_size_digest_and_preserves_existing(self):
        bound = self.bound()
        reopened = save.verify_reopen(bound, self.scope, deadline=110)
        fact, actual = reopened["exact_target"], self.target.stat()
        self.assertEqual(fact["name"], self.target.name)
        self.assertEqual(fact["identity"][0:2], (actual.st_dev, actual.st_ino))
        self.assertEqual(fact["identity"][6], actual.st_size)
        self.assertEqual(fact["sha256"], hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertEqual(self.keep.read_bytes(), self.original)
        self.assertEqual(
            {x.name for x in self.directory.iterdir()}, {self.keep.name, self.target.name}
        )
        self.assertEqual(reopened["scope"]["job"], self.scope.job)
        self.assertEqual(reopened["claim"], "filesystem_identity_only")

    def test_preexisting_target_and_filesystem_case_alias_refuse_overwrite(self):
        self.target.write_bytes(b"must not overwrite")
        with self.assertRaises(save.SaveFileRefused):
            self.before()
        self.assertEqual(self.target.read_bytes(), b"must not overwrite")
        alias = self.keep.with_name(self.keep.name.upper())
        intent = replace(self.intent, filename=alias.name)
        if alias.exists():
            with self.assertRaises(save.SaveFileRefused):
                save.capture_before(self.scope, intent)
        else:
            self.assertEqual(save.capture_before(self.scope, intent).intent.filename, alias.name)

    def test_target_created_after_before_refuses_before_save_commit(self):
        before = self.before()
        self.target.write_bytes(b"another writer created this")
        with self.assertRaises(save.SaveFileRefused):
            save.require_absent(before)
        self.assertEqual(self.target.read_bytes(), b"another writer created this")

    def test_missing_zero_and_oversized_target_refuse(self):
        before = self.before(limits=save.Limits(file_bytes=64))
        for contents in (None, b"", b"x" * 65):
            if contents is not None:
                self.target.write_bytes(contents)
            with self.assertRaises(REFUSALS):
                save.inspect_new(before)
        self.assertEqual(self.keep.read_bytes(), self.original)

    def test_additional_unknown_file_holds_and_preserves_every_file(self):
        before = self.before()
        self.target.write_bytes(b"intended")
        extra = self.directory / "unexpected-autosave-or-other-file"
        extra.write_bytes(b"preserve this too")
        with self.assertRaisesRegex(save.SaveFileRefused, "additional files ambiguous"):
            save.inspect_new(before)
        self.assertEqual(self.target.read_bytes(), b"intended")
        self.assertEqual(extra.read_bytes(), b"preserve this too")
        self.assertEqual(self.keep.read_bytes(), self.original)

    def test_preexisting_byte_metadata_removal_and_same_byte_replacement_drift(self):
        for mode in ("bytes", "metadata", "missing", "replacement"):
            with self.subTest(mode=mode):
                if self.target.exists():
                    self.target.unlink()
                self.keep.write_bytes(self.original)
                before = self.before()
                self.target.write_bytes(b"intended new file")
                if mode == "bytes":
                    self.keep.write_bytes(b"changed prior save")
                elif mode == "metadata":
                    os.chmod(self.keep, 0o400)
                elif mode == "missing":
                    self.keep.unlink()
                else:
                    replacement = self.root / "replacement"
                    replacement.write_bytes(self.original)
                    os.replace(replacement, self.keep)
                with self.assertRaises(save.SaveFileRefused):
                    save.inspect_new(before)
                if self.keep.exists():
                    os.chmod(self.keep, 0o600)

    def test_identical_bytes_target_replacement_is_not_same_file(self):
        bound = self.bound()
        replacement = self.root / "same-bytes-different-file"
        replacement.write_bytes(self.target.read_bytes())
        os.replace(replacement, self.target)
        with self.assertRaisesRegex(save.SaveFileRefused, "replaced/changed"):
            save.verify_reopen(bound, self.scope, deadline=110)

    def test_partial_write_between_samples_and_unchanged_size_mutation_refuse(self):
        before = self.before()
        first = self.new_sample(before)
        self.target.write_bytes(b"x" * self.target.stat().st_size)
        self.clock[0] += 1
        with self.assertRaisesRegex(save.SaveFileRefused, "between stability samples"):
            save.bind_stable(before, first, minimum_stable_seconds=0.5, deadline=110)

    def test_mutation_during_hash_is_detected_without_a_retry(self):
        before = self.before()
        self.target.write_bytes(b"write is still in flight")
        real = save.save_digest
        calls = []

        def changing(path):
            calls.append(path.name)
            value = real(path)
            if path == self.target:
                path.write_bytes(b"write changed after read")
            return value

        with patch.object(save, "save_digest", side_effect=changing):
            with self.assertRaisesRegex(save.SaveFileRefused, "changed while hashing"):
                save.inspect_new(before)
        self.assertEqual(calls.count(self.target.name), 1)

    def test_previously_hashed_file_mutation_before_inventory_end_is_detected(self):
        before = self.before()
        self.target.write_bytes(b"new")
        real = save.save_digest

        def changing(path):
            value = real(path)
            if path == self.keep:
                self.target.write_bytes(b"changed while another file was read")
            return value

        with patch.object(save, "save_digest", side_effect=changing):
            with self.assertRaisesRegex(save.SaveFileRefused, "before completion"):
                save.inspect_new(before)

    def test_symlink_hardlink_fifo_and_nested_directory_refuse(self):
        for kind in ("symlink", "hardlink", "fifo", "directory"):
            with self.subTest(kind=kind):
                extra = self.directory / "unsafe"
                if kind == "symlink":
                    extra.symlink_to(self.keep)
                elif kind == "hardlink":
                    os.link(self.keep, extra)
                elif kind == "fifo":
                    os.mkfifo(extra)
                else:
                    extra.mkdir()
                with self.assertRaises(REFUSALS):
                    self.before()
                if kind == "directory":
                    extra.rmdir()
                else:
                    extra.unlink()
        self.assertEqual(self.keep.read_bytes(), self.original)

    def test_link_created_after_binding_refuses_reopen(self):
        bound = self.bound()
        os.link(self.target, self.root / "outside-hardlink")
        with self.assertRaises(REFUSALS):
            save.verify_reopen(bound, self.scope, deadline=110)

    def test_symlink_ancestor_and_replaced_directory_refuse(self):
        alias = self.root / "profile-link"
        alias.symlink_to(self.profile, target_is_directory=True)
        with self.assertRaises(REFUSALS):
            save.capture_before(
                replace(self.scope, owned_profile_path=str(alias)),
                replace(self.intent, save_directory=str(alias / self.directory.name)),
            )
        before = self.before()
        displaced = self.root / "old-directory"
        self.directory.rename(displaced)
        shutil.copytree(displaced, self.directory)
        self.target.write_bytes(b"new")
        with self.assertRaisesRegex(save.SaveFileRefused, "directory identity changed"):
            save.inspect_new(before)

    def test_entry_file_and_total_budgets_refuse_without_save_writes(self):
        with patch.object(save, "save_digest", wraps=save.save_digest) as hashing:
            with self.assertRaises(save.SaveFileRefused):
                self.before(limits=save.Limits(file_bytes=1))
            hashing.assert_not_called()
        with self.assertRaises(save.SaveFileRefused):
            self.before(limits=save.Limits(total_bytes=1))
        (self.directory / "second").write_bytes(b"second")
        with self.assertRaises(save.SaveFileRefused):
            self.before(limits=save.Limits(entries=1))
        self.assertEqual(self.keep.read_bytes(), self.original)

    def test_stability_requires_positive_elapsed_interval_and_original_deadline(self):
        before = self.before()
        first = self.new_sample(before)
        with self.assertRaisesRegex(save.SaveFileRefused, "interval not observed"):
            save.bind_stable(before, first, minimum_stable_seconds=0.5, deadline=110)
        for interval in (0, -1, True, float("inf"), float("nan"), 31):
            with self.assertRaises(save.SaveFileRefused):
                save.bind_stable(before, first, minimum_stable_seconds=interval, deadline=110)
        self.clock[0] = 111
        with self.assertRaises(save.SaveFileRefused):
            save.bind_stable(before, first, minimum_stable_seconds=0.5, deadline=110)

    def test_wrong_job_or_mapping_cannot_substitute_save_authority(self):
        bound = self.bound()
        for changed in (
            replace(self.scope, job="another-job"),
            replace(self.scope, variant_id="e" * 64),
            replace(self.scope, scenario="another-scenario"),
        ):
            with self.assertRaises(save.SaveFileRefused):
                save.verify_reopen(bound, changed, deadline=110)
        first = bound.first
        foreign = replace(bound.before, scope=replace(self.scope, job="another-job"))
        with self.assertRaises(save.SaveFileRefused):
            save.bind_stable(foreign, first, minimum_stable_seconds=0.5, deadline=110)

    def test_reopen_cannot_replace_the_original_bound_deadline(self):
        bound = self.bound()
        self.clock[0] = 111
        with self.assertRaisesRegex(save.SaveFileRefused, "deadline changed"):
            save.verify_reopen(bound, self.scope, deadline=120)
        with self.assertRaisesRegex(save.SaveFileRefused, "deadline expired"):
            save.verify_reopen(bound, self.scope, deadline=110)

    def test_owned_relocation_requires_explicit_mapping_and_same_directory_and_file(self):
        bound = self.bound()
        destination = self.root / "retained-diagnostic-profile"
        self.profile.rename(destination)
        current = replace(self.scope, owned_profile_path=str(destination))
        mapped_directory = destination / self.directory.name
        with self.assertRaises(save.SaveFileRefused):
            save.verify_reopen(bound, current, deadline=110)
        mapping = save.OwnedPathMapping(
            str(self.profile),
            str(destination),
            str(self.directory),
            str(mapped_directory),
            "e" * 64,
        )
        reopened = save.verify_reopen(bound, current, deadline=110, mapping=mapping)
        self.assertEqual(reopened["inventory"]["directory"], str(mapped_directory))
        actual = (mapped_directory / self.target.name).stat()
        self.assertEqual(reopened["exact_target"]["identity"][0:2], (actual.st_dev, actual.st_ino))
        wrong = replace(mapping, from_profile_path=str(self.root / "foreign"))
        with self.assertRaises(save.SaveFileRefused):
            save.verify_reopen(bound, current, deadline=110, mapping=wrong)

    def test_explicit_mapping_never_promotes_a_byte_identical_copy(self):
        bound = self.bound()
        destination = self.root / "copied-profile"
        shutil.copytree(self.profile, destination)
        current = replace(self.scope, owned_profile_path=str(destination))
        mapping = save.OwnedPathMapping(
            str(self.profile),
            str(destination),
            str(self.directory),
            str(destination / self.directory.name),
            "e" * 64,
        )
        with self.assertRaisesRegex(save.SaveFileRefused, "replaced/changed"):
            save.verify_reopen(bound, current, deadline=110, mapping=mapping)

    def test_public_digest_reference_has_no_private_path_or_success_claim(self):
        bound = self.bound()
        public = save.public_reference(bound)
        self.assertEqual(
            public,
            dict(
                save_id=hashlib.sha256(self.target.read_bytes()).hexdigest(),
                claim="filesystem_identity_only",
                save_made="unknown",
                save_reopened="unknown",
                continued_action="unknown",
            ),
        )
        encoded = json.dumps(public)
        for private in (
            str(self.profile),
            self.target.name,
            self.scope.job,
            self.scope.scenario,
            self.scope.diagnostic_profile,
        ):
            self.assertNotIn(private, encoded)
        private = asdict(bound)
        self.assertEqual(private["before"]["scope"], asdict(self.scope))
        self.assertEqual(private["before"]["intent"], asdict(self.intent))

    def test_invalid_intent_scope_path_and_limits_refuse(self):
        for filename in ("../save", "/save", "save/part", "save\\part", "", "..", "save\x00"):
            with self.assertRaises(ValueError):
                replace(self.intent, filename=filename)
        with self.assertRaises(save.SaveFileRefused):
            save.capture_before(self.scope, replace(self.intent, save_directory=str(self.root)))
        with self.assertRaises(ValueError):
            replace(self.scope, archive_sha256="unknown")
        for limits in (dict(entries=True), dict(total_bytes=0), dict(file_bytes=513 * 1024**2)):
            with self.assertRaises(save.SaveFileRefused):
                save.Limits(**limits)


class FiniteSaveCycleTests(SaveFixture):
    @classmethod
    def setUpClass(cls):
        paths = [os.environ.get("SMR_SAVE_CYCLE_ADAPTER"), os.environ.get("SMR_SAVE_LIFECYCLE")]
        defaults = ["save_cycle", "finite_operation"]
        cls.modules = []
        for index, (path, default) in enumerate(zip(paths, defaults)):
            if path:
                spec = importlib.util.spec_from_file_location(
                    "synthetic_save_contract_" + str(index), path
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            else:
                module = __import__(default)
            cls.modules.append(module)

    def run_cycle(
        self,
        *,
        save_mode="exact",
        receipt_failure=None,
        change_before_reopen=False,
        relaunch_stop_result=True,
        receipt_effect=None,
        guard_effect=None,
        hook_effect=None,
    ):
        source, lifecycle = self.modules
        events, receipts = [], {}
        available, injected = [True], set()

        def inject(boundary, requested, actual):
            if requested is None or requested[0] != actual or boundary in injected:
                return
            injected.add(boundary)
            effect = requested[1]
            if effect == "expire":
                self.clock[0] = 111
            elif effect == "replace":
                if self.target.exists():
                    replacement = self.root / "replacement"
                    replacement.write_bytes(self.target.read_bytes())
                    os.replace(replacement, self.target)
                else:
                    self.target.write_bytes(b"racing writer must not be overwritten")
            elif effect == "unavailable":
                available[0] = False
            else:
                self.fail("Unknown synthetic boundary effect")

        def write(name, value):
            events.append(name)
            if name == receipt_failure:
                raise OSError("synthetic receipt failure")
            receipts[name] = value
            inject("receipt", receipt_effect, name)

        def guard(phase, *args):
            events.append("guard_" + phase)
            inject("guard", guard_effect, phase)
            if not available[0]:
                raise save.SaveFileRefused("Synthetic owner availability changed")

        def delivery_hook(phase, action):
            # Model blocking preparation inside a one-use hook, then its actual
            # delivery boundary. No event is recorded as delivered before this.
            def delivery(*args, validate_delivery, deadline):
                self.assertEqual(deadline, 110)
                inject("hook", hook_effect, phase)
                fresh = validate_delivery()
                return action(fresh) if args else action()

            return delivery

        def save_once(intent):
            events.append("save_once")
            self.target.write_bytes(b"synthetic new exact file")
            if save_mode == "ambiguous":
                (self.directory / "unknown-extra").write_bytes(b"preserve")

        def wait_stability(when, deadline):
            events.append("bounded_stability_wait")
            self.assertLess(when, deadline)
            self.clock[0] = when

        def relaunch():
            events.append("relaunch_once")
            if change_before_reopen:
                replacement = self.root / "replacement"
                replacement.write_bytes(self.target.read_bytes())
                os.replace(replacement, self.target)

        owner = SimpleNamespace(
            prepare=lambda: events.append("prepare"),
            launch=lambda: events.append("launch"),
            guard=guard,
            write=write,
            save_once=delivery_hook("save", save_once),
            wait_stability=wait_stability,
            stop_for_relaunch=lambda: (
                events.append("exact_stop_for_relaunch") or relaunch_stop_result
            ),
            owned_reopen_location=lambda bound: (self.scope, None),
            relaunch_once=delivery_hook("relaunch", relaunch),
            reopen_once=delivery_hook("reopen", lambda receipt: events.append("exact_reopen_once")),
            observe_reopened_world_and_continued_action=delivery_hook(
                "continued-action",
                lambda: events.append("continued_action") or {"source": "synthetic_unverified"},
            ),
            persist_outcome=lambda value: events.append("raw_outcome"),
            stop=lambda: events.append("unconditional_final_stop") or True,
            restore=lambda: events.append("preservation_restore") or True,
            finish=lambda *args: events.append("terminal_index"),
        )
        owner.observe = lambda: source.observe_save_cycle(
            owner, self.scope, self.intent, minimum_stable_seconds=0.5, deadline=110
        )
        result = lifecycle.run_calibration(owner)
        return result, events, receipts

    def test_connected_success_is_filesystem_only_and_uses_existing_terminal_order(self):
        result, events, receipts = self.run_cycle()
        self.assertTrue(result["terminal_finished"], result["errors"])
        for earlier, later in (
            ("save-before.json", "save_once"),
            ("save-bound-file.json", "exact_stop_for_relaunch"),
            ("save-after-stop-identity.json", "relaunch_once"),
            ("save-before-reopen-identity.json", "exact_reopen_once"),
            ("raw_outcome", "unconditional_final_stop"),
            ("unconditional_final_stop", "preservation_restore"),
            ("preservation_restore", "terminal_index"),
        ):
            self.assertLess(events.index(earlier), events.index(later))
        self.assertEqual(events.count("save_once"), 1)
        self.assertEqual(events.count("exact_reopen_once"), 1)
        self.assertFalse(result["outcome"]["save_reopen_credit"])
        self.assertEqual(result["outcome"]["world_loaded"], "unknown")
        self.assertEqual(
            receipts["save-before-reopen-identity.json"]["exact_target"]["name"], self.target.name
        )

    def test_ambiguity_refuses_relaunch_and_reopen_but_unconditionally_cleans_up(self):
        result, events, _ = self.run_cycle(save_mode="ambiguous")
        self.assertEqual(result["outcome"]["status"], "automation_failed")
        self.assertNotIn("relaunch_once", events)
        self.assertNotIn("exact_reopen_once", events)
        self.assertIn("unconditional_final_stop", events)
        self.assertTrue(result["terminal_finished"], result["errors"])
        self.assertTrue(result["errors"][0].startswith("operation: "))
        self.assertEqual((self.directory / "unknown-extra").read_bytes(), b"preserve")

    def test_before_receipt_failure_permits_no_save_input_and_still_cleans_up(self):
        result, events, _ = self.run_cycle(receipt_failure="save-before.json")
        self.assertNotIn("save_once", events)
        self.assertIn("unconditional_final_stop", events)
        self.assertTrue(result["terminal_finished"], result["errors"])
        self.assertTrue(result["errors"][0].startswith("operation: "))
        self.assertFalse(self.target.exists())

    def test_same_byte_replacement_after_relaunch_refuses_reopen_and_cleans_up(self):
        result, events, _ = self.run_cycle(change_before_reopen=True)
        self.assertIn("relaunch_once", events)
        self.assertNotIn("exact_reopen_once", events)
        self.assertIn("unconditional_final_stop", events)
        self.assertTrue(result["terminal_finished"], result["errors"])
        self.assertTrue(result["errors"][0].startswith("operation: "))

    def assert_refused_before_delivery(self, result, events, forbidden):
        self.assertNotIn(forbidden, events)
        self.assertEqual(result["outcome"]["status"], "automation_failed")
        self.assertTrue(result["terminal_finished"], result["errors"])
        self.assertTrue(result["errors"][0].startswith("operation: "))
        self.assertLess(events.index("raw_outcome"), events.index("unconditional_final_stop"))
        self.assertLess(
            events.index("unconditional_final_stop"), events.index("preservation_restore")
        )
        self.assertEqual(self.keep.read_bytes(), self.original)

    def test_expired_reopen_receipt_blocks_input_before_dispatch(self):
        result, events, _ = self.run_cycle(
            receipt_effect=("save-before-reopen-identity.json", "expire")
        )
        self.assert_refused_before_delivery(result, events, "exact_reopen_once")
        self.assertIn("relaunch_once", events)

    def test_same_byte_replacement_during_reopen_receipt_blocks_input_before_dispatch(self):
        result, events, _ = self.run_cycle(
            receipt_effect=("save-before-reopen-identity.json", "replace")
        )
        self.assert_refused_before_delivery(result, events, "exact_reopen_once")
        self.assertEqual(self.target.read_bytes(), b"synthetic new exact file")

    def test_save_and_relaunch_receipt_delay_or_collision_blocks_affected_input(self):
        cases = (
            ("save-before.json", "expire", "save_once"),
            ("save-before.json", "replace", "save_once"),
            ("save-after-stop-identity.json", "expire", "relaunch_once"),
            ("save-after-stop-identity.json", "replace", "relaunch_once"),
        )
        for label, effect, forbidden in cases:
            with self.subTest(label=label, effect=effect):
                if self.target.exists():
                    self.target.unlink()
                self.clock[0] = 100
                result, events, _ = self.run_cycle(receipt_effect=(label, effect))
                self.assert_refused_before_delivery(result, events, forbidden)

    def test_blocking_guards_cannot_leave_expired_or_replaced_input_binding(self):
        for phase, forbidden in (
            ("save", "save_once"),
            ("relaunch", "relaunch_once"),
            ("reopen", "exact_reopen_once"),
            ("continued-action", "continued_action"),
        ):
            for effect in ("expire", "replace"):
                with self.subTest(phase=phase, effect=effect):
                    if self.target.exists():
                        self.target.unlink()
                    self.clock[0] = 100
                    result, events, _ = self.run_cycle(guard_effect=(phase, effect))
                    self.assert_refused_before_delivery(result, events, forbidden)
                    if phase == "continued-action":
                        self.assertIn("exact_reopen_once", events)

    def test_hook_blocking_prework_must_revalidate_before_each_actual_input(self):
        for phase, forbidden in (
            ("save", "save_once"),
            ("relaunch", "relaunch_once"),
            ("reopen", "exact_reopen_once"),
            ("continued-action", "continued_action"),
        ):
            for effect in ("expire", "replace", "unavailable"):
                with self.subTest(phase=phase, effect=effect):
                    if self.target.exists():
                        self.target.unlink()
                    self.clock[0] = 100
                    result, events, _ = self.run_cycle(hook_effect=(phase, effect))
                    self.assert_refused_before_delivery(result, events, forbidden)
                    if phase == "continued-action":
                        # Earlier input was delivered; subsequent failure cannot
                        # retroactively call that delivered reopen canceled.
                        self.assertEqual(events.count("exact_reopen_once"), 1)

    def test_availability_changed_during_receipt_is_rechecked_before_reopen(self):
        result, events, _ = self.run_cycle(
            receipt_effect=("save-before-reopen-identity.json", "unavailable")
        )
        self.assert_refused_before_delivery(result, events, "exact_reopen_once")

    def test_unproven_stop_blocks_relaunch_and_reopen_but_final_cleanup_still_runs(self):
        for value in (False, None, 1, "true"):
            with self.subTest(stop_result=value):
                if self.target.exists():
                    self.target.unlink()
                self.clock[0] = 100
                result, events, _ = self.run_cycle(relaunch_stop_result=value)
                self.assert_refused_before_delivery(result, events, "relaunch_once")
                self.assertNotIn("exact_reopen_once", events)
                self.assertIn("Exact stop before relaunch refused", result["errors"][0])
