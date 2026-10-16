"""Synthetic filesystem maintenance only; no native game/provider execution."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/research")]
from active_job_control import ControlRefused, decode, read_plain, require, sha, state_lock
from restoration_maintenance import RestorationMaintenance, SCOPE, file_snapshot, tree_snapshot
from restoration_maintenance import verify_imported_sources
from smr_launcher.activation import FilesystemProfiles
from smr_launcher.resource_identity import fingerprint_resources


def dump(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}


class PreferenceSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name).resolve() / "preferences"
        self.path.write_bytes(b"synthetic preference")
        self.before = self.path.stat()
        self.data = self.path.read_bytes()

    def changed_stat(self, field):
        values = list(self.before)
        attributes = {
            name: getattr(self.before, name) for name in dir(self.before) if name.startswith("st_")
        }
        attributes[field] += 1
        if field == "st_atime":
            attributes["st_atime_ns"] += 1_000_000_000
        fields = (
            "st_mode",
            "st_ino",
            "st_dev",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_atime",
            "st_mtime",
            "st_ctime",
        )
        if field in fields:
            values[fields.index(field)] = attributes[field]
        return os.stat_result(values, attributes)

    def snapshot_with_metadata(self, after):
        with (
            patch("restoration_maintenance.read_plain", return_value=self.data),
            patch.object(Path, "stat", side_effect=(self.before, after)),
        ):
            return file_snapshot(self.path)

    def test_read_updated_access_time_does_not_refuse_unchanged_preference(self):
        after = self.changed_stat("st_atime")
        self.assertNotEqual(self.before, after)
        result = self.snapshot_with_metadata(after)
        self.assertEqual(
            result,
            dict(
                sha256=sha(self.data),
                mode=self.before.st_mode & 0o7777,
                mtime_ns=self.before.st_mtime_ns,
            ),
        )
        self.assertEqual(file_snapshot(self.path), result)

    def test_identity_and_mutation_metadata_drift_still_refuse(self):
        for field in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ControlRefused, "Plain unchanged preference"):
                    self.snapshot_with_metadata(self.changed_stat(field))

    def test_same_size_content_change_with_restored_mtime_refuses(self):
        def mutate_after_read(path, **kwargs):
            data = read_plain(path, **kwargs)
            before = path.stat()
            path.write_bytes(b"x" * len(data))
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(path.stat().st_size, before.st_size)
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertNotEqual(path.stat().st_ctime_ns, before.st_ctime_ns)
            return data

        with patch("restoration_maintenance.read_plain", side_effect=mutate_after_read):
            with self.assertRaisesRegex(ControlRefused, "Plain unchanged preference"):
                file_snapshot(self.path)

    def test_inode_replacement_after_read_refuses(self):
        def replace_after_read(path, **kwargs):
            data = read_plain(path, **kwargs)
            replacement = path.with_name("replacement")
            replacement.write_bytes(data)
            os.replace(replacement, path)
            self.assertNotEqual(path.stat().st_ino, self.before.st_ino)
            return data

        with patch("restoration_maintenance.read_plain", side_effect=replace_after_read):
            with self.assertRaisesRegex(ControlRefused, "Plain unchanged preference"):
                file_snapshot(self.path)

    def test_symlink_and_hardlink_preferences_still_refuse(self):
        symbolic = self.path.with_name("symbolic")
        symbolic.symlink_to(self.path)
        with self.assertRaises(ControlRefused):
            file_snapshot(symbolic)
        linked = self.path.with_name("linked")
        os.link(self.path, linked)
        for path in (self.path, linked):
            with self.subTest(path=path.name), self.assertRaises(ControlRefused):
                file_snapshot(path)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.running, self.games, self.present, self.now = False, [], set(), 100.0
        self.live = self.tree("live")
        self.manager = FilesystemProfiles(
            self.live, self.root / "managed", ("CustomAssets", "UserMaps"), lambda: self.running
        )
        self.manager.enroll_original()
        self.canonical = self.tree("canonical")
        self.source = self.manager.register_variant("a" * 64, self.canonical)
        self.return_id = self.manager.register_variant("b" * 64, self.tree("return"))
        self.diagnostic = self.manager.register_variant("c" * 64, self.tree("diagnostic"))
        self.manager.switch(self.return_id)
        self.imported, self.prepared = self.tree("imported"), self.tree("prepared")
        protected = {
            "canonical": str(self.manager._profile_path(self.source)),
            "import": str(self.imported),
            "prepared": str(self.prepared),
            "stock": str(self.manager._profile_path("original-game")),
        }
        baseline = {role: tree_snapshot(path) for role, path in protected.items()}
        baseline["live"] = tree_snapshot(self.live)
        self.manager.switch(self.diagnostic)
        (self.live / "Saves/future.sav").write_bytes(b"retain future save")
        steam = self.root / "steamapps"
        assets = steam / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        assets.mkdir(parents=True)
        (assets / "asset.xml").write_bytes(b"synthetic")
        pref_rows, preferences = [], []
        for index, name in enumerate(("Preferences Data", "global.plist")):
            path, copy = self.root / name, self.root / ("preimage-" + str(index))
            path.write_bytes(b"before")
            shutil.copy2(path, copy)
            old = file_snapshot(copy)
            pref_rows.append({"path": str(path), "copy": str(copy), **old})
            if index == 0:
                path.write_bytes(b"after")
            preferences.append(
                {
                    "path": str(path),
                    "copy": str(copy),
                    "baseline": old,
                    "current": file_snapshot(path),
                }
            )
        original_preferences = dump(self.root / "preferences.json", pref_rows)
        code = self.root / "original-code.py"
        code.write_bytes(b"# frozen synthetic code\n")
        code_pin = {
            "path": str(code),
            "bytes": code.stat().st_size,
            "sha256": sha(code.read_bytes()),
        }
        self.job = {
            "root": {"run": "synthetic", "consumer_generation": 17, "coordinator": "owner"},
            "job": "synthetic-failed-attempt",
            "return_profile": self.return_id,
            "source_profile": self.source,
            "diagnostic_variant": "c" * 64,
            "source_original": {"variant_id": "a" * 64},
            "game_sha256": "d" * 64,
            "installed_assets_identity": fingerprint_resources({"installed_assets": assets})[
                "identity"
            ],
            "preservation": baseline,
            "closure": [code_pin],
        }
        job_pin = dump(self.root / "job.json", self.job)
        outcome = {
            "phase": "raw_outcome",
            "run_id": self.job["job"],
            "payload": {"status": "automation_failed", "cause": "unknown effect"},
        }
        outcome_pin = dump(self.root / "outcome.json", outcome)
        positive = dump(
            self.root / "exits.json",
            {
                "uncertain_input": True,
                "game_absent": True,
                "children": [
                    {"pid": 12, "role": "game", "positive_exit": True},
                    {"pid": 13, "role": "observer", "positive_exit": True},
                ],
            },
        )
        controller = dump(self.root / "controller.json", {"pid": 14})
        response = {
            "id": "synthetic-complete-response",
            "ok": False,
            "error": {
                "code": "window_not_focused",
                "message": "recipient changed; 1 press(es) may already have been delivered; unknown",
            },
            "_meta": {"runtimeId": "synthetic-runtime"},
        }
        response_pin = dump(self.root / "response.json", response)
        created = dump(self.root / "created.json", {"pid": 11, "parent_pid": 14, "mutable": True})
        exited = dump(
            self.root / "exited.json", {"pid": 11, "reaped": True, "owned_wait_returncode": 1}
        )
        self.lease = {"owner": "owner", "ticket": "held", "nonce": "synthetic"}
        admission = dump(
            self.root / "admission.json", {"lease": self.lease, "queue_path": "synthetic-queue"}
        )
        self.plan = {
            "scope": SCOPE,
            "root": self.job["root"],
            "original_job": job_pin,
            "original_admission": admission,
            "queue_path": "synthetic-queue",
            "lease": self.lease,
            "controller": controller,
            "positive_exits": positive,
            "owned_pids": [11, 12, 13, 14],
            "original_outcome": outcome_pin,
            "expected_outcome": outcome,
            "original_preferences": original_preferences,
            "preferences": preferences,
            "diagnostic_profile": self.diagnostic,
            "return_profile": self.return_id,
            "baseline": baseline,
            "protected_paths": protected,
            "installed_assets": str(assets),
            "diagnostic_snapshot": tree_snapshot(self.live),
            "provider_runtime_id": "synthetic-runtime",
            "completion": {
                "response": response_pin,
                "expected_response": response,
                "created": created,
                "exited": exited,
                "input_pid": 11,
            },
            "evidence": [
                outcome_pin,
                positive,
                response_pin,
                created,
                exited,
                controller,
                original_preferences,
                admission,
            ],
            "result_directory": str(self.root / "result"),
        }
        self.plan_path, self.approval_path = self.root / "plan.json", self.root / "approval.json"
        self.state_path = self.root / "state.json"

        @contextmanager
        def cleanup_guard():
            with state_lock(self.state_path):
                require(decode(read_plain(self.state_path))["owner"] == "owner", "Stale owner")
                yield

        @contextmanager
        def app_lock(**kwargs):
            self.assertEqual(kwargs, {"recover_removal": False, "nonblocking": True})
            yield

        self.app = SimpleNamespace(
            profiles=self.manager,
            library=self.root,
            imports=self.root,
            prepared=self.root,
            installation=SimpleNamespace(
                game_running=lambda: self.running, executable_sha256="d" * 64, steamapps_root=steam
            ),
            catalogue=lambda: [
                SimpleNamespace(
                    variant_id="a" * 64,
                    imported_directory="imported",
                    prepared_directory="prepared",
                )
            ],
            _locked=app_lock,
            _verify_installation_binding=lambda: None,
        )
        self.monitor = SimpleNamespace(
            games=lambda: self.games, inspect=lambda pid: object() if pid in self.present else None
        )
        self.control = SimpleNamespace(
            job=self.job, path=self.state_path, cleanup_guard=cleanup_guard
        )
        self.queue = SimpleNamespace(
            guard=lambda lease: require(lease == self.lease, "Lease changed")
        )
        self.seal()

    def tree(self, name):
        root = self.root / name
        for part in ("CustomAssets", "UserMaps", "Saves"):
            (root / part).mkdir(parents=True)
        (root / "CustomAssets/asset.xml").write_bytes(name.encode())
        (root / "Saves/original.sav").write_bytes(name.encode())
        return root

    def seal(self):
        plan_pin = dump(self.plan_path, self.plan)
        candidate_sha = sha(read_plain(ROOT / "scripts/research/restoration_maintenance.py"))
        review = dump(
            self.root / "review.json",
            {
                "decision": "GO_SOURCE_ONLY_RESTORATION_MAINTENANCE",
                "plan_sha256": plan_pin["sha256"],
                "candidate_sha256": candidate_sha,
                "findings": [],
            },
        )
        self.approval = {
            "scope": SCOPE,
            "approved_by": "Craig",
            "plan_sha256": plan_pin["sha256"],
            "root": self.job["root"],
            "original_job_sha256": self.plan["original_job"]["sha256"],
            "candidate_sha256": candidate_sha,
            "issued_epoch": 90,
            "expires_epoch": 390,
            "issued_monotonic": 90,
            "expires_monotonic": 390,
            "review": review,
        }
        self.approval_sha = dump(self.approval_path, self.approval)["sha256"]
        dump(self.state_path, {"owner": "owner", "restoration_maintenance": self.approval})

    def operation(self):
        return RestorationMaintenance(
            self.plan_path,
            self.approval_path,
            self.approval_sha,
            self.app,
            self.monitor,
            self.control,
            self.queue,
            clock=lambda: self.now,
            epoch=lambda: self.now,
        )

    def assert_refuses(self, pattern):
        before = tree_snapshot(self.live)
        with self.assertRaisesRegex((ControlRefused, FileNotFoundError), pattern):
            self.operation().run()
        self.assertEqual(tree_snapshot(self.live), before)
        self.assertEqual(self.manager._state()["active"], self.diagnostic)

    def completed_quit(self):
        request = {
            "job": self.job["job"],
            "stage": "finished",
            "frame": {"window": {"id": 30}},
            "process": {"pid": 12},
        }
        request_pin = dump(self.root / "request.json", request)
        decision = {
            "request_sha256": request_pin["sha256"],
            "choice": "action",
            "receiver": "owned_window",
            "action": ["hotkey", "--key", "CmdOrCtrl+Q"],
        }
        response = {
            "ok": True,
            "_meta": {"runtimeId": "synthetic-runtime"},
            "result": {
                "action": {"path": "synthetic", "actionName": "hotkey", "targetWindowId": 30},
                "snapshot": {"window": {"id": 30}, "app": {"pid": 12}},
            },
        }
        self.plan["completion"].update(
            kind="completed_quit_hotkey",
            request=request_pin,
            decision=dump(self.root / "decision.json", decision),
            response=dump(self.root / "quit-response.json", response),
            expected_response=response,
            exited=dump(
                self.root / "quit-exit.json",
                {"pid": 11, "reaped": True, "owned_wait_returncode": 0},
            ),
        )
        self.seal()

    def test_completed_quit_restores_without_normal_exit_or_effect_credit(self):
        self.completed_quit()
        original = read_plain(self.root / "outcome.json")
        receipt = decode(read_plain(self.operation().run()))
        self.assertEqual(tree_snapshot(self.live), self.plan["baseline"]["live"])
        self.assertEqual(
            tree_snapshot(self.manager._profile_path(self.diagnostic)),
            self.plan["diagnostic_snapshot"],
        )
        for row in self.plan["preferences"]:
            self.assertEqual(file_snapshot(row["path"]), row["baseline"])
        self.assertEqual(read_plain(self.root / "outcome.json"), original)
        self.assertEqual(receipt["original_effect"], "unknown")
        self.assertFalse(receipt["original_run_restored"])
        self.assertFalse(receipt["lease_released"])

    def test_completed_quit_wrong_correspondence_or_unreaped_child_refuses(self):
        for target, mutation in (
            ("decision", lambda v: v.update(action=["hotkey", "--key", "CmdOrCtrl+S"])),
            ("decision", lambda v: v.update(request_sha256="0" * 64)),
            ("request", lambda v: v.update(stage="action")),
            ("request", lambda v: v.update(job="other-job")),
            ("response", lambda v: v["result"]["snapshot"]["app"].update(pid=99)),
            ("response", lambda v: v["result"]["action"].update(targetWindowId=99)),
            ("response", lambda v: v["_meta"].update(runtimeId="other-runtime")),
            ("response", lambda v: v.update(ok=False)),
            ("exited", lambda v: v.update(reaped=False)),
            ("exited", lambda v: v.update(owned_wait_returncode=1)),
        ):
            with self.subTest(target=target):
                self.completed_quit()
                row = self.plan["completion"][target]
                value = decode(read_plain(row["path"]))
                mutation(value)
                self.plan["completion"][target] = dump(Path(row["path"]), value)
                if target == "response":
                    self.plan["completion"]["expected_response"] = value
                self.seal()
                self.assert_refuses("completed")

    def test_transaction_restores_exact_return_preferences_keeps_diagnostic_and_unknown(self):
        original = read_plain(self.root / "outcome.json")
        result = self.operation().run()
        self.assertEqual(tree_snapshot(self.live), self.plan["baseline"]["live"])
        self.assertEqual(
            tree_snapshot(self.manager._profile_path(self.diagnostic)),
            self.plan["diagnostic_snapshot"],
        )
        self.assertEqual(
            (self.manager._profile_path(self.diagnostic) / "Saves/future.sav").read_bytes(),
            b"retain future save",
        )
        self.assertEqual(read_plain(self.root / "outcome.json"), original)
        self.assertTrue(decode(read_plain(self.root / "exits.json"))["uncertain_input"])
        for row in self.plan["preferences"]:
            self.assertEqual(file_snapshot(row["path"]), row["baseline"])
        receipt = decode(read_plain(result))
        self.assertFalse(receipt["lease_released"])
        self.assertFalse(receipt["original_run_restored"])
        self.assertFalse(receipt["automatic_successor"])
        with self.assertRaisesRegex(ControlRefused, "One-use"):
            self.operation()

    def test_live_unknown_and_foreign_game_refuse(self):
        for running, games in ((True, []), (None, []), (False, [object()])):
            with self.subTest(running=running, games=bool(games)):
                self.running, self.games = running, games
                self.assert_refuses("No live or foreign game")

    def test_owned_input_still_present_refuses(self):
        self.present.add(11)
        self.assert_refuses("still present")

    def test_stale_owner_or_separate_approval_refuses(self):
        dump(self.state_path, {"owner": "foreign", "restoration_maintenance": self.approval})
        self.assert_refuses("Stale owner")
        dump(self.state_path, {"owner": "owner", "restoration_maintenance": None})
        self.assert_refuses("ownership changed")

    def test_missing_or_replaced_baseline_refuses(self):
        stored = self.manager._profile_path(self.return_id) / "Saves/original.sav"
        stored.write_bytes(b"replacement")
        self.assert_refuses("Return profile changed")
        stored.unlink()
        self.assert_refuses("Return profile changed")

    def test_pending_activation_or_removal_refuses(self):
        self.manager.journal.write_text("{}")
        self.assert_refuses("Pending activation")
        self.manager.journal.unlink()
        (self.root / "removal.json").write_text("{}")
        self.assert_refuses("Pending removal")

    def test_incomplete_or_replaced_provider_response_refuses(self):
        Path(self.plan["completion"]["response"]["path"]).write_bytes(b'{"ok":false')
        self.assert_refuses("Maintenance input changed")

    def test_cleared_original_uncertainty_refuses(self):
        exits = decode(read_plain(self.root / "exits.json"))
        exits["uncertain_input"] = False
        self.plan["positive_exits"] = dump(self.root / "exits.json", exits)
        self.plan["evidence"] = [
            row for row in self.plan["evidence"] if row["path"] != str(self.root / "exits.json")
        ]
        self.seal()
        self.assert_refuses("failed UNKNOWN")

    def test_expired_separate_approval_refuses(self):
        self.now = 390
        self.assert_refuses("Separate exact Craig")

    def test_preference_drift_refuses_before_profile_exchange(self):
        Path(self.plan["preferences"][0]["path"]).write_bytes(b"foreign change")
        self.assert_refuses("Preference correspondence")

    def test_global_preference_bytes_refuse_before_profile_exchange(self):
        path = Path(self.plan["preferences"][1]["path"])
        path.write_bytes(b"different global bytes")
        self.plan["preferences"][1]["current"] = file_snapshot(path)
        self.seal()
        self.assert_refuses("Unexpected global preference")

    def test_approval_replaced_after_construction_refuses(self):
        operation = self.operation()
        self.approval_path.write_bytes(b"{}")
        before = tree_snapshot(self.live)
        with self.assertRaisesRegex(ControlRefused, "plan, approval or candidate changed"):
            operation.run()
        self.assertEqual(tree_snapshot(self.live), before)

    def test_expiry_after_construction_refuses(self):
        operation = self.operation()
        self.now = 390
        with self.assertRaisesRegex(ControlRefused, "deadline expired"):
            operation.run()
        self.assertEqual(self.manager._state()["active"], self.diagnostic)

    def test_expiry_during_guard_preservation_prevents_exchange(self):
        operation = self.operation()
        original = operation.preservation
        calls = []

        def slow_preservation(*, restored):
            original(restored=restored)
            calls.append(restored)
            if len(calls) == 3:
                self.now = 390

        operation.preservation = slow_preservation
        with self.assertRaisesRegex(ControlRefused, "deadline expired"):
            operation.run()
        self.assertEqual(self.manager._state()["active"], self.diagnostic)
        self.assertFalse(self.manager.journal.exists())

    def test_expiry_after_journal_prevents_exchange_and_retains_recovery_hold(self):
        operation = self.operation()
        before = tree_snapshot(self.live)

        def expire(event):
            if event == "after_journal":
                self.now = 390

        self.manager.fault_hook = expire
        with self.assertRaisesRegex(ControlRefused, "deadline expired"):
            operation.run()
        self.assertEqual(tree_snapshot(self.live), before)
        self.assertEqual(self.manager._state()["active"], self.diagnostic)
        self.assertTrue(self.manager.journal.exists())
        self.assertIsNone(self.manager.observed_io)
        with state_lock(self.state_path):
            pass

    def test_production_imports_select_task_checkout_without_pythonpath(self):
        code = (
            "import sys; sys.path.insert(0, " + repr(str(ROOT / "scripts/research")) + "); "
            "import restoration_maintenance, smr_launcher.application, smr_launcher.activation; "
            "print(smr_launcher.application.__file__); print(smr_launcher.activation.__file__)"
        )
        environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        self.assertEqual(
            result.stdout.splitlines(),
            [
                str(ROOT / "src/smr_launcher/application.py"),
                str(ROOT / "src/smr_launcher/activation.py"),
            ],
        )

    def test_expiry_after_lock_open_transfers_descriptor_for_backend_cleanup(self):
        operation = self.operation()
        original = self.manager._call
        opened = []

        def late_open(name, function, *args, **kwargs):
            if name == "activation.lock_open":

                def opening(*values, **options):
                    fd = function(*values, **options)
                    opened.append(fd)
                    self.now = 390
                    return fd

                return original(name, opening, *args, **kwargs)
            return original(name, function, *args, **kwargs)

        self.manager._call = late_open
        with self.assertRaisesRegex(ControlRefused, "deadline expired"):
            operation.run()
        self.assertTrue(opened)
        for fd in opened:
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertFalse(self.manager.journal.exists())
        self.assertEqual(self.manager._state()["active"], self.diagnostic)

    def test_preloaded_foreign_module_refuses_before_discovery(self):
        closure = [
            {"path": str(Path(m.__file__).resolve())}
            for m in tuple(sys.modules.values())
            if getattr(m, "__file__", None)
        ]
        sys.modules["smr_launcher.foreign"] = SimpleNamespace(
            __file__=str(self.root / "foreign.py")
        )
        try:
            with self.assertRaisesRegex(ControlRefused, "Imported maintained source outside"):
                verify_imported_sources({"closure": closure})
        finally:
            del sys.modules["smr_launcher.foreign"]


if __name__ == "__main__":
    unittest.main()
