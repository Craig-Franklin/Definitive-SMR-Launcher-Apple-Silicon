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
from test_agent_driver import Process
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

    def test_save_pilot_never_admits_named_or_unproven_old_saves(self):
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


if __name__ == "__main__":
    unittest.main()
