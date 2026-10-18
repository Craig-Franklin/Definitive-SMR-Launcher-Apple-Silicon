"""Closed packaged menu controls; temporary catalogue, no installed game input."""

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import tarfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/research")]
from test_agent_driver import ChildFailed, Completion, Process
import test_agent_driver as route_fixtures
import catalogue_menu as menu
import agent_driver as driver
import test_application
from active_job_control import ControlRefused
from smr_launcher.launch_preferences import prepare_settings


class CatalogueRestoreTests(unittest.TestCase):
    def setUp(self):
        fixture = test_application.ApplicationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        app = fixture.application
        app.setup()
        returned = app.import_archive(fixture.archive)
        archive = fixture.root / "Second.tar"
        with tarfile.open(archive, "w") as stream:
            data = b"<Scenario/>"
            entry = tarfile.TarInfo("UserMaps/Second/RRT_Scenario_User_Second.xml")
            entry.size = len(data)
            stream.addfile(entry, BytesIO(data))
        target = app.import_archive(archive)
        app.activate(returned.profile_id)
        p = app.profiles
        (p.live / "Saves/personal.sav").write_bytes(b"return save")
        target_root = p._profile_path(target.profile_id)
        (target_root / "Settings.ini").write_bytes(b"[User Settings]\nQuickstart = 1\n")
        (target_root / "Saves/existing.sav").write_bytes(b"existing target save")
        preferences = [fixture.root / "Preferences Data", fixture.root / "global.plist"]
        for path in preferences:
            path.write_bytes(b"original preferences")
        directory = fixture.root / "evidence"
        directory.mkdir()
        baseline = dict(
            return_files=menu.tree_files(p.live),
            target_files=menu.tree_files(target_root),
            immutable={str(app.catalogue_file): menu.file_facts(app.catalogue_file)},
        )
        baseline["sibling_saves"] = menu.sibling_saves(app, target.profile_id, returned.profile_id)
        preimages = []
        for role, path in zip(
            ("settings", "feral", "global"), [target_root / "Settings.ini", *preferences]
        ):
            copy = directory / (role + "-before")
            copy.write_bytes(path.read_bytes())
            preimages.append(
                dict(role=role, path=str(path), copy=str(copy), **menu.file_facts(path))
            )
        self.state_held = False

        @contextmanager
        def cleanup():
            if self.state_held:
                raise ValueError("Recursive STATE lock")
            self.state_held = True
            try:
                yield
            finally:
                self.state_held = False

        def stopped():
            if fixture.installation.running:
                raise ValueError("Game present")

        self.receipts = {}
        self.owner = SimpleNamespace(
            app=app,
            profiles=p,
            job=dict(return_profile=returned.profile_id),
            config=dict(
                target=dict(profile_id=target.profile_id, assets_sha256=p._asset_hash(target_root))
            ),
            baseline=baseline,
            directory=directory,
            preimages=preimages,
            service=None,
            dispatch_settled=True,
            open_attempted=True,
            anchor=time.monotonic(),
            control=SimpleNamespace(cleanup_guard=cleanup),
            require_stopped=stopped,
            verify_cleanup_owner=lambda: None,
            require_settled_input=lambda: None,
            write=lambda name, value: self.receipts.setdefault(name, value),
            journal=SimpleNamespace(append=lambda *args: None),
        )
        self.target = target
        app.activate(target.profile_id)
        (p.live / "Settings.ini").write_bytes(
            prepare_settings(Path(preimages[0]["copy"]).read_bytes(), target.scenarios)
        )
        for path in preferences:
            path.write_bytes(b"changed preferences")
        (p.live / "Saves/new.sav").write_bytes(b"new save remains")
        (p.live / "new.log").write_bytes(b"new file remains")

    def test_actual_switch_restores_three_files_preserves_both_save_sets_and_new_files(self):
        self.assertTrue(menu.restore_catalogue(self.owner))
        p = self.owner.profiles
        self.assertEqual(p._state()["active"], self.owner.job["return_profile"])
        self.assertEqual(menu.tree_files(p.live), self.owner.baseline["return_files"])
        target = p._profile_path(self.target.profile_id)
        self.assertEqual((target / "Saves/new.sav").read_bytes(), b"new save remains")
        self.assertEqual((target / "new.log").read_bytes(), b"new file remains")
        for row in self.owner.preimages:
            path = target / "Settings.ini" if row["role"] == "settings" else Path(row["path"])
            actual = menu.file_facts(path)
            for key in ("sha256", "mode", "mtime_ns"):
                self.assertEqual(actual[key], row[key])
        self.assertEqual(len(list(self.owner.directory.glob("mutable-post-*"))), 3)
        self.assertFalse(self.state_held)

    def assert_refusal_preserves(self, message):
        before = menu.tree_files(self.owner.profiles.live)
        with self.assertRaisesRegex(ControlRefused, message):
            menu.restore_catalogue(self.owner)
        self.assertEqual(menu.tree_files(self.owner.profiles.live), before)
        self.assertFalse(list(self.owner.directory.glob("mutable-post-*")))

    def test_unknown_dispatch_holds(self):
        self.owner.dispatch_settled = False
        self.assert_refusal_preserves("Unsettled packaged")

    def test_changed_old_save_holds_without_overwrite(self):
        (self.owner.profiles.live / "Saves/existing.sav").write_bytes(b"personal change")
        self.assert_refusal_preserves("Existing target file changed")

    def test_changed_return_and_assets_refuse(self):
        path = (
            self.owner.profiles._profile_path(self.owner.job["return_profile"])
            / "Saves/personal.sav"
        )
        path.write_bytes(b"changed return")
        self.assert_refusal_preserves("Return profile changed")

    def test_foreign_active_refuses(self):
        self.owner.app.activate("original-game")
        self.assert_refusal_preserves("Foreign active")

    def owned_autosave(self):
        path = self.owner.profiles.live / menu.OWNED_AUTOSAVE
        path.write_bytes(b"prior owned autosave")
        facts = menu.file_facts(path)
        self.owner.baseline["target_files"][menu.OWNED_AUTOSAVE] = facts
        self.owner.job["purpose"] = menu.SAVE_PURPOSE
        self.owner.job["catalogue_menu"] = self.owner.config
        origin = self.owner.directory / "prior-root-readback.json"
        origin.write_bytes(
            menu.canonical(
                dict(
                    lease_released=True,
                    preservation_verified=True,
                    terminal_finished=True,
                    game_absent=True,
                    invocation=dict(returncode=0),
                    service_settled=dict(
                        reaped=True,
                        commit_response=dict(data=dict(launched_profile_id=self.target.profile_id)),
                    ),
                    new_target_files_retained={menu.OWNED_AUTOSAVE: facts},
                )
            )
        )
        self.owner.config.update(
            preservation=self.owner.baseline,
            owned_autosave=dict(
                origin_readback=str(origin),
                origin_sha256=menu.sha(origin.read_bytes()),
                facts=facts,
            ),
        )
        copy = menu.preserve_owned_autosave(self.owner, self.owner.profiles.live)
        self.owner.autosave_preimage = copy
        path.write_bytes(b"new game autosave")
        return copy

    def test_owned_autosave_rotation_retains_old_copy_and_new_save_without_overwrite(self):
        copy = self.owned_autosave()
        self.assertTrue(menu.restore_catalogue(self.owner))
        self.assertEqual(copy.read_bytes(), b"prior owned autosave")
        target = self.owner.profiles._profile_path(self.target.profile_id)
        self.assertEqual((target / menu.OWNED_AUTOSAVE).read_bytes(), b"new game autosave")
        self.assertEqual((target / "Saves/existing.sav").read_bytes(), b"existing target save")
        self.assertEqual((target / "Saves/new.sav").read_bytes(), b"new save remains")

    def test_owned_autosave_exception_never_allows_changed_named_save(self):
        self.owned_autosave()
        (self.owner.profiles.live / "Saves/existing.sav").write_bytes(b"changed user save")
        self.assert_refusal_preserves("Existing target file changed")

    def partial_autosave_origin(self):
        self.owned_autosave()
        proof = self.owner.config["owned_autosave"]
        path = Path(proof["origin_readback"])
        origin = menu.decode(path.read_bytes())
        first = origin["service_settled"]
        first.update(commit_sent=True, returncode=0, pending=None, eof_closed=True)
        first["commit_response"]["ok"] = True
        origin.update(
            invocation=dict(returncode=2),
            service_settled=[
                first,
                dict(
                    commit_sent=False,
                    commit_response=None,
                    reaped=True,
                    returncode=0,
                    pending=None,
                    eof_closed=True,
                ),
            ],
            exact_normal_exit=dict(
                result=dict(state="observed_exited", stop_requested=False, terminal=True)
            ),
            current_test_autosave=proof["facts"],
            new_target_files_retained={},
        )
        path.write_bytes(menu.canonical(origin))
        proof["origin_sha256"] = menu.sha(path.read_bytes())
        return path, origin

    def test_settled_partial_origin_preserves_named_save_and_both_autosave_versions(self):
        self.partial_autosave_origin()
        self.assertEqual(menu.autosave_origin(self.owner.job), self.owner.config["owned_autosave"])
        self.assertTrue(menu.restore_catalogue(self.owner))
        target = self.owner.profiles._profile_path(self.target.profile_id)
        self.assertEqual((target / "Saves/existing.sav").read_bytes(), b"existing target save")
        self.assertEqual((target / menu.OWNED_AUTOSAVE).read_bytes(), b"new game autosave")
        self.assertEqual(self.owner.autosave_preimage.read_bytes(), b"prior owned autosave")

    def test_second_committed_owned_stop_can_supply_autosave_without_normal_quit_credit(self):
        path, origin = self.partial_autosave_origin()
        origin["job"] = "synthetic-owned-job"
        origin["exact_normal_exit"]["process"] = dict(pid=100)
        origin["service_settled"][1].update(
            commit_sent=True,
            commit_response=dict(ok=True, data=dict(launched_profile_id=self.target.profile_id)),
        )
        origin["second_observer"] = dict(
            job=origin["job"],
            process=dict(pid=200),
            result=dict(state="observed_exited", terminal=True, stop_requested=True),
        )
        origin["owned_absences"] = [dict(pid=200, role="game", positive_absence=True)]
        for cause in (
            None,
            "unknown-response",
            "wrong-target",
            "wrong-job",
            "wrong-pid",
            "not-absent",
        ):
            with self.subTest(cause=cause):
                value = menu.decode(menu.canonical(origin))
                if cause == "unknown-response":
                    value["service_settled"][1]["commit_response"] = None
                elif cause == "wrong-target":
                    value["service_settled"][1]["commit_response"]["data"][
                        "launched_profile_id"
                    ] = "foreign"
                elif cause == "wrong-job":
                    value["second_observer"]["job"] = "foreign"
                elif cause == "wrong-pid":
                    value["second_observer"]["process"]["pid"] = 100
                elif cause == "not-absent":
                    value["owned_absences"][0]["positive_absence"] = False
                path.write_bytes(menu.canonical(value))
                self.owner.config["owned_autosave"]["origin_sha256"] = menu.sha(path.read_bytes())
                if cause:
                    with self.assertRaisesRegex(ControlRefused, "settled owned trial"):
                        menu.autosave_origin(self.owner.job)
                else:
                    self.assertEqual(
                        menu.autosave_origin(self.owner.job), self.owner.config["owned_autosave"]
                    )
                    self.assertEqual(value["invocation"]["returncode"], 2)
                    self.assertTrue(value["second_observer"]["result"]["stop_requested"])

    def test_partial_origin_requires_settled_services_no_second_commit_and_normal_exit(self):
        path, original = self.partial_autosave_origin()
        for cause in (
            "lease",
            "pending",
            "eof",
            "reap",
            "second-commit",
            "stop",
            "facts",
            "target",
        ):
            with self.subTest(cause=cause):
                origin = menu.decode(menu.canonical(original))
                if cause == "lease":
                    origin["lease_released"] = False
                elif cause == "pending":
                    origin["service_settled"][1]["pending"] = "unsettled"
                elif cause == "eof":
                    origin["service_settled"][1]["eof_closed"] = False
                elif cause == "reap":
                    origin["service_settled"][0]["reaped"] = False
                elif cause == "second-commit":
                    origin["service_settled"][1]["commit_sent"] = True
                elif cause == "stop":
                    origin["exact_normal_exit"]["result"]["stop_requested"] = True
                elif cause == "facts":
                    origin["current_test_autosave"] = dict(origin["current_test_autosave"], bytes=1)
                else:
                    origin["service_settled"][0]["commit_response"]["data"][
                        "launched_profile_id"
                    ] = "foreign"
                path.write_bytes(menu.canonical(origin))
                self.owner.config["owned_autosave"]["origin_sha256"] = menu.sha(path.read_bytes())
                with self.assertRaisesRegex(ControlRefused, "settled owned trial"):
                    menu.autosave_origin(self.owner.job)

    def successful_autosave_origin(self):
        job = self.save_config(empty=True)
        job["game_sha256"] = "e" * 64
        root = self.owner.profiles.live
        (root / menu.OWNED_AUTOSAVE).write_bytes(b"owned successful-trial autosave")
        (root / "Saves/personal.sav").write_bytes(b"strict prior named save")
        self.owner.baseline["target_files"].update(
            {"Saves/" + name: facts for name, facts in menu.tree_files(root / "Saves").items()}
        )
        facts = menu.file_facts(root / menu.OWNED_AUTOSAVE)
        expected = {
            key: self.owner.config["selection"][key]
            for key in ("profile_id", "archive_sha256", "variant_id")
        }
        expected["game_executable_sha256"] = job["game_sha256"]

        def service(pid):
            return dict(
                pid=pid,
                commit_sent=True,
                eof_closed=True,
                reaped=True,
                returncode=0,
                pending=None,
                commit_response=dict(
                    ok=True, data=dict(target=expected, launched_profile_id=self.target.profile_id)
                ),
            )

        def exited(pid):
            return dict(
                job="prior-successful-job",
                process=dict(pid=pid, birth=str(pid), executable="/synthetic/game"),
                result=dict(state="observed_exited", stop_requested=False, terminal=True),
            )

        origin = dict(
            job="prior-successful-job",
            invocation=dict(returncode=0),
            lease_released=True,
            preservation_verified=True,
            terminal_finished=True,
            game_absent=True,
            both_normal_quits_verified=True,
            service_settled=[service(300), service(400)],
            exact_normal_exit=exited(100),
            second_observer=exited(200),
            owned_absences=[
                dict(pid=pid, role=role, positive_absence=True)
                for pid, role in (
                    (100, "game"),
                    (200, "game"),
                    (300, "packaged_service"),
                    (400, "packaged_service"),
                )
            ],
            current_test_autosave=facts,
        )
        path = self.owner.directory / "successful-root-readback.json"
        path.write_bytes(menu.canonical(origin))
        self.owner.config["owned_autosave"] = dict(
            origin_readback=str(path), origin_sha256=menu.sha(path.read_bytes()), facts=facts
        )
        return path, origin

    def test_successful_two_service_origin_reaches_real_config_and_preserves_rotation(self):
        self.successful_autosave_origin()
        self.assertIs(menu.verify_config(self.owner.job), self.owner.config)
        root = self.owner.profiles.live
        copy = menu.preserve_owned_autosave(self.owner, root)
        self.owner.autosave_preimage = copy
        (root / menu.OWNED_AUTOSAVE).write_bytes(b"next owned autosave")
        self.assertTrue(menu.restore_catalogue(self.owner))
        target = self.owner.profiles._profile_path(self.target.profile_id)
        self.assertEqual(copy.read_bytes(), b"owned successful-trial autosave")
        self.assertEqual((target / menu.OWNED_AUTOSAVE).read_bytes(), b"next owned autosave")
        self.assertEqual((target / "Saves/personal.sav").read_bytes(), b"strict prior named save")

    def test_successful_origin_requires_each_exact_success_proof(self):
        path, original = self.successful_autosave_origin()
        changes = [
            (("invocation", "returncode"), 1),
            (("job",), "foreign-job"),
            (("both_normal_quits_verified",), None),
            *(
                (
                    ((key,), None)
                    for key in (
                        "lease_released",
                        "preservation_verified",
                        "terminal_finished",
                        "game_absent",
                    )
                )
            ),
            (("current_test_autosave", "inode"), 1),
            (("current_test_autosave",), None),
            (("owned_absences",), []),
            (("service_settled", 1, "pid"), 300),
        ]
        for index in (0, 1):
            changes.extend(
                [
                    (("service_settled", index, "commit_sent"), False),
                    (("service_settled", index, "eof_closed"), False),
                    (("service_settled", index, "reaped"), False),
                    (("service_settled", index, "returncode"), 1),
                    (("service_settled", index, "pending"), "pending"),
                    (("service_settled", index, "pending"), None),
                    (("service_settled", index, "commit_response"), None),
                    (("service_settled", index, "commit_response", "ok"), False),
                    (
                        (
                            "service_settled",
                            index,
                            "commit_response",
                            "data",
                            "launched_profile_id",
                        ),
                        "foreign",
                    ),
                ]
            )
            for key in ("profile_id", "archive_sha256", "variant_id", "game_executable_sha256"):
                changes.append(
                    (
                        ("service_settled", index, "commit_response", "data", "target", key),
                        "foreign",
                    )
                )
        for index in range(4):
            changes.append((("owned_absences", index, "positive_absence"), False))
            changes.append((("owned_absences", index, "role"), "foreign"))
        for key in ("exact_normal_exit", "second_observer"):
            changes.extend(
                [
                    ((key,), None),
                    ((key, "job"), "foreign-job"),
                    ((key, "process", "pid"), 100 if key == "second_observer" else 200),
                    ((key, "process", "birth"), "unknown"),
                    ((key, "process", "executable"), "/foreign/game"),
                    ((key, "result", "state"), "unknown"),
                    ((key, "result", "terminal"), False),
                    ((key, "result", "stop_requested"), True),
                ]
            )
        before = menu.tree_files(self.owner.profiles.live)
        for keys, replacement in changes:
            with self.subTest(keys=keys):
                value = menu.decode(menu.canonical(original))
                node = value
                for key in keys[:-1]:
                    node = node[key]
                if replacement is None:
                    node.pop(keys[-1])
                else:
                    node[keys[-1]] = replacement
                path.write_bytes(menu.canonical(value))
                self.owner.config["owned_autosave"]["origin_sha256"] = menu.sha(path.read_bytes())
                with self.assertRaises(ControlRefused):
                    menu.verify_config(self.owner.job)
                self.assertEqual(menu.tree_files(self.owner.profiles.live), before)

    def test_successful_origin_never_allows_changed_current_autosave(self):
        self.successful_autosave_origin()
        (self.owner.profiles.live / menu.OWNED_AUTOSAVE).write_bytes(b"foreign autosave")
        with self.assertRaisesRegex(ControlRefused, "changed before preservation"):
            menu.preserve_owned_autosave(self.owner, self.owner.profiles.live)
        self.assertFalse((self.owner.directory / "owned-Auto0-preimage").exists())

    def test_successful_origin_keeps_named_save_and_pretrial_slot_restrictions(self):
        self.successful_autosave_origin()
        facts = self.owner.config["owned_autosave"]["facts"]
        for index in range(1, 5):
            name = f"Saves/Auto{index}.SMRailroadsSavedGame"
            self.owner.baseline["target_files"][name] = facts
            with self.assertRaisesRegex(ControlRefused, "pretrial Auto1..4 refuse"):
                menu.verify_config(self.owner.job)
            del self.owner.baseline["target_files"][name]
        self.owner.autosave_preimage = menu.preserve_owned_autosave(
            self.owner, self.owner.profiles.live
        )
        (self.owner.profiles.live / "Saves/personal.sav").write_bytes(b"changed named save")
        self.assert_refusal_preserves("Existing target file changed")

    def save_config(self, *, empty=False):
        if empty:
            self.owner.job.update(purpose=menu.SAVE_PURPOSE, catalogue_menu=self.owner.config)
            self.owner.config["preservation"] = self.owner.baseline
            for path in (self.owner.profiles.live / "Saves").iterdir():
                path.unlink()
            self.owner.baseline["target_files"] = {
                name: facts
                for name, facts in self.owner.baseline["target_files"].items()
                if not name.startswith("Saves/")
            }
        else:
            self.owned_autosave()
        bundle = self.owner.directory / "Synthetic.app"
        helper = bundle / "Contents/MacOS/Definitive SMR Launcher Apple Silicon"
        helper.parent.mkdir(parents=True)
        helper.write_bytes(b"synthetic helper, never executed")
        receipt = self.owner.directory / "artifact.json"
        receipt.write_bytes(
            menu.canonical(
                dict(
                    source_commit="synthetic",
                    tag="synthetic",
                    zip_sha256="a" * 64,
                    codesign_verified=True,
                    gatekeeper_accepted=True,
                )
            )
        )
        target = dict(
            profile_id=self.target.profile_id,
            variant_id=self.target.profile_id[4:],
            archive_sha256="a" * 64,
            assets_sha256=self.owner.profiles._asset_hash(self.owner.profiles.live),
            scenarios=["synthetic.xml"],
        )
        self.owner.config.update(
            target=target,
            scenario_title="Synthetic Canyon",
            prepare_compatibility=True,
            warning="Experimental",
            selection=dict(
                profile_id="map-" + "c" * 64,
                variant_id="c" * 64,
                archive_sha256="a" * 64,
                scenarios=target["scenarios"],
            ),
            preparation=dict(cached_profile_id=target["profile_id"]),
            package=dict(
                bundle=str(bundle),
                executable=str(helper),
                executable_sha256=menu.sha(helper.read_bytes()),
                receipt_path=str(receipt),
                receipt_sha256=menu.sha(receipt.read_bytes()),
                source_commit="synthetic",
                tag="synthetic",
                zip_sha256="a" * 64,
            ),
        )
        self.owner.job["source_original"] = dict(
            archive_sha256=target["archive_sha256"],
            variant_id=target["variant_id"],
            scenario="synthetic.xml",
        )
        return self.owner.job

    def test_save_config_admits_strict_named_files_with_proven_autosave(self):
        self.assertIs(menu.verify_config(self.save_config()), self.owner.config)

    def entry_contract(self, purpose):
        """Exercise the complete entry consumer with temporary package evidence."""
        job = self.save_config(empty=True)
        common = self.owner.directory / "common"
        (common / "stop-helper").mkdir(parents=True)
        for name in ("agent_calibration.py", "stop-helper/agent_host.py", "source-manifest.json"):
            (common / name).write_bytes(
                b'{"files": []}' if name.endswith(".json") else b"synthetic"
            )
        authority = self.owner.directory / "authority.txt"
        authority.write_bytes(b"synthetic finite authority")
        actor = dict(session="synthetic", terminal="synthetic", cwd=str(Path.cwd()))
        root = dict(run="run_9c111880b18c")
        job.update(
            purpose=purpose,
            common_root=str(common),
            authority_path=str(authority),
            authority_sha256=driver.sha(authority.read_bytes()),
            actor_binding=dict(driver_actor=actor),
            root=root,
            queue_module=str(common / "queue.py"),
            native_source=str(common / "native.swift"),
            source_manifest_sha256=driver.sha((common / "source-manifest.json").read_bytes()),
        )
        for key in ("queue_module", "native_source"):
            Path(job[key]).write_bytes(b"synthetic")
        paths = set((ROOT / "src").rglob("*.py"))
        paths.update((ROOT / "scripts/research").rglob("*.py"))
        paths.update(p for p in common.rglob("*") if p.is_file())
        job["closure"] = [
            dict(path=str(p), bytes=p.stat().st_size, sha256=driver.sha(p.read_bytes()))
            for p in sorted(paths)
        ]
        decision, review_decision = driver.admission_decisions(purpose)
        review = self.owner.directory / "entry-review.json"
        review.write_bytes(
            menu.canonical(
                dict(
                    decision=review_decision,
                    job_sha256="a" * 64,
                    closure=job["closure"],
                    findings=[],
                )
            )
        )
        now = time.time()
        admission = dict(
            decision=decision,
            job_sha256="a" * 64,
            runtime_owner=actor["terminal"],
            root=root,
            issued_epoch=now - 1,
            expires_epoch=now + 60,
            review=dict(path=str(review), sha256=driver.sha(review.read_bytes())),
        )
        self.enterContext(
            patch.dict(
                driver.os.environ,
                {
                    "CODEX_THREAD_ID": actor["session"],
                    "ORCA_TERMINAL_HANDLE": actor["terminal"],
                },
            )
        )
        self.enterContext(
            patch.object(
                driver,
                "LAUNCHER_CALIBRATION_SHA256",
                driver.sha((common / "agent_calibration.py").read_bytes()),
            )
        )
        return job, admission

    def gameplay_config(self, job=None):
        job = self.save_config(empty=True) if job is None else job
        job["purpose"] = menu.GAMEPLAY_PURPOSE
        job["game_sha256"] = "e" * 64
        config = job["catalogue_menu"]
        token = "GameTest" + "1" * 11
        filename = token + ".SMRailroadsSavedGame"
        saves = self.owner.profiles.live / "Saves"
        (saves / filename).write_bytes(b"retained exact test world")
        autosaves = {}
        for index in (0, 1):
            name = f"Auto{index}.SMRailroadsSavedGame"
            (saves / name).write_bytes(("owned test autosave " + str(index)).encode())
            autosaves[name] = menu.file_facts(saves / name)
        facts = menu.file_facts(saves / filename)
        expected = {
            key: config["selection"][key] for key in ("profile_id", "archive_sha256", "variant_id")
        }
        expected["game_executable_sha256"] = job["game_sha256"]
        service = dict(
            commit_sent=True,
            eof_closed=True,
            reaped=True,
            returncode=0,
            pending=None,
            commit_response=dict(
                ok=True,
                data=dict(target=expected, launched_profile_id=config["target"]["profile_id"]),
            ),
        )

        def exited(pid):
            return dict(
                job="prior-owned-trial",
                process=dict(pid=pid, birth=str(pid), executable="/synthetic/game"),
                result=dict(state="observed_exited", terminal=True, stop_requested=False),
            )

        origin = dict(
            job="prior-owned-trial",
            invocation=dict(returncode=0),
            lease_released=True,
            preservation_verified=True,
            terminal_finished=True,
            game_absent=True,
            first_normal_exit=True,
            final_normal_exit=True,
            service_settled=[service, service],
            exact_normal_exit=exited(100),
            second_observer=exited(200),
            owned_absences=[
                dict(pid=pid, role="game", positive_absence=True) for pid in (100, 200)
            ],
            exact_new_named_save=facts,
            current_test_autosave=autosaves["Auto0.SMRailroadsSavedGame"],
            new_target_files_retained={
                "Saves/" + filename: facts,
                "Saves/Auto1.SMRailroadsSavedGame": autosaves["Auto1.SMRailroadsSavedGame"],
            },
        )
        path = self.owner.directory / "gameplay-origin.json"
        path.write_bytes(menu.canonical(origin))
        config["preservation"]["target_files"] = menu.tree_files(self.owner.profiles.live)
        config["preservation"]["immutable"][str(path)] = menu.file_facts(path)
        config["resume_save"] = dict(
            token=token,
            filename=filename,
            facts=facts,
            autosaves=autosaves,
            origin_readback=str(path),
            origin_sha256=menu.sha(path.read_bytes()),
        )
        return job

    def gameplay_after_owned_trial(self, job):
        proof = job["catalogue_menu"]["resume_save"]
        original = menu.decode(Path(proof["origin_readback"]).read_bytes())
        name = "Auto0.SMRailroadsSavedGame"
        (self.owner.profiles.live / "Saves" / name).write_bytes(b"rotated by owned gameplay")
        proof["autosaves"][name] = menu.file_facts(self.owner.profiles.live / "Saves" / name)
        job["catalogue_menu"]["preservation"]["target_files"] = menu.tree_files(
            self.owner.profiles.live
        )
        service = dict(original["service_settled"][0], pid=300)
        exited = menu.decode(menu.canonical(original["exact_normal_exit"]))
        exited["job"] = "later-owned-gameplay"
        recent = dict(
            job=exited["job"],
            invocation=dict(returncode=0),
            normal_exit=True,
            lease_released=True,
            preservation_verified=True,
            terminal_finished=True,
            game_absent=True,
            service_settled=service,
            exact_normal_exit=exited,
            exact_retained_named_save=proof["facts"],
            current_test_autosaves=proof["autosaves"],
            new_target_files_retained={},
            owned_absences=[
                dict(pid=100, role="game", positive_absence=True),
                dict(pid=300, role="packaged_service", positive_absence=True),
            ],
        )
        path = self.owner.directory / "later-gameplay-root.json"
        path.write_bytes(menu.canonical(recent))
        proof["autosave_origin"] = dict(path=str(path), sha256=menu.sha(path.read_bytes()))
        job["catalogue_menu"]["preservation"]["immutable"][str(path)] = menu.file_facts(path)
        return path, recent

    def test_actual_gameplay_prepare_keeps_both_origin_files_in_baseline(self):
        # Real temporary application/profile preparation through the first copy.
        # Only actor provenance, signature subprocesses and outer owner guards
        # are synthetic; no installed application or data is used.
        from smr_launcher import editions

        app, profiles = self.owner.app, self.owner.profiles
        original = next(
            row for row in app.catalogue() if row.profile_id == self.owner.job["return_profile"]
        )
        returned = self.target.profile_id
        with patch.object(editions, "SUPPORTED_GAME", app.installation.executable_sha256):
            target = app.create_edition(original, editor=False, difficulty=True)
        self.target = target
        self.owner.job["return_profile"] = returned
        app.activate(target.profile_id)
        job = self.gameplay_config()
        later, recent = self.gameplay_after_owned_trial(job)
        profiles.switch(job["return_profile"])
        root = profiles._profile_path(target.profile_id)
        (root / "Logs").mkdir(exist_ok=True)
        config = job["catalogue_menu"]
        config["target"].update(
            archive_sha256=target.archive_sha256,
            variant_id=target.variant_id,
            scenarios=list(target.scenarios),
            assets_sha256=profiles._asset_hash(root),
        )
        config["selection"].update(
            profile_id=original.profile_id,
            archive_sha256=original.archive_sha256,
            variant_id=original.variant_id,
            scenarios=list(original.scenarios),
        )
        job["game_sha256"] = app.installation.executable_sha256
        job["actor_binding"] = {}
        expected = {
            k: config["selection"][k] for k in ("profile_id", "archive_sha256", "variant_id")
        }
        expected["game_executable_sha256"] = job["game_sha256"]
        proof = config["resume_save"]
        first = Path(proof["origin_readback"])
        origin = menu.decode(first.read_bytes())
        for service in origin["service_settled"]:
            service["commit_response"]["data"].update(
                target=expected, launched_profile_id=target.profile_id
            )
        first.write_bytes(menu.canonical(origin))
        proof["origin_sha256"] = menu.sha(first.read_bytes())
        recent["service_settled"]["commit_response"]["data"].update(
            target=expected, launched_profile_id=target.profile_id
        )
        later.write_bytes(menu.canonical(recent))
        proof["autosave_origin"]["sha256"] = menu.sha(later.read_bytes())
        immutable = [
            app.catalogue_file,
            app.originals / (target.archive_sha256 + ".7z"),
            first,
            later,
        ]
        for base in (
            profiles._profile_path(original.profile_id),
            app.prepared / original.prepared_directory,
            app.imports / target.imported_directory,
            app.prepared / target.prepared_directory,
            profiles._profile_path("original-game"),
        ):
            immutable.extend(base / name for name in menu.tree_files(base))
        config["preservation"] = dict(
            return_files=menu.tree_files(profiles.live),
            target_files=menu.tree_files(root),
            sibling_saves=menu.sibling_saves(app, target.profile_id, job["return_profile"]),
            immutable={str(path): menu.file_facts(path) for path in immutable},
        )
        job["source_original"] = dict(
            archive_sha256=target.archive_sha256,
            variant_id=target.variant_id,
            scenario=target.scenarios[0],
        )
        menu.verify_config(job)
        owner_type = menu.pilot_class(None, type("SyntheticOuterOwner", (), {}))
        owner = owner_type.__new__(owner_type)
        owner.__dict__.update(vars(self.owner))
        owner.config, owner.job = config, job
        owner.verify_available = lambda: None
        owner.verify_code = lambda: None

        class FirstCopyReached(Exception):
            pass

        with (
            patch(
                "agent_actor_binding.collect_actor",
                return_value=dict(
                    model="gpt-6.1-sol",
                    effort="high",
                    approval_policy="never",
                    sandbox="danger-full-access",
                ),
            ),
            patch.object(menu.subprocess, "run") as signature,
            patch.object(menu, "preserve_autosave_preimage", side_effect=FirstCopyReached) as copy,
        ):
            with self.assertRaises(FirstCopyReached):
                owner.prepare()
        self.assertEqual(signature.call_count, 2)
        self.assertEqual(copy.call_count, 1)
        self.assertIn(str(first), owner.baseline["immutable"])
        self.assertIn(str(later), owner.baseline["immutable"])
        self.assertEqual(owner.baseline, config["preservation"])
        self.assertEqual(profiles._state()["active"], job["return_profile"])

    def test_gameplay_rotated_autosave_needs_separate_settled_origin(self):
        job = self.gameplay_config()
        self.gameplay_after_owned_trial(job)
        self.assertIs(menu.verify_config(job), self.owner.config)
        del job["catalogue_menu"]["resume_save"]["autosave_origin"]
        with self.assertRaisesRegex(ControlRefused, "Test autosave was not retained"):
            menu.gameplay_origin(job)

    def test_gameplay_recent_origin_refuses_unknown_launch_exit_and_changed_files(self):
        job = self.gameplay_config()
        path, original = self.gameplay_after_owned_trial(job)
        proof = job["catalogue_menu"]["resume_save"]
        for cause in (
            "pending",
            "wrong-target",
            "stopped",
            "not-absent",
            "wrong-executable",
            "wrong-job",
            "named",
            "autosave",
            "not-immutable",
        ):
            with self.subTest(cause=cause):
                value = menu.decode(menu.canonical(original))
                if cause == "pending":
                    value["service_settled"]["pending"] = "unknown"
                elif cause == "wrong-target":
                    value["service_settled"]["commit_response"]["data"]["launched_profile_id"] = (
                        "foreign"
                    )
                elif cause == "stopped":
                    value["exact_normal_exit"]["result"]["stop_requested"] = True
                elif cause == "not-absent":
                    value["owned_absences"][1]["positive_absence"] = False
                elif cause == "wrong-executable":
                    value["exact_normal_exit"]["process"]["executable"] = "/foreign/game"
                elif cause == "wrong-job":
                    value["exact_normal_exit"]["job"] = "foreign"
                elif cause == "named":
                    value["exact_retained_named_save"]["inode"] += 1
                elif cause == "autosave":
                    value["current_test_autosaves"]["Auto0.SMRailroadsSavedGame"]["inode"] += 1
                path.write_bytes(menu.canonical(value))
                proof["autosave_origin"]["sha256"] = menu.sha(path.read_bytes())
                job["catalogue_menu"]["preservation"]["immutable"][str(path)] = menu.file_facts(
                    path
                )
                if cause == "not-immutable":
                    del job["catalogue_menu"]["preservation"]["immutable"][str(path)]
                with self.assertRaises(ControlRefused):
                    menu.gameplay_origin(job)

    def test_real_gameplay_entry_decisions_after_rotation_keep_original_named_proof(self):
        job, admission, route = self.gameplay_route()
        self.gameplay_after_owned_trial(job)
        driver.verify_entry(job, admission, "a" * 64)
        with (
            patch.object(menu.time, "monotonic", side_effect=lambda: route.clock[0]),
            patch.object(
                driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
            ),
        ):
            result = route.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertEqual(route.owner.launches, 1)
        self.assertTrue(result["restored"] and result["terminal_finished"])
        proof = job["catalogue_menu"]["resume_save"]
        original = Path(proof["origin_readback"])
        value = menu.decode(original.read_bytes())
        value["final_normal_exit"] = False
        original.write_bytes(menu.canonical(value))
        proof["origin_sha256"] = menu.sha(original.read_bytes())
        job["catalogue_menu"]["preservation"]["immutable"][str(original)] = menu.file_facts(
            original
        )
        with self.assertRaisesRegex(ControlRefused, "both normal exits"):
            menu.gameplay_origin(job)

    def test_gameplay_config_binds_named_save_and_every_owned_autosave(self):
        job = self.gameplay_config()
        self.assertIs(menu.verify_config(job), self.owner.config)
        proof = job["catalogue_menu"]["resume_save"]
        del proof["autosaves"]["Auto1.SMRailroadsSavedGame"]
        with self.assertRaisesRegex(ControlRefused, "Every pre-existing"):
            menu.verify_config(job)

    def test_gameplay_origin_refuses_unsettled_wrong_target_and_named_save_drift(self):
        job = self.gameplay_config()
        proof = job["catalogue_menu"]["resume_save"]
        path = Path(proof["origin_readback"])
        original = menu.decode(path.read_bytes())
        for cause in ("pending", "wrong-target", "stopped", "not-absent", "named", "autosave"):
            with self.subTest(cause=cause):
                value = menu.decode(menu.canonical(original))
                if cause == "pending":
                    value["service_settled"][1]["pending"] = "unknown"
                elif cause == "wrong-target":
                    value["service_settled"][1]["commit_response"]["data"][
                        "launched_profile_id"
                    ] = "foreign"
                elif cause == "stopped":
                    value["second_observer"]["result"]["stop_requested"] = True
                elif cause == "not-absent":
                    value["owned_absences"][1]["positive_absence"] = False
                elif cause == "named":
                    value["exact_new_named_save"]["sha256"] = "f" * 64
                else:
                    value["new_target_files_retained"]["Saves/Auto1.SMRailroadsSavedGame"][
                        "inode"
                    ] += 1
                path.write_bytes(menu.canonical(value))
                proof["origin_sha256"] = menu.sha(path.read_bytes())
                job["catalogue_menu"]["preservation"]["immutable"][str(path)] = menu.file_facts(
                    path
                )
                with self.assertRaises(ControlRefused):
                    menu.gameplay_origin(job)

    def test_gameplay_preimages_allow_only_proven_slots_and_keep_named_saves_strict(self):
        job = self.gameplay_config()
        self.owner.job = job
        self.owner.gameplay_autosave_preimages = {}
        for name, facts in self.owner.config["resume_save"]["autosaves"].items():
            self.owner.gameplay_autosave_preimages[name] = menu.preserve_autosave_preimage(
                self.owner.profiles.live / "Saves" / name,
                self.owner.directory / (name + "-before"),
                facts,
            )
            (self.owner.profiles.live / "Saves" / name).write_bytes(b"new owned test rotation")
        self.owner.baseline = self.owner.config["preservation"]
        menu.target_files_unchanged(self.owner, self.owner.profiles.live)
        named = self.owner.profiles.live / "Saves" / self.owner.config["resume_save"]["filename"]
        named.write_bytes(b"must refuse changed named save")
        with self.assertRaisesRegex(ControlRefused, "Existing target file changed"):
            menu.target_files_unchanged(self.owner, self.owner.profiles.live)

    def test_gameplay_restores_return_and_retains_each_rotated_test_preimage(self):
        job = self.gameplay_config()
        self.owner.job = job
        self.owner.baseline = self.owner.config["preservation"]
        self.owner.gameplay_autosave_preimages = {}
        saves = self.owner.profiles.live / "Saves"
        for name, facts in self.owner.config["resume_save"]["autosaves"].items():
            copy = self.owner.directory / (name + "-preserved")
            self.owner.gameplay_autosave_preimages[name] = menu.preserve_autosave_preimage(
                saves / name, copy, facts
            )
            (saves / name).write_bytes(b"rotated inside synthetic owned game")
        (saves / "Auto2.SMRailroadsSavedGame").write_bytes(b"new test autosave retained")
        self.assertTrue(menu.restore_catalogue(self.owner))
        self.assertEqual(self.owner.profiles._state()["active"], job["return_profile"])
        target = self.owner.profiles._profile_path(self.target.profile_id)
        proof = self.owner.config["resume_save"]
        self.assertEqual(menu.file_facts(target / "Saves" / proof["filename"]), proof["facts"])
        self.assertEqual(
            (target / "Saves/Auto2.SMRailroadsSavedGame").read_bytes(),
            b"new test autosave retained",
        )
        for name, copy in self.owner.gameplay_autosave_preimages.items():
            self.assertEqual(menu.file_facts(copy)["sha256"], proof["autosaves"][name]["sha256"])

    def test_gameplay_permits_one_packaged_launch_only(self):
        cls = menu.pilot_class(SimpleNamespace(), object)
        owner = cls.__new__(cls)
        owner.job = dict(purpose=menu.GAMEPLAY_PURPOSE)
        owner.launches = 0
        calls = []
        owner._launch_packaged = lambda: calls.append("packaged Play")
        owner.launch()
        with self.assertRaisesRegex(ControlRefused, "One packaged launch maximum"):
            owner.launch()
        self.assertEqual(calls, ["packaged Play"])

    def test_gameplay_entry_refuses_missing_origin_baseline_and_new_save_intent(self):
        job, admission = self.entry_contract(driver.CACHED_GAMEPLAY_PURPOSE)
        self.gameplay_config(job)
        proof = job["catalogue_menu"]["resume_save"]
        facts = job["catalogue_menu"]["preservation"]["immutable"].pop(proof["origin_readback"])
        with self.assertRaisesRegex(ControlRefused, "immutable baseline"):
            driver.verify_entry(job, admission, "a" * 64)
        job["catalogue_menu"]["preservation"]["immutable"][proof["origin_readback"]] = facts
        job["agent_save"] = dict(token="WrongToken1234567890", filename="irrelevant")
        with self.assertRaisesRegex(ControlRefused, "without new-save intent"):
            driver.verify_entry(job, admission, "a" * 64)

    def gameplay_route(self):
        job, admission = self.entry_contract(driver.CACHED_GAMEPLAY_PURPOSE)
        self.gameplay_config(job)
        driver.verify_entry(job, admission, "a" * 64)
        route = route_fixtures.AgentRouteTests()
        route.setUp()
        self.addCleanup(route.doCleanups)
        owner = route.owner
        parent = type(owner)
        owner.__class__ = menu.pilot_class(route.common, parent)
        for name in ("prepare", "launch", "stop", "restore"):
            setattr(owner, name, getattr(parent, name).__get__(owner))
        owner.job = job
        owner.config = job["catalogue_menu"]
        owner.job["job"] = "synthetic-retained-gameplay"
        owner.job["orca"] = "/synthetic/orca"
        owner.job["root"]["session"] = "synthetic-root"
        owner.job["actor_binding"]["provider_runtime_id"] = "synthetic-runtime"
        owner.diagnostic = owner.config["target"]["profile_id"]
        # Real entry/decisions/file validator/workflow; physical lifecycle is synthetic.
        owner.profiles.live = self.owner.profiles.live
        route.reply_transform = lambda value: dict(
            value, exact_token=owner.config["resume_save"]["token"]
        )
        return job, admission, route

    def test_real_gameplay_entry_to_actual_decisions_uses_one_launch_no_save(self):
        job, admission, route = self.gameplay_route()
        owner = route.owner
        with (
            patch.object(menu.time, "monotonic", side_effect=lambda: route.clock[0]),
            patch.object(
                driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
            ),
        ):
            result = route.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertEqual(owner.launches, 1)
        self.assertEqual(set(route.stage_indexes), {"reopened", "action", "continued", "finished"})
        self.assertEqual(route.actions.count(["hotkey", "--key", "CmdOrCtrl+Q"]), 1)
        self.assertFalse(any(action[0] == "type-text" for action in route.actions))
        self.assertTrue(result["restored"] and result["terminal_finished"])
        self.assertFalse(result["outcome"]["gameplay_credit"])
        self.assertNotIn("agent_save", job)
        admission["decision"] = driver.admission_decisions(driver.CACHED_SAVE_PURPOSE)[0]
        with self.assertRaisesRegex(ControlRefused, "Exact root grant"):
            driver.verify_entry(job, admission, "a" * 64)

    def test_gameplay_partial_takes_normal_quit_without_action(self):
        job, admission, route = self.gameplay_route()
        route.reply_transform = lambda value: dict(
            value,
            choice="partial",
            action=[],
            observation="No current paused world; stop route discovery",
        )
        # Only work stages return partial; final Quit remains the actual input path.
        partial = route.reply_transform
        route.reply_transform = lambda value: (
            partial(value) if "finished" not in route.stage_indexes else value
        )
        with patch.object(
            driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
        ):
            result = route.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertEqual(set(route.stage_indexes), {"reopened", "finished"})
        self.assertEqual(route.actions, [["hotkey", "--key", "CmdOrCtrl+Q"]])
        self.assertIsNotNone(result["outcome"]["partial_reason"])

    def test_gameplay_decision_cap_takes_existing_normal_quit(self):
        job, admission, route = self.gameplay_route()
        route.reply_transform = lambda value: (
            dict(value, choice="wait", action=[])
            if "finished" not in route.stage_indexes
            else value
        )
        with patch.object(
            driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
        ):
            result = route.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertEqual(route.stage_indexes["reopened"], 16)
        self.assertEqual(route.actions, [["hotkey", "--key", "CmdOrCtrl+Q"]])
        self.assertFalse(result["outcome"]["gameplay_credit"])

    def test_gameplay_final_390_cutoff_blocks_work_but_allows_normal_quit(self):
        job, admission, route = self.gameplay_route()
        route.before_poll = lambda: route.clock.__setitem__(0, 491.0)
        with patch.object(
            driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
        ):
            result = route.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertIn("cutoff", result["outcome"]["partial_reason"])
        self.assertEqual(route.actions, [["hotkey", "--key", "CmdOrCtrl+Q"]])
        self.assertEqual(route.owner.launches, 1)

    def test_gameplay_wrong_row_acknowledgement_refuses_double_click(self):
        job, admission, route = self.gameplay_route()
        exact = route.reply_transform
        route.reply_transform = lambda value: (
            dict(value, exact_token="WrongRow12345678901")
            if value["action"][-2:] == ["--click-count", "2"]
            else exact(value)
        )
        with patch.object(
            driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
        ):
            result = route.run_trial()
        self.assertIn("Exact retained row acknowledgement required", result["outcome"]["cause"])
        self.assertFalse(any(action[-2:] == ["--click-count", "2"] for action in route.actions))

    def test_gameplay_refuses_a_second_retained_row_double_click(self):
        job, admission, route = self.gameplay_route()
        exact = route.reply_transform
        route.reply_transform = lambda value: (
            dict(
                exact(value),
                choice="action",
                action=["click", "--x", "10", "--y", "20", "--click-count", "2"],
            )
            if route.stage_indexes.get("reopened", 0) >= 3
            else exact(value)
        )
        with patch.object(
            driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
        ):
            result = route.run_trial()
        self.assertIn("One retained-row double click maximum", result["outcome"]["cause"])
        self.assertEqual(sum(action[-2:] == ["--click-count", "2"] for action in route.actions), 1)

    def test_gameplay_named_save_change_at_final_ingress_refuses_before_input(self):
        job, admission, route = self.gameplay_route()
        path = route.owner.profiles.live / "Saves" / route.owner.config["resume_save"]["filename"]
        route.before_poll = lambda: path.write_bytes(b"changed after visual decision")
        with patch.object(
            driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
        ):
            result = route.run_trial()
        self.assertIn("Exact retained gameplay save changed", result["outcome"]["cause"])
        self.assertEqual(route.actions, [])
        self.assertFalse(route.owner.uncertain_input)
        self.assertEqual(route.argv, [])

    def test_gameplay_refuses_save_stages_before_any_provider_request(self):
        job, admission, route = self.gameplay_route()
        for stage in (
            "world",
            "filename",
            "saved",
            "quit",
            "menu",
            "returned",
            "gui_select",
            "gui_play",
        ):
            with (
                self.subTest(stage=stage),
                self.assertRaisesRegex(ControlRefused, "cannot start a scenario or save"),
            ):
                route.owner.decisions.stage(stage, "not granted")
        self.assertEqual(route.stage_indexes, {})
        frame = dict(window=dict(width=100, height=100))
        for action in (
            ["hotkey", "--key", "CmdOrCtrl+S"],
            ["type-text", "--text", job["catalogue_menu"]["resume_save"]["token"]],
        ):
            with self.assertRaises(ControlRefused):
                driver.validate_action(
                    "action", action, frame, None, "owned_window", purpose=menu.GAMEPLAY_PURPOSE
                )

    def test_real_229_entry_checks_current_evidence_and_complete_closure(self):
        job, admission = self.entry_contract(driver.CACHED_LOAD_PURPOSE)
        expected = dict(driver.PREINPUT_229)
        contract = dict(expected)
        for path_key, hash_key in (
            ("native_helper_path", "native_helper_sha256"),
            ("native_source", "native_source_sha256"),
            ("proof_path", "proof_sha256"),
            ("release_match_path", "release_match_sha256"),
            ("request_loop_source_path", "request_loop_source_sha256"),
        ):
            path = (
                Path(job["native_source"])
                if path_key == "native_source"
                else self.owner.directory / path_key
            )
            path.write_bytes(("temporary provider " + path_key).encode())
            expected[hash_key] = contract[hash_key] = driver.sha(path.read_bytes())
            if path_key != "native_source":
                contract[path_key] = str(path)
        expected["native_helper_path"] = contract["native_helper_path"]
        job["preinput_refusal_contract"] = contract
        paths = {row["path"] for row in job["closure"]}
        paths.update(
            contract[key]
            for key in ("proof_path", "release_match_path", "request_loop_source_path")
        )
        job["closure"] = [
            dict(path=p, bytes=Path(p).stat().st_size, sha256=driver.sha(Path(p).read_bytes()))
            for p in sorted(paths)
        ]
        review_path = Path(admission["review"]["path"])
        review = menu.decode(review_path.read_bytes())
        review["closure"] = job["closure"]
        review_path.write_bytes(menu.canonical(review))
        admission["review"]["sha256"] = driver.sha(review_path.read_bytes())
        with patch.object(driver, "PREINPUT_229", expected):
            driver.verify_entry(job, admission, "a" * 64)
            proof = Path(contract["proof_path"])
            original = proof.read_bytes()
            proof.write_bytes(b"changed proof")
            with self.assertRaisesRegex(ControlRefused, "build/proof changed"):
                driver.verify_entry(job, admission, "a" * 64)
            proof.write_bytes(original)
            job["closure"] = [row for row in job["closure"] if row["path"] != str(proof)]
            with self.assertRaisesRegex(ControlRefused, "Actual imported source paths missing"):
                driver.verify_entry(job, admission, "a" * 64)

    def test_real_load_entry_needs_no_save_intent_and_keeps_closure_guard(self):
        job, admission = self.entry_contract(driver.CACHED_LOAD_PURPOSE)
        self.assertNotIn("agent_save", job)
        driver.verify_entry(job, admission, "a" * 64)
        job["closure"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ControlRefused, "source closure drift"):
            driver.verify_entry(job, admission, "a" * 64)

    def direct_load_entry(self, scenario_count=2):
        job, admission = self.entry_contract(driver.CATALOGUE_LOAD_PURPOSE)
        config = job["catalogue_menu"]
        config.pop("selection")
        config.pop("preparation")
        config.update(
            prepare_compatibility=False,
            warning="Exact synthetic warning retained",
            scenario_title="Synthetic Solo",
            scenario_subtitle="Version 2",
        )
        if scenario_count == 2:
            config["target"]["scenarios"].append("UserMaps/Synthetic/RRT_Scenario_Solo.xml")
        job["source_original"]["scenario"] = config["target"]["scenarios"][-1]
        return job, admission

    def test_direct_catalogue_load_real_entry_decisions_world_and_normal_quit(self):
        self.assert_direct_load_lifecycle(2)

    def test_single_scenario_direct_load_real_entry_decisions_world_and_normal_quit(self):
        self.assert_direct_load_lifecycle(1)

    def assert_direct_load_lifecycle(self, scenario_count):
        job, admission = self.direct_load_entry(scenario_count)
        route = route_fixtures.AgentRouteTests()
        route.setUp()
        self.addCleanup(route.doCleanups)
        owner = route.owner
        route.cached_save_owner()  # Reuse the catalogue class and closed physical lifecycle.
        job["job"] = "synthetic-direct-load-" + str(scenario_count)
        job["game_sha256"] = owner.job["game_sha256"]
        job["orca"] = "/synthetic/orca"
        job["root"]["session"] = "synthetic-root"
        job["actor_binding"]["provider_runtime_id"] = "synthetic-runtime"
        owner.job, owner.config = job, job["catalogue_menu"]
        owner.diagnostic = owner.config["target"]["profile_id"]
        driver.verify_entry(job, admission, "a" * 64)
        route.quit_confirmation = True
        with patch.object(
            driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
        ):
            result = route.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(owner.launches, 1)
        self.assertEqual(set(route.stage_indexes), {"launcher", "world", "finished"})
        request = next(
            menu.decode(p.read_bytes())
            for p in route.directory.glob("*-request.json")
            if menu.decode(p.read_bytes())["stage"] == "world"
        )
        self.assertIn("'Synthetic Solo' and subtitle 'Version 2'", request["instruction"])
        self.assertIn("do not assume its default selection", request["instruction"])
        self.assertEqual(len(owner.config["target"]["scenarios"]), scenario_count)
        self.assertEqual(result["outcome"]["world_loaded"], "agent_observed")
        self.assertFalse(
            result["outcome"]["gameplay_credit"] or result["outcome"]["save_reopen_credit"]
        )
        self.assertEqual(
            route.actions[-2:],
            [["hotkey", "--key", "CmdOrCtrl+Q"], ["press-key", "--key", "Return"]],
        )
        self.assertEqual(route.keep.read_bytes(), b"preserved original synthetic save")

    def test_direct_catalogue_load_refuses_other_selection_preparation_and_old_grants(self):
        job, admission = self.direct_load_entry()
        driver.verify_entry(job, admission, "a" * 64)
        config = job["catalogue_menu"]
        original = menu.decode(menu.canonical(job))
        for cause in (
            "missing-scenario",
            "duplicate-basename",
            "no-scenarios",
            "three-scenarios",
            "prepare",
            "save",
            "resume",
            "subtitle",
            "saves",
        ):
            with self.subTest(cause=cause):
                job = menu.decode(menu.canonical(original))
                config = job["catalogue_menu"]
                if cause == "missing-scenario":
                    job["source_original"]["scenario"] = "UserMaps/Other.xml"
                elif cause == "duplicate-basename":
                    config["target"]["scenarios"][1] = "CustomAssets/synthetic.xml"
                elif cause == "no-scenarios":
                    config["target"]["scenarios"] = []
                elif cause == "three-scenarios":
                    config["target"]["scenarios"].append("UserMaps/Synthetic/Third.xml")
                elif cause == "prepare":
                    config["prepare_compatibility"] = True
                elif cause == "save":
                    job["agent_save"] = dict(
                        token="SyntheticToken12345",
                        filename="SyntheticToken12345.SMRailroadsSavedGame",
                    )
                elif cause == "resume":
                    config["resume_save"] = {}
                elif cause == "subtitle":
                    config["scenario_subtitle"] = ""
                else:
                    config["preservation"]["target_files"]["Saves/personal.sav"] = {}
                with self.assertRaises(ControlRefused):
                    driver.verify_entry(job, admission, "a" * 64)
        job = original
        admission["decision"] = driver.admission_decisions(driver.CACHED_LOAD_PURPOSE)[0]
        with self.assertRaisesRegex(ControlRefused, "Exact root grant"):
            driver.verify_entry(job, admission, "a" * 64)
        admission["decision"] = driver.admission_decisions(driver.CATALOGUE_LOAD_PURPOSE)[0]
        path = Path(admission["review"]["path"])
        review = menu.decode(path.read_bytes())
        review["decision"] = driver.admission_decisions(driver.CACHED_LOAD_PURPOSE)[1]
        path.write_bytes(menu.canonical(review))
        admission["review"]["sha256"] = driver.sha(path.read_bytes())
        with self.assertRaisesRegex(ControlRefused, "Exact agent composition review"):
            driver.verify_entry(job, admission, "a" * 64)

    def test_real_load_entry_to_actual_decisions_and_catalogue_quit_without_save_intent(self):
        route = route_fixtures.AgentRouteTests()
        route.setUp()
        self.addCleanup(route.doCleanups)
        owner = route.owner
        parent = type(owner)
        owner.__class__ = menu.pilot_class(route.common, parent)
        # Keep the actual catalogue workflow/decisions/guards. Physical launch,
        # stop and restore remain the existing closed synthetic lifecycle.
        for name in ("prepare", "launch", "stop", "restore"):
            setattr(owner, name, getattr(parent, name).__get__(owner))
        job, admission = self.entry_contract(driver.CACHED_LOAD_PURPOSE)
        job["job"] = "synthetic-load-entry-through-quit"
        job["orca"] = "/synthetic/orca"
        job["root"]["session"] = "synthetic-root"
        job["actor_binding"]["provider_runtime_id"] = "synthetic-runtime"
        owner.job = job
        owner.config = job["catalogue_menu"]
        owner.diagnostic = owner.config["target"]["profile_id"]
        self.assertNotIn("agent_save", owner.job)
        driver.verify_entry(owner.job, admission, "a" * 64)
        route.quit_confirmation = True
        with patch.object(
            driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
        ):
            result = route.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(result["outcome"]["world_loaded"], "agent_observed")
        self.assertFalse(result["outcome"]["gameplay_credit"])
        self.assertFalse(result["outcome"]["save_reopen_credit"])
        self.assertEqual(
            route.actions,
            [
                ["click", "--element-index", "18"],
                ["click", "--x", "10", "--y", "20"],
                ["hotkey", "--key", "CmdOrCtrl+Q"],
                ["press-key", "--key", "Return"],
            ],
        )
        self.assertEqual(set(route.stage_indexes), {"launcher", "world", "finished"})
        self.assertEqual(owner.events[-3:], ["exact_stop", "restore", "finish"])

    def test_real_save_entry_requires_exact_intent(self):
        job, admission = self.entry_contract(driver.CACHED_SAVE_PURPOSE)
        for intent in (
            None,
            {},
            {"token": 19, "filename": "invalid"},
            {"token": "SyntheticToken12345", "filename": "wrong"},
        ):
            with self.subTest(intent=intent):
                if intent is None:
                    job.pop("agent_save", None)
                else:
                    job["agent_save"] = intent
                with self.assertRaisesRegex(ControlRefused, "Exact new agent save contract"):
                    driver.verify_entry(job, admission, "a" * 64)
        job["agent_save"] = dict(
            token="SyntheticToken12345", filename="SyntheticToken12345.SMRailroadsSavedGame"
        )
        driver.verify_entry(job, admission, "a" * 64)

    def test_real_save_entry_through_decisions_commits_observed_filename_without_saved_turn(self):
        route = route_fixtures.AgentRouteTests()
        route.setUp()
        self.addCleanup(route.doCleanups)
        route.cached_save_owner()
        job, admission = self.entry_contract(driver.CACHED_SAVE_PURPOSE)
        job["job"] = "synthetic-save-entry-through-quit"
        job["game_sha256"] = route.owner.job["game_sha256"]
        job["agent_save"] = dict(route.owner.job["agent_save"])
        job["orca"] = "/synthetic/orca"
        job["root"]["session"] = "synthetic-root"
        job["actor_binding"]["provider_runtime_id"] = "synthetic-runtime"
        route.owner.job = job
        route.owner.config = job["catalogue_menu"]
        route.owner.diagnostic = route.owner.config["target"]["profile_id"]
        driver.verify_entry(job, admission, "a" * 64)
        with patch.object(
            driver, "NATIVE_SOURCE_SHA256", driver.sha(Path(job["native_source"]).read_bytes())
        ):
            result = route.run_trial()
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(route.owner.launches, 2)
        self.assertNotIn("saved", route.stage_indexes)
        self.assertEqual(route.actions.count(["press-key", "--key", "Return"]), 1)
        self.assertEqual(route.actions.count(["hotkey", "--key", "CmdOrCtrl+Q"]), 2)
        self.assertEqual(route.keep.read_bytes(), b"preserved original synthetic save")
        commit = menu.decode((route.directory / "save-commit-input.json").read_bytes())
        self.assertEqual(commit["evidence_kind"], "mechanical_commit_from_agent_observed_filename")
        self.assertFalse(result["outcome"]["save_reopen_credit"])

    def test_real_load_entry_refuses_save_grant_or_review(self):
        job, admission = self.entry_contract(driver.CACHED_LOAD_PURPOSE)
        decision = admission["decision"]
        admission["decision"] = driver.admission_decisions(driver.CACHED_SAVE_PURPOSE)[0]
        with self.assertRaisesRegex(ControlRefused, "Exact root grant"):
            driver.verify_entry(job, admission, "a" * 64)
        admission["decision"] = decision
        path = Path(admission["review"]["path"])
        review = menu.decode(path.read_bytes())
        review["decision"] = driver.admission_decisions(driver.CACHED_SAVE_PURPOSE)[1]
        path.write_bytes(menu.canonical(review))
        admission["review"]["sha256"] = driver.sha(path.read_bytes())
        with self.assertRaisesRegex(ControlRefused, "Exact agent composition review"):
            driver.verify_entry(job, admission, "a" * 64)

    def test_original_save_entry_still_requires_save_intent(self):
        job, admission = self.entry_contract(driver.PURPOSE)
        with self.assertRaisesRegex(ControlRefused, "Exact new agent save contract"):
            driver.verify_entry(job, admission, "a" * 64)

    def test_empty_save_target_admits_without_origin_or_extra_backup_and_retains_new_files(self):
        job = self.save_config(empty=True)
        with patch.object(menu, "autosave_origin", side_effect=AssertionError("No prior autosave")):
            self.assertIs(menu.verify_config(job), self.owner.config)
            saves = self.owner.profiles.live / "Saves"
            for name in ("Auto0.SMRailroadsSavedGame", "Auto1.SMRailroadsSavedGame", "new.sav"):
                (saves / name).write_bytes(name.encode())
            expected = menu.tree_files(saves)
            self.assertTrue(menu.restore_catalogue(self.owner))
        target = self.owner.profiles._profile_path(self.target.profile_id)
        self.assertEqual(menu.tree_files(target / "Saves"), expected)
        self.assertFalse(list(self.owner.directory.glob("owned-Auto*-preimage")))
        self.assertEqual(
            menu.tree_files(self.owner.profiles.live), self.owner.baseline["return_files"]
        )

    def test_empty_target_refuses_misleading_prior_autosave_evidence(self):
        job = self.save_config(empty=True)
        self.owner.config["owned_autosave"] = {}
        with self.assertRaisesRegex(ControlRefused, "Empty target must not declare"):
            menu.verify_config(job)

    def test_corrupt_owned_autosave_copy_or_origin_holds_before_switch(self):
        copy = self.owned_autosave()
        copy.write_bytes(b"corrupt archive")
        self.assert_refusal_preserves("preimage changed")
        copy.write_bytes(b"prior owned autosave")
        origin = Path(self.owner.config["owned_autosave"]["origin_readback"])
        origin.write_bytes(b"changed origin")
        self.assert_refusal_preserves("origin changed")

    def test_missing_owned_autosave_copy_keeps_strict_original_check(self):
        self.owned_autosave()
        self.owner.autosave_preimage = None
        self.assert_refusal_preserves("Existing target file changed")

    def test_unsettled_origin_never_authorizes_autosave_rotation(self):
        self.owned_autosave()
        proof = self.owner.config["owned_autosave"]
        path = Path(proof["origin_readback"])
        origin = menu.decode(path.read_bytes())
        origin["terminal_finished"] = False
        path.write_bytes(menu.canonical(origin))
        proof["origin_sha256"] = menu.sha(path.read_bytes())
        with self.assertRaisesRegex(ControlRefused, "settled owned trial"):
            menu.autosave_origin(self.owner.job)

    def test_changed_owned_autosave_refuses_snapshot_without_replacing_any_file(self):
        copy = self.owned_autosave()
        with self.assertRaisesRegex(ControlRefused, "collision"):
            menu.preserve_owned_autosave(self.owner, self.owner.profiles.live)
        copy.unlink()
        with self.assertRaisesRegex(ControlRefused, "changed before preservation"):
            menu.preserve_owned_autosave(self.owner, self.owner.profiles.live)
        self.assertFalse(copy.exists())
        self.assertEqual(
            (self.owner.profiles.live / menu.OWNED_AUTOSAVE).read_bytes(), b"new game autosave"
        )

    def test_journal_and_live_service_hold(self):
        self.owner.profiles.journal.write_bytes(b"pending")
        self.assert_refusal_preserves("Activation journal")
        self.owner.profiles.journal.unlink()
        self.owner.service = SimpleNamespace(child=SimpleNamespace(poll=lambda: None))
        self.assert_refusal_preserves("service still live")

    def test_late_cleanup_and_corrupt_preimage_hold(self):
        self.owner.anchor = time.monotonic() - 901
        self.assert_refusal_preserves("cutoff expired")
        self.owner.anchor = time.monotonic()
        Path(self.owner.preimages[-1]["copy"]).write_bytes(b"corrupt")
        self.assert_refusal_preserves("preimage changed")

    def test_no_dispatch_return_state_is_allowed_but_unexpected_return_is_not(self):
        self.owner.app.activate(self.owner.job["return_profile"])
        self.assert_refusal_preserves("Unexpected return")
        self.owner.open_attempted = False
        self.assertTrue(menu.restore_catalogue(self.owner))


class PackagedBoundaryTests(unittest.TestCase):
    def service(self):
        service = menu.PackagedService.__new__(menu.PackagedService)
        service.pending = None
        service.commit_sent = False
        service.closed = False
        service.child = SimpleNamespace(
            poll=lambda: None, stdin=SimpleNamespace(fileno=lambda: 100)
        )
        service.owner = SimpleNamespace(
            mutation_guard=nullcontext,
            launch_deadline=time.monotonic() + 10,
            control=SimpleNamespace(_poll_locked=lambda: None, cancelled=False),
            open_attempted=False,
        )
        return service

    def test_exact_argv_payload_one_commit_and_uncertainty_before_write(self):
        service = self.service()
        values = []

        def write(fd, raw):
            self.assertTrue(service.owner.open_attempted)
            self.assertTrue(service.commit_sent)
            values.append(menu.decode(raw))
            return len(raw)

        with (
            patch.object(menu.os, "write", side_effect=write),
        ):
            service.send("play-commit", "maptools.play_commit", {"confirmed": True}, commit=True)
            service.pending = None
            with self.assertRaisesRegex(ControlRefused, "One packaged"):
                service.send("again", "maptools.play_commit", {}, commit=True)
        self.assertEqual(
            values,
            [dict(id="play-commit", method="maptools.play_commit", params=dict(confirmed=True))],
        )

    def test_cancel_deadline_and_large_request_refuse_before_write(self):
        for cause in ("cancel", "deadline", "size"):
            service = self.service()
            if cause == "cancel":
                service.owner.control.cancelled = True
            if cause == "deadline":
                service.owner.launch_deadline = time.monotonic() - 1
            with (
                patch.object(menu.os, "write") as write,
            ):
                with self.assertRaises(ControlRefused):
                    service.send(
                        "play-commit",
                        "maptools.play_commit",
                        {"oversized": "x" * 4096} if cause == "size" else {},
                        commit=True,
                    )
                write.assert_not_called()
            self.assertFalse(service.commit_sent)

    def test_unknown_write_is_not_replayed(self):
        service = self.service()
        with (
            patch.object(menu.os, "write", side_effect=BrokenPipeError),
        ):
            with self.assertRaises(BrokenPipeError):
                service.send("play-commit", "maptools.play_commit", {}, commit=True)
        self.assertTrue(service.commit_sent)
        self.assertEqual(service.pending, "play-commit")
        self.assertTrue(service.owner.open_attempted)

    def test_relaunch_final_validator_precedes_commit_mark_and_write(self):
        for cause in ("changed-save", "late", "cancel"):
            service = self.service()
            service.owner.launch_deadline = 390.0
            clock = [389.0]
            calls = []

            def validate():
                calls.append("validate")
                self.assertFalse(service.commit_sent or service.owner.open_attempted)
                if cause == "changed-save":
                    raise ControlRefused("Exact saved file changed")
                if cause == "late":
                    clock[0] = 391.0
                if cause == "cancel":
                    service.owner.control.cancelled = True

            service.owner.delivery_validation = validate
            with (
                patch.object(menu.time, "monotonic", side_effect=lambda: clock[0]),
                patch.object(menu.os, "write") as write,
            ):
                with self.assertRaises(ControlRefused):
                    service.send("play-commit", "maptools.play_commit", {}, commit=True)
                write.assert_not_called()
            self.assertEqual(calls, ["validate"])
            self.assertFalse(service.commit_sent or service.owner.open_attempted)
            self.assertIsNone(service.pending)

    def test_partial_nonblocking_write_stays_unknown_and_cannot_replay(self):
        service = self.service()
        with patch.object(menu.os, "write", side_effect=lambda fd, raw: len(raw) - 1) as write:
            with self.assertRaisesRegex(ControlRefused, "Incomplete packaged"):
                service.send("play-commit", "maptools.play_commit", {}, commit=True)
            with self.assertRaisesRegex(ControlRefused, "pending"):
                service.send("play-commit", "maptools.play_commit", {}, commit=True)
            self.assertEqual(write.call_count, 1)
        self.assertTrue(service.commit_sent)
        self.assertEqual(service.pending, "play-commit")
        self.assertTrue(service.owner.open_attempted)

    def test_new_purpose_has_separate_admission_and_closed_menu_grammar(self):
        self.assertEqual(
            driver.admission_decisions(menu.PURPOSE),
            ("ADMIT_ONE_AGENT_PACKAGED_CATALOGUE_MENU", "GO_ONE_AGENT_PACKAGED_CATALOGUE_MENU"),
        )
        for action in (
            ["type-text", "--text", "save"],
            ["press-key", "--key", "B"],
            ["scroll", "--direction", "down", "--pages", "0.1"],
        ):
            with self.assertRaises(ControlRefused):
                driver.validate_action("menu", action, {}, "", "owned_window", purpose=menu.PURPOSE)


class ServiceLifetimeTests(unittest.TestCase):
    def test_real_child_single_request_eof_response_and_reap(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            helper = directory / "service"
            helper.write_text(
                "#!" + sys.executable + "\n" + "import json, sys\n"
                "print(json.dumps(dict(event='ready')), flush=True)\n"
                "for line in sys.stdin:\n"
                " request=json.loads(line)\n"
                " print(json.dumps(dict(id=request['id'],ok=True,data=dict(target='synthetic'))),flush=True)\n"
                "print(json.dumps(dict(event='closed')),flush=True)\n"
            )
            helper.chmod(0o700)
            self.child = None

            @dataclass
            class Identity:
                pid: int
                birth_us: int = 1
                executable: str = str(helper)

                def same(self, other):
                    return self == other

            test = self

            class Monitor:
                def __init__(self, *args):
                    pass

                def inspect(self, pid):
                    return (
                        Identity(pid) if test.child is None or test.child.poll() is None else None
                    )

            owner = SimpleNamespace(
                directory=directory,
                mutation_guard=nullcontext,
                config=dict(package=dict(executable=str(helper))),
                creation=[],
                control=SimpleNamespace(
                    _poll_locked=lambda: None, cancelled=False, cleanup_guard=nullcontext
                ),
                launch_deadline=time.monotonic() + 10,
                anchor=time.monotonic(),
                verify_cleanup_owner=lambda: None,
                tick=lambda: None,
                write=lambda *args: None,
            )
            module = SimpleNamespace(MacProcesses=Monitor)
            with patch.dict(sys.modules, frozen_mac_processes=module):
                service = menu.PackagedService(owner)
                self.child = service.child
                try:
                    service.receive(event="ready", deadline=time.monotonic() + 3)
                    service.send(
                        "play-commit",
                        "maptools.play_commit",
                        dict(
                            confirmed=True,
                            profile_id="map-" + "a" * 64,
                            archive_sha256="b" * 64,
                            variant_id="c" * 64,
                            game_executable_sha256="d" * 64,
                            prepare_compatibility=False,
                            launch_deadline=owner.launch_deadline,
                            ticket="e" * 64,
                            fingerprint="f" * 64,
                        ),
                        commit=True,
                    )
                    response = service.receive(deadline=time.monotonic() + 3)
                    self.assertTrue(response["ok"])
                    self.assertIsNone(service.pending)
                finally:
                    service.settle()
            self.assertEqual(service.child.returncode, 0)
            self.assertTrue(service.closed)
            self.assertTrue(service.eof)
            self.assertFalse(service.thread.is_alive())
            self.assertEqual(service.child.args, [str(helper), "--native-service"])
            self.assertTrue(service.stdout.closed)

    def test_unknown_unbound_dispatch_keeps_observer_and_refuses_stop(self):
        events = []

        class Base:
            def stop(self):
                events.append("base-stop")

        cls = menu.pilot_class(SimpleNamespace(), Base)
        owner = cls.__new__(cls)
        owner.verify_cleanup_owner = lambda: events.append("owner")
        owner.service = SimpleNamespace(
            stdout=SimpleNamespace(closed=False),
            commit_sent=True,
            settle=lambda: events.append("service-reaped"),
        )
        owner.open_attempted, owner.game = True, None
        owner.observer = SimpleNamespace(terminate=lambda: events.append("observer-stop"))
        with self.assertRaisesRegex(ValueError, "observer retained"):
            owner.stop()
        self.assertEqual(events, ["owner", "service-reaped"])

    def test_settled_service_precedes_exact_game_stop(self):
        events = []

        class Base:
            def stop(self):
                events.append("base-stop")
                return True

        cls = menu.pilot_class(SimpleNamespace(), Base)
        owner = cls.__new__(cls)
        owner.verify_cleanup_owner = lambda: events.append("owner")
        owner.service = SimpleNamespace(
            stdout=SimpleNamespace(closed=False),
            commit_sent=True,
            settle=lambda: events.append("service-reaped"),
        )
        owner.open_attempted, owner.game = True, object()
        self.assertTrue(owner.stop())
        self.assertEqual(events, ["owner", "service-reaped", "base-stop"])

    def test_menu_cutoff_enters_quit_without_new_menu_stage(self):
        events = []
        cls = menu.pilot_class(SimpleNamespace(), object)
        owner = cls.__new__(cls)
        owner.job = dict(purpose=menu.PURPOSE)
        owner.anchor = time.monotonic() - 391
        owner.decisions = SimpleNamespace(stage=lambda *args: events.append("stage"))
        owner.normal_quit = lambda *args: events.append("normal-quit")
        result = owner.observe()
        self.assertEqual(events, ["normal-quit"])
        self.assertIsNotNone(result["partial_reason"])
        self.assertFalse(owner.menu_work_active)
        self.assertFalse(result["gameplay_credit"])


class CatalogueCompositionTests(unittest.TestCase):
    def test_load_owner_observes_only_exact_world_then_existing_quit(self):
        cls = menu.pilot_class(SimpleNamespace(), object)
        owner = cls.__new__(cls)
        owner.job = dict(purpose=menu.LOAD_PURPOSE)
        owner.config = dict(scenario_title="Synthetic Canyon")
        owner.anchor = time.monotonic()
        calls = []

        def stage(name, instruction):
            calls.append((name, instruction))
            self.assertTrue(owner.menu_work_active)
            if name == "world":
                self.assertIn("'Synthetic Canyon' before starting once", instruction)
            return dict(stage=name, observation="Synthetic observed scene")

        def quit_once(*args):
            self.assertFalse(owner.menu_work_active)
            calls.append(("quit", args))

        owner.decisions = SimpleNamespace(stage=stage)
        owner.normal_quit = quit_once
        result = owner.observe()
        self.assertEqual([c[0] for c in calls], ["launcher", "world", "quit"])
        self.assertEqual(result["world_loaded"], "agent_observed")
        self.assertFalse(result["gameplay_credit"] or result["save_reopen_credit"])
        self.assertIsNone(result["partial_reason"])

    def test_load_prerequisite_or_cutoff_keeps_existing_normal_quit(self):
        cls = menu.pilot_class(SimpleNamespace(), object)
        for cause in ("wrong-title", "cutoff"):
            owner = cls.__new__(cls)
            owner.job = dict(purpose=menu.LOAD_PURPOSE)
            owner.config = dict(scenario_title="Synthetic Canyon")
            owner.anchor = 0.0
            calls = []

            def stage(name, instruction):
                calls.append(name)
                if name == "world":
                    raise menu.MenuIncomplete("Exact title unavailable")
                return dict(stage=name)

            owner.decisions = SimpleNamespace(stage=stage)
            owner.normal_quit = lambda *args: calls.append("quit")
            with patch.object(
                menu.time, "monotonic", return_value=390.0 if cause == "cutoff" else 100.0
            ):
                result = owner.observe()
            self.assertEqual(calls[-1], "quit")
            self.assertNotIn("world", result)
            self.assertEqual(result["world_loaded"], "unknown")
            self.assertIsNotNone(result["partial_reason"])
            self.assertFalse(owner.menu_work_active)

    def test_direct_load_missing_selected_title_keeps_other_scenario_unproven_and_quits(self):
        cls = menu.pilot_class(SimpleNamespace(), object)
        owner = cls.__new__(cls)
        owner.job = dict(purpose=menu.DIRECT_LOAD_PURPOSE)
        owner.config = dict(scenario_title="Synthetic Solo", scenario_subtitle="Version 2")
        owner.anchor = time.monotonic()
        calls = []

        def stage(name, instruction):
            calls.append(name)
            if name == "world":
                raise menu.MenuIncomplete("Only the unselected AI title is visible")
            return dict(stage=name)

        owner.decisions = SimpleNamespace(stage=stage)
        owner.normal_quit = lambda *args: calls.append("quit")
        result = owner.observe()
        self.assertEqual(calls, ["launcher", "world", "quit"])
        self.assertEqual(result["world_loaded"], "unknown")
        self.assertIn("unselected AI", result["partial_reason"])
        self.assertFalse(result["gameplay_credit"] or result["save_reopen_credit"])

    def test_load_requires_bounded_exact_reviewed_title(self):
        for title in (None, "", "wrong\nline", " padded", "x" * 257):
            job = dict(
                purpose=menu.LOAD_PURPOSE, catalogue_menu=dict(target={}, scenario_title=title)
            )
            with self.assertRaisesRegex(ControlRefused, "rendered scenario title"):
                menu.verify_config(job)

    def test_first_load_refuses_existing_target_saves_before_any_launch(self):
        job = dict(
            purpose=menu.LOAD_PURPOSE,
            catalogue_menu=dict(
                target={},
                scenario_title="Synthetic Canyon",
                preservation=dict(target_files={"Saves/Auto0.SMRailroadsSavedGame": {}}),
            ),
        )
        with self.assertRaisesRegex(ControlRefused, "without pre-existing saves"):
            menu.verify_config(job)

    def test_save_pilot_requires_proven_autosave_even_with_named_files(self):
        for saves, message in (
            ({"Saves/personal.sav": {}}, "Only the proven"),
            ({menu.OWNED_AUTOSAVE: {}}, "Exact autosave origin"),
        ):
            job = dict(
                purpose=menu.SAVE_PURPOSE,
                catalogue_menu=dict(
                    target={},
                    scenario_title="Synthetic Canyon",
                    preservation=dict(target_files=saves),
                    owned_autosave={},
                ),
            )
            with self.assertRaisesRegex(ControlRefused, message):
                menu.verify_config(job)

    def test_save_pilot_refuses_pretrial_auto1_even_with_proven_auto0(self):
        job = dict(
            purpose=menu.SAVE_PURPOSE,
            catalogue_menu=dict(
                target={},
                scenario_title="Synthetic Canyon",
                preservation=dict(
                    target_files={
                        menu.OWNED_AUTOSAVE: {},
                        "Saves/Auto1.SMRailroadsSavedGame": {},
                    }
                ),
            ),
        )
        with self.assertRaisesRegex(ControlRefused, "Only the proven"):
            menu.verify_config(job)

    def exercise(self, response, *, cached=False, wrong_review=False, two_launches=False):
        events = []

        class Frozen:
            def launch(self, **kwargs):
                raise AssertionError("Frozen source launch must never be called")

            def stop(self):
                events.append("observer-cleanup")
                self.assert_service_closed()
                return True

        common = SimpleNamespace(UKCalibration=Frozen)
        cls = menu.pilot_class(common, driver.pilot_class(common))
        owner = cls.__new__(cls)
        owner.launches = 0
        owner.anchor = time.monotonic()
        owner.launch_deadline = owner.anchor + 120
        owner.creation = []
        owner.operation_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        owner.directory = owner.operation_directory
        owner.first_stop_verified = False
        owner.controller_identity = dict(pid=123)
        owner.job = dict(
            job="synthetic-catalogue-job",
            game_sha256="g" * 64,
            source_original=dict(archive_sha256="a" * 64),
        )
        if two_launches:
            owner.job["purpose"] = menu.SAVE_PURPOSE
        target = dict(profile_id="map-target", archive_sha256="a" * 64, variant_id="b" * 64)
        owner.config = dict(
            target=target,
            prepare_compatibility=cached,
            warning="Experimental cached repair" if cached else "",
        )
        if cached:
            owner.config["selection"] = dict(target, profile_id="map-original", variant_id="z" * 64)
            owner.config["preparation"] = dict(
                recipe_id="synthetic-v1",
                title="Synthetic repair",
                plan_digest="exact-plan",
                cached_profile_id=target["profile_id"],
            )
        owner.game = None
        owner.open_attempted = False
        owner.dispatch_settled = False
        owner.service = None
        owner.phases = {}
        owner.require_stopped = lambda: events.append("game-absent-check")
        owner.verify_cleanup_owner = lambda: None
        owner.write = lambda *args: None
        owner.publish = lambda *args: events.append("publish-request")
        owner.journal = SimpleNamespace(append=lambda *args: events.append("selected"))
        owner.app = SimpleNamespace(
            play=lambda *args, **kwargs: self.fail("Source app.play called")
        )

        class Service:
            def __init__(self, pilot):
                self.stdout = SimpleNamespace(closed=False)
                self.identity = Process(
                    200 + pilot.launches, 2000 + pilot.launches, "/synthetic/packaged-backend"
                )
                self.pending = None
                self.commit_sent = False
                self.requests = []
                events.append("packaged-service")

            def send(self, ident, method, params, *, commit=False):
                self.requests.append((method, params))
                self.pending = ident
                if commit:
                    self.commit_sent = True
                    pilot = owner
                    pilot.open_attempted = True
                    events.append("single-packaged-commit")

            def receive(self, *, event=None, deadline):
                if event == "ready":
                    self.ready_deadline = deadline
                    return dict(event="ready")
                if self.pending == "play-review":
                    scope = self.requests[-1][1]
                    return dict(
                        ok=True,
                        data=dict(
                            target={
                                k: scope[k]
                                for k in (
                                    "profile_id",
                                    "archive_sha256",
                                    "variant_id",
                                    "game_executable_sha256",
                                )
                            },
                            warning="Changed warning" if wrong_review else owner.config["warning"],
                            preparation=owner.config["preparation"] if cached else None,
                            prepare_compatibility=cached,
                            launch_deadline=deadline,
                            ticket="one-use",
                            fingerprint="exact",
                        ),
                    )
                expected = {
                    k: self.requests[-1][1][k]
                    for k in (
                        "profile_id",
                        "archive_sha256",
                        "variant_id",
                        "game_executable_sha256",
                    )
                }
                return response(expected)

            def settle(self):
                events.append("service-settled")
                self.stdout.closed = True

        def arm(pilot, unused):
            events.append("observer-armed")
            pilot.nonce, pilot.code = "nonce", "code"
            return dict(admission=True)

        def bind(pilot, unused):
            events.append("game-bound")
            pilot.game = Process(300 + pilot.launches, 3000 + pilot.launches, "/synthetic/game")

        owner.assert_service_closed = lambda: self.assertTrue(owner.service.stdout.closed)
        with (
            patch.object(menu, "PackagedService", Service),
            patch.object(menu, "arm_observer", side_effect=arm),
            patch.object(menu, "bind_observer", side_effect=bind),
        ):
            try:
                owner.launch()
                if two_launches:
                    first = owner.game
                    with self.assertRaisesRegex(ControlRefused, "Exact first stop"):
                        owner.launch()
                    owner.first_stop_verified = True
                    with patch.object(menu.time, "monotonic", return_value=owner.anchor + 200):
                        owner.launch()
                    self.assertEqual(owner.service.ready_deadline, owner.anchor + 210)
                    self.assertFalse(first.same(owner.game))
                    with self.assertRaisesRegex(ControlRefused, "Two launches"):
                        owner.launch()
                failure = None
            except Exception as exc:
                failure = exc
            if owner.game is None and owner.open_attempted:
                with self.assertRaisesRegex(ValueError, "observer retained"):
                    owner.stop()
            else:
                self.assertTrue(owner.stop())
        return owner, events, failure

    def test_ingress_crossing_menu_cutoff_refuses_before_attempt_but_quit_still_spawns(self):
        common = SimpleNamespace(UKCalibration=object)
        cls = menu.pilot_class(common, driver.pilot_class(common))
        owner = cls.__new__(cls)
        clock = [389.0]
        attempted = []
        owner.anchor = 0.0
        owner.menu_work_active = True
        owner.job = dict(purpose=menu.PURPOSE)
        owner.delivery_validation = None
        owner.admitted = lambda: None
        owner.mutation_guard = nullcontext
        owner.control = SimpleNamespace(
            cancelled=False, _poll_locked=lambda: clock.__setitem__(0, 391.0)
        )
        with (
            patch.object(driver.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(driver.subprocess, "Popen") as spawn,
        ):
            with self.assertRaises(menu.MenuIncomplete):
                owner.spawn_input(
                    ["synthetic-provider"],
                    deadline=480.0,
                    before_spawn=lambda: attempted.append(True),
                )
            spawn.assert_not_called()
            self.assertEqual(attempted, [])
            owner.menu_work_active = False
            owner.spawn_input(
                ["synthetic-normal-quit"],
                deadline=480.0,
                before_spawn=lambda: attempted.append(True),
            )
            spawn.assert_called_once_with(["synthetic-normal-quit"])
            self.assertEqual(attempted, [True])

    def test_actual_owner_composition_uses_package_only_and_settles_before_cleanup(self):
        owner, events, failure = self.exercise(
            lambda target: dict(
                ok=True, data=dict(target=target, launched_profile_id=target["profile_id"])
            )
        )
        self.assertIsNone(failure)
        self.assertLess(events.index("observer-armed"), events.index("single-packaged-commit"))
        self.assertLess(events.index("service-settled"), events.index("observer-cleanup"))
        self.assertEqual(events.count("single-packaged-commit"), 1)
        self.assertTrue(owner.dispatch_settled)
        method, params = owner.service.requests[-1]
        self.assertEqual(method, "maptools.play_commit")
        self.assertIs(params["prepare_compatibility"], False)
        self.assertIs(params["confirmed"], True)
        self.assertEqual(params["launch_deadline"], owner.launch_deadline)

    def test_cached_owner_selects_original_but_requires_exact_prepared_launch(self):
        owner, events, failure = self.exercise(
            lambda original: dict(
                ok=True, data=dict(target=original, launched_profile_id="map-target")
            ),
            cached=True,
        )
        self.assertIsNone(failure)
        method, params = owner.service.requests[-1]
        self.assertEqual(params["profile_id"], "map-original")
        self.assertTrue(params["prepare_compatibility"])
        self.assertTrue(owner.dispatch_settled)
        self.assertLess(events.index("service-settled"), events.index("observer-cleanup"))

    def test_two_launch_owner_keeps_both_package_commits_and_fixed_second_cutoff(self):
        owner, events, failure = self.exercise(
            lambda original: dict(
                ok=True, data=dict(target=original, launched_profile_id="map-target")
            ),
            cached=True,
            two_launches=True,
        )
        self.assertIsNone(failure)
        self.assertEqual(events.count("single-packaged-commit"), 2)
        self.assertEqual(events.count("observer-armed"), 2)
        self.assertEqual(events.count("service-settled"), 2)
        self.assertEqual(owner.launches, 2)
        self.assertEqual(owner.launch_deadline, owner.anchor + 390)
        self.assertTrue((owner.operation_directory / "launch-1/observer").is_dir())
        self.assertTrue((owner.operation_directory / "launch-2/observer").is_dir())

    def test_cached_owner_refuses_if_package_launches_original_instead_of_cache(self):
        owner, events, failure = self.exercise(
            lambda original: dict(
                ok=True, data=dict(target=original, launched_profile_id=original["profile_id"])
            ),
            cached=True,
        )
        self.assertIn("outcome unknown", str(failure))
        self.assertFalse(owner.dispatch_settled)
        self.assertNotIn("observer-cleanup", events)
        self.assertNotIn("game-bound", events)

    def test_changed_cached_review_refuses_before_observer_or_commit(self):
        owner, events, failure = self.exercise(
            lambda original: self.fail("Commit reached"), cached=True, wrong_review=True
        )
        self.assertIn("review scope drift", str(failure))
        self.assertFalse(owner.open_attempted)
        self.assertNotIn("observer-armed", events)
        self.assertNotIn("single-packaged-commit", events)

    def test_typed_predispatch_cancel_settles_service_then_observer(self):
        owner, events, failure = self.exercise(
            lambda target: dict(ok=True, data=dict(target=target, launch_dispatched=False))
        )
        self.assertRegex(str(failure), "cancelled before dispatch")
        self.assertTrue(owner.dispatch_settled)
        self.assertFalse(owner.open_attempted)
        self.assertNotIn("game-bound", events)
        self.assertLess(events.index("service-settled"), events.index("observer-cleanup"))

    def test_unknown_commit_settles_service_but_keeps_observer_and_restoration_hold(self):
        owner, events, failure = self.exercise(lambda target: dict(ok=False, error="unknown"))
        self.assertRegex(str(failure), "outcome unknown")
        self.assertFalse(owner.dispatch_settled)
        self.assertIn("service-settled", events)
        self.assertNotIn("observer-cleanup", events)
        self.assertNotIn("game-bound", events)


class MissingWindowObservationTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        probe = self.directory / "WindowProbe"
        source = self.directory / "WindowProbe.swift"
        probe.write_bytes(b"synthetic executable, never launched")
        probe.chmod(0o700)
        source.write_bytes(b"synthetic read-only source")
        binding = dict(
            executable=str(probe),
            executable_sha256=menu.sha(probe.read_bytes()),
            source=str(source),
            source_sha256=menu.sha(source.read_bytes()),
        )
        self.clock = 100.0
        self.child_calls, self.native_calls, self.records = [], [], {}
        self.spawn_calls, self.ingress_seconds = [], 0
        self.metadata = dict(
            window=501, pid=123, layer=0, bounds=dict(x=0, y=0, width=1200, height=800)
        )
        self.absent, self.recovery, self.child_seconds, self.native_seconds = False, False, 0.1, 0.1
        self.current = Process(123, 456)

        class Frozen:
            def stop(owner):
                owner.cleanup_events.append("owned-stop")
                return True

        common = SimpleNamespace(
            UKCalibration=Frozen,
            ChildFailed=ChildFailed,
            bounded_child=self.child,
            fresh_play_index=lambda *a, **k: None,
        )
        cls = menu.pilot_class(common, driver.pilot_class(common))
        self.owner = cls.__new__(cls)
        self.owner.job = dict(
            purpose=menu.LOAD_PURPOSE,
            catalogue_menu=dict(window_probe=binding),
            closure=[
                dict(path=str(p), bytes=p.stat().st_size, sha256=menu.sha(p.read_bytes()))
                for p in (probe, source)
            ],
        )
        self.owner.config = self.owner.job["catalogue_menu"]
        self.owner.anchor, self.owner.last_agent_window = 0.0, 501
        self.owner.menu_work_active = True
        self.owner.game = self.current
        self.owner.monitor = SimpleNamespace(inspect=lambda pid: self.current)
        self.owner.directory = self.directory
        self.owner.guard_running = lambda: None
        self.owner.write = lambda name, value: self.records.__setitem__(name, value)
        self.owner.native = self.native
        self.enterContext(patch.object(menu.time, "monotonic", side_effect=lambda: self.clock))
        self.enterContext(patch.object(menu.time, "sleep", side_effect=self.sleep))

    def sleep(self, seconds):
        self.clock += seconds

    def child(self, argv, directory, label, tick, timeout, mutable, *, spawn):
        tick()
        self.child_calls.append((argv, timeout, mutable))
        self.clock += self.ingress_seconds
        with patch.object(
            menu.subprocess, "Popen", side_effect=lambda *a, **k: self.spawn_calls.append(a)
        ):
            spawn(argv)
        self.clock += self.child_seconds
        tick()
        if self.absent:
            (directory / (label + ".stderr")).write_bytes(b"Exact window absent\n")
            raise ChildFailed(
                Completion(tuple(argv), 300, 3, b"", self.clock, self.clock + timeout, True)
            )
        return menu.canonical(self.metadata)

    def native(self, label, action, window, deadline):
        self.native_calls.append((label, action, window, deadline))
        self.clock += self.native_seconds
        return dict(
            result=dict(app=dict(pid=123), windows=[self.window()] if self.recovery else [])
        )

    def window(self):
        return dict(id=501, app=dict(pid=123), isOffscreen=False, isMinimized=False)

    def observe(self, deadline=480):
        return self.owner.observe_missing_window("Agent006", deadline)

    def test_two_readonly_inspections_then_stop_without_visibility_or_crash_claim(self):
        with self.assertRaisesRegex(ControlRefused, "bounded observation ended"):
            self.observe()
        self.assertEqual(len(self.child_calls), 2)
        self.assertEqual(len(self.native_calls), 1)
        self.assertLess(self.clock, 110)
        for argv, timeout, mutable in self.child_calls:
            self.assertEqual(argv[1:], ["inspect", "501"])
            self.assertLessEqual(timeout, 2)
            self.assertFalse(mutable)
        for record in self.records.values():
            self.assertEqual(record["process_before"], record["process_after"])
            self.assertEqual(record["metadata"], self.metadata)
            self.assertIn("visibility/alpha/sharing unknown", record["limit"])
        with self.assertRaisesRegex(ControlRefused, "already observed"):
            self.observe()
        self.assertEqual(len(self.child_calls), 2)

    def test_absent_old_id_stays_inconclusive_about_replacement_windows(self):
        self.absent = True
        with self.assertRaisesRegex(ControlRefused, "bounded observation ended"):
            self.observe()
        self.assertEqual(len(self.records), 2)
        self.assertTrue(all(r["metadata"] is None for r in self.records.values()))
        self.assertTrue(all("replacement windows" in r["limit"] for r in self.records.values()))

    def test_natural_recovery_reaches_actual_inherited_fresh_frame_path(self):
        self.recovery = True
        ordinary_native = self.owner.native
        initial = [True]

        def native(label, action, window, deadline):
            if initial[0]:
                initial[0] = False
                return dict(result=dict(app=dict(pid=123), windows=[]))
            return ordinary_native(label, action, window, deadline)

        self.owner.native = native
        self.owner.retain_frame = lambda *args: {}
        accepted = []
        self.owner.scene_frame = lambda receipt, frame, window: accepted.append(window)
        receipt, frame, window = self.owner.agent_frame("Agent006", 480)
        self.assertEqual(accepted, [501])
        self.assertEqual(len(self.child_calls), 1)
        self.assertEqual(window, 501)
        self.assertEqual(frame, dict(play_index=None))
        self.assertEqual(self.native_calls[-1][1:3], (["get-app-state"], 501))
        self.assertFalse(self.records["Agent006WindowRecovered.json"]["automatic_focus"])

    def test_original_deadline_and_work_cutoff_never_extend_ten_second_cap(self):
        self.child_seconds = 0.6
        with self.assertRaisesRegex(ControlRefused, "cap expired"):
            self.observe(100.5)
        self.assertEqual(len(self.child_calls), 1)
        self.assertEqual(self.native_calls, [])

    def test_work_cutoff_refuses_before_any_probe(self):
        self.clock = 390
        with self.assertRaisesRegex(ControlRefused, "cap expired"):
            self.observe()
        self.assertEqual(self.child_calls, [])

    def test_final_readonly_spawn_rechecks_cap_after_child_ingress(self):
        self.ingress_seconds = 11
        with self.assertRaisesRegex(ControlRefused, "cap expired"):
            self.observe()
        self.assertEqual(self.spawn_calls, [])
        self.assertEqual(self.native_calls, [])

    def test_late_native_followup_cannot_authorize_recovery(self):
        self.recovery, self.native_seconds = True, 9
        with self.assertRaisesRegex(ControlRefused, "cap expired"):
            self.observe()
        self.assertNotIn("Agent006WindowRecovered.json", self.records)

    def test_wrong_process_or_metadata_refuses_without_followup(self):
        self.current = Process(124, 999)
        with self.assertRaisesRegex(ControlRefused, "process drift"):
            self.observe()
        self.assertEqual(self.child_calls, [])
        self.owner.window_loss_observed = False
        self.current = self.owner.game
        self.metadata["pid"] = 999
        with self.assertRaisesRegex(ControlRefused, "metadata required"):
            self.observe()
        self.assertEqual(self.native_calls, [])

    def test_probe_purpose_and_closure_drift_refuse(self):
        self.owner.job["purpose"] = menu.SAVE_PURPOSE
        with self.assertRaisesRegex(ControlRefused, "load-only"):
            self.observe()
        self.owner.window_loss_observed = False
        self.owner.job["purpose"] = menu.LOAD_PURPOSE
        Path(self.owner.config["window_probe"]["source"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ControlRefused, "closure drift"):
            self.observe()
        self.assertEqual(self.child_calls, [])

    def test_no_probe_no_prior_window_and_normal_quit_keep_existing_path(self):
        self.owner.menu_work_active = False
        self.assertIsNone(self.observe())
        self.owner.menu_work_active = True
        del self.owner.last_agent_window
        self.assertIsNone(self.observe())
        self.owner.config.clear()
        self.assertIsNone(self.observe())
        self.assertEqual(self.child_calls, [])

    def complete_load(self, recovery):
        fixture = CatalogueRestoreTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        owner = self.owner
        for name in (
            "app",
            "profiles",
            "preimages",
            "baseline",
            "control",
            "journal",
            "require_stopped",
            "verify_cleanup_owner",
            "require_settled_input",
        ):
            setattr(owner, name, getattr(fixture.owner, name))
        owner.job["return_profile"] = fixture.owner.job["return_profile"]
        owner.config.update(fixture.owner.config, scenario_title="Synthetic Canyon")
        owner.service, owner.open_attempted, owner.dispatch_settled = None, True, True
        owner.cleanup_events = []
        owner.prepare = lambda: None
        owner.launch = lambda: None
        owner.persist_outcome = lambda outcome: owner.cleanup_events.append("persist")
        owner.finish = lambda *args: owner.cleanup_events.append("terminal")
        owner.normal_quit = lambda *args: owner.cleanup_events.append("normal-quit")
        self.recovery = recovery
        ordinary_native, first = owner.native, [True]

        def native(label, action, window, deadline):
            if first[0]:
                first[0] = False
                return dict(result=dict(app=dict(pid=123), windows=[]))
            return ordinary_native(label, action, window, deadline)

        owner.native = native
        owner.retain_frame = lambda *args: {}
        owner.scene_frame = lambda *args: None

        def stage(name, instruction):
            self.assertIn(name, ("launcher", "world"))
            if name == "world":
                owner.agent_frame("Agent006", 480)
            return dict(stage=name, observation="Synthetic world, no runtime credit")

        owner.decisions = SimpleNamespace(stage=stage)
        result = driver.run_calibration(owner)
        self.assertTrue(result["stopped"] and result["restored"] and result["terminal_finished"])
        self.assertEqual(owner.profiles._state()["active"], owner.job["return_profile"])
        self.assertEqual(menu.tree_files(owner.profiles.live), owner.baseline["return_files"])
        target = owner.profiles._profile_path(owner.config["target"]["profile_id"])
        self.assertEqual((target / "Saves/existing.sav").read_bytes(), b"existing target save")
        self.assertEqual((target / "Saves/new.sav").read_bytes(), b"new save remains")
        return result, owner.cleanup_events

    def test_first_empty_list_flows_through_real_finite_cleanup_and_profile_restoration(self):
        result, events = self.complete_load(False)
        self.assertEqual(result["outcome"]["status"], "automation_failed")
        self.assertIn("bounded observation ended", result["outcome"]["cause"])
        self.assertEqual(events, ["persist", "owned-stop", "terminal"])
        self.assertEqual(len(self.child_calls), 2)
        self.assertEqual(len(self.native_calls), 1)

    def test_first_empty_list_naturally_recovers_then_load_normal_quit_without_save(self):
        result, events = self.complete_load(True)
        self.assertEqual(events, ["normal-quit", "persist", "owned-stop", "terminal"])
        self.assertFalse(result["outcome"]["save_reopen_credit"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(self.child_calls), 1)

    def test_lost_window_helper_cutoff_does_not_reenter_normal_quit_polling(self):
        self.clock, self.ingress_seconds = 389, 2

        def guard():
            if self.clock >= 390:
                raise menu.MenuIncomplete("Menu discovery cutoff reached")

        self.owner.guard_running = guard
        result, events = self.complete_load(False)
        self.assertEqual(events, ["persist", "owned-stop", "terminal"])
        self.assertIn("Lost-window observation cap expired", result["outcome"]["cause"])
        self.assertEqual(self.spawn_calls, [])

    def test_lost_window_native_followup_cutoff_keeps_owned_cleanup_without_quit_retry(self):
        def native(*args):
            self.clock = 390
            raise menu.MenuIncomplete("Menu discovery cutoff reached")

        self.owner.native = native
        result, events = self.complete_load(False)
        self.assertEqual(events, ["persist", "owned-stop", "terminal"])
        self.assertIn("Lost-window observation cap expired", result["outcome"]["cause"])
        self.assertEqual(len(self.child_calls), 1)


if __name__ == "__main__":
    unittest.main()
