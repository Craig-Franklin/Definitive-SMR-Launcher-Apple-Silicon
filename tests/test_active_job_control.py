"""Synthetic native-message and finite-controller cancellation checks only."""

from __future__ import annotations

import copy
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/research"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import active_job_control as control


SESSION = "00000000-0000-4000-8000-000000000009"
NONCE = "00000000-0000-4000-8000-000000000010"
AT = datetime(2026, 10, 13, 23, 0, tzinfo=timezone.utc)


def encoded(row):
    return (json.dumps(row) + "\n").encode()


def user(text, *, seconds=1, role="user", kinds=None):
    when = datetime.fromtimestamp(AT.timestamp() + seconds, timezone.utc)
    return dict(
        type="response_item",
        timestamp=when.isoformat(),
        payload=dict(
            type="message",
            role=role,
            id="msg_synthetic_" + str(seconds),
            content=[dict(type="input_text", text=text)],
            internal_chat_message_metadata_passthrough=dict(
                turn_id="synthetic-turn",
                create_time=when.timestamp(),
                content_item_kinds=["user.text"] if kinds is None else kinds,
            ),
        ),
    )


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.rollout = self.directory / (SESSION + ".jsonl")
        prefix = encoded(
            dict(
                type="session_meta", payload=dict(id=SESSION, source="cli", originator="codex-tui")
            )
        ) + encoded(user("I'm playing", seconds=-1))
        self.rollout.write_bytes(prefix)
        root = dict(
            run="run_000000000009", consumer_generation=9, session=SESSION, coordinator="term_owner"
        )
        self.job = dict(
            orca="/synthetic/orca",
            job=f"finite-uk-calibration-{SESSION}-generation9-{NONCE}",
            root=root,
            active_control=dict(
                nonce=NONCE,
                start_offset=len(prefix),
                prefix_sha256=control.sha(prefix),
                not_before=AT.isoformat(),
                stream_identity={
                    "device": self.rollout.stat().st_dev,
                    "inode": self.rollout.stat().st_ino,
                },
            ),
            actor_binding=dict(
                root=root,
                root_actor=dict(session=SESSION, terminal="term_owner", rollout=str(self.rollout)),
            ),
            availability_path=str(self.directory / "STATE.json"),
        )
        self.state_path = Path(self.job["availability_path"])
        self.state = dict(
            paused=False,
            protected_unrelated={"sentinel": [1, 2, 3]},
            effective_continuation=dict(
                root,
                status="ACTIVE_ONE_FINITE_CALIBRATION_WAVE",
                model="gpt-6.1-sol",
                effort="xhigh",
                approval_policy="never",
                sandbox="danger-full-access",
                runtime_automation_allowed=True,
                runtime_admitted=True,
                admitted_job=self.job["job"],
                exclusive_runtime_owner=root["coordinator"],
                closed_circuits_unchanged=True,
                manual_collector_allowed=False,
                capacity_recovery_enabled=False,
            ),
            game_availability=dict(status="AVAILABLE", updated_at=AT.isoformat()),
            active_job_control=dict(
                binding=control.binding_for(self.job),
                phase="active",
                cancelled=False,
                cursor=len(prefix),
                prefix_sha256=control.sha(prefix),
            ),
        )
        self.save(self.state)

        def native_run(argv, **kwargs):
            return encoded(
                {
                    "ok": True,
                    "result": {
                        "run": {
                            "consumer_generation": self.job["root"]["consumer_generation"],
                            "coordinator_handle": self.job["root"]["coordinator"],
                        }
                    },
                    "_meta": {"runtimeId": self.job["actor_binding"].get("provider_runtime_id")},
                }
            )

        self.enterContext(patch.object(control.subprocess, "check_output", side_effect=native_run))
        self.control = control.ActiveJobControl(self.job)

    def save(self, value):
        self.state_path.write_bytes(encoded(value))

    def read(self):
        return json.loads(self.state_path.read_bytes())

    def append(self, row):
        with self.rollout.open("ab") as stream:
            stream.write(encoded(row))
            stream.flush()
            os.fsync(stream.fileno())


class NativeControlTests(Fixture):
    def test_large_native_image_record_does_not_hide_following_handback(self):
        self.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": "synthetic image data " + "x" * (2 * 1024 * 1024),
                },
            }
        )
        self.control.poll()
        self.assertFalse(self.control.cancelled)
        self.assertEqual(self.read()["active_job_control"]["cursor"], self.rollout.stat().st_size)
        self.append(user("I'm playing"))
        with self.assertRaises(control.UserCancelled):
            self.control.poll()
        self.assertTrue(self.read()["active_job_control"]["cancelled"])

    def test_oversized_authoritative_control_record_still_refuses(self):
        row = user("I'm playing")
        row["padding"] = "x" * (2 * 1024 * 1024)
        self.append(row)
        with self.assertRaisesRegex(control.ControlRefused, "Native control record size bound"):
            self.control.poll()

    def test_standalone_commands_and_only_documented_surface(self):
        for text in ("I'm playing", "I want to play.", "I’M PLAYING!"):
            self.assertEqual(control.command(user(text))[0], "PLAYING")
        for text in ("I'm done playing", "You can use the game."):
            self.assertEqual(control.command(user(text))[0], "AVAILABLE")
        for text in (
            'Example: "I\'m playing"',
            '"I\'m playing"',
            "`I'm playing`",
            "> I'm playing",
            "I'm playing\nYou can use the game",
            "I'm not playing",
            "I want to play?",
            "Please say I'm playing",
            "I'm playing now",
            '{"command":"I\'m playing"}',
        ):
            self.assertIsNone(control.command(user(text)))

    def test_history_tools_assistant_examples_and_duplicate_event_do_not_trigger(self):
        self.append(user("I'm playing", role="assistant"))
        self.append(user("I'm playing", role="tool"))
        self.append(user("I'm playing", kinds=["agents_md.instructions"]))
        self.append(user("I'm playing", kinds=["user.text", "user.image"]))
        self.append(dict(type="event_msg", payload=dict(type="UserMessage", content="I'm playing")))
        self.append(user("Example: I'm playing"))
        self.append(user("I'm playing", seconds=-1))
        self.control.poll()
        self.assertFalse(self.read()["active_job_control"]["cancelled"])
        self.assertEqual(self.read()["game_availability"]["status"], "AVAILABLE")

    def test_native_playing_is_durable_and_latched_across_available_and_restart(self):
        self.append(user("I'm playing"))
        with self.assertRaises(control.UserCancelled):
            self.control.poll()
        state = self.read()
        self.assertEqual(state["game_availability"]["status"], "PLAYING")
        self.assertTrue(state["active_job_control"]["cancelled"])
        self.append(user("I'm done playing", seconds=2))
        restarted = control.ActiveJobControl(self.job)
        with self.assertRaises(control.UserCancelled):
            restarted.poll()
        state = self.read()
        self.assertEqual(state["game_availability"]["status"], "AVAILABLE")
        self.assertTrue(state["active_job_control"]["cancelled"])
        self.assertEqual(state["protected_unrelated"], {"sentinel": [1, 2, 3]})
        self.assertNotIn("I'm playing", self.state_path.read_text())

    def test_newer_durable_playing_survives_restart_and_older_available(self):
        self.state["game_availability"] = dict(
            status="PLAYING", updated_at="2026-10-13T23:00:30+00:00"
        )
        self.save(self.state)
        self.append(user("You can use the game", seconds=2))
        with self.assertRaises(control.UserCancelled):
            control.ActiveJobControl(self.job).poll()
        self.assertEqual(self.read()["game_availability"]["status"], "PLAYING")

    def test_partial_record_waits_for_newline_and_malformed_complete_refuses(self):
        row = encoded(user("I'm playing"))
        with self.rollout.open("ab") as stream:
            stream.write(row[:-1])
        self.control.poll()
        self.assertFalse(self.read()["active_job_control"]["cancelled"])
        with self.rollout.open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaises(control.UserCancelled):
            self.control.poll()
        with self.rollout.open("ab") as stream:
            stream.write(b'{"type":\n')
        before = self.state_path.read_bytes()
        with self.assertRaises(control.ControlRefused):
            self.control.poll(cancel=False)
        self.assertEqual(before, self.state_path.read_bytes())

    def test_session_prefix_replacement_and_truncation_refuse(self):
        original = self.rollout.read_bytes()
        for changed in (original.replace(SESSION.encode(), b"0" * len(SESSION)), original[:-2]):
            self.rollout.write_bytes(changed)
            with self.assertRaises(control.ControlRefused):
                self.control.poll()
        self.rollout.write_bytes(original)
        self.control.poll()

    def test_owner_job_generation_unknown_and_malformed_state_refuse_without_writes(self):
        for section, key, value in (
            ("effective_continuation", "session", "stale-session"),
            ("effective_continuation", "consumer_generation", True),
            ("effective_continuation", "coordinator", "term_replacement"),
            ("effective_continuation", "admitted_job", "old-job"),
            ("game_availability", "status", "UNKNOWN"),
            ("active_job_control", "cancelled", "false"),
            ("active_job_control", "phase", "closed"),
        ):
            state = copy.deepcopy(self.state)
            state[section][key] = value
            self.save(state)
            before = self.state_path.read_bytes()
            self.append(user("I'm playing"))
            with self.assertRaises(control.ControlRefused):
                self.control.poll()
            self.assertEqual(before, self.state_path.read_bytes())

    def test_prefix_cursor_rollback_and_latch_loss_refuse(self):
        self.append(user("I'm playing"))
        with self.assertRaises(control.UserCancelled):
            self.control.poll()
        self.save(self.state)
        with self.assertRaises(control.ControlRefused):
            self.control.poll()

    def test_busy_lock_and_observed_foreign_write_refuse(self):
        # Freeze the coordinator's existing lock filename independently of the
        # implementation, so a second lock protocol cannot pass this check.
        with self.state_path.with_name("STATE.json.lock").open("wb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(control.ControlRefused):
                self.control.poll()
        old = self.state_path.read_bytes()
        self.state["game_availability"]["status"] = "PLAYING"
        self.save(self.state)
        with control.state_lock(self.state_path):
            with self.assertRaises(control.ControlRefused):
                control.replace_state(self.state_path, old, {})
        self.assertEqual(self.read()["game_availability"]["status"], "PLAYING")

    def test_cleanup_guard_holds_owner_through_mutation_and_allows_playing(self):
        self.state["game_availability"]["status"] = "PLAYING"
        self.save(self.state)
        with self.control.cleanup_guard():
            with self.assertRaises(control.ControlRefused):
                with control.state_lock(self.state_path):
                    self.fail("Owner writer entered during exact cleanup")
        self.state["effective_continuation"]["coordinator"] = "term_replaced"
        self.save(self.state)
        with self.assertRaises(control.ControlRefused):
            with self.control.cleanup_guard():
                self.fail("Replacement must refuse cleanup")

    def test_native_clock_mismatch_and_duplicate_json_key_refuse(self):
        row = user("I'm playing")
        row["payload"]["internal_chat_message_metadata_passthrough"]["create_time"] = 0
        self.append(row)
        with self.assertRaises(control.ControlRefused):
            self.control.poll()
        with self.assertRaises(control.ControlRefused):
            control.decode(b'{"type":1,"type":2}')

    def test_old_job_or_foreign_actor_cannot_be_substituted(self):
        for changed in (
            dict(self.job, job="finite-uk-calibration-20261013-generation8"),
            dict(self.job, root=dict(self.job["root"], consumer_generation=8)),
            dict(self.job, actor_binding=dict(self.job["actor_binding"], root={})),
        ):
            with self.assertRaises(control.ControlRefused):
                control.ActiveJobControl(changed)

    def test_coordinator_drain_uses_same_native_path_and_refuses_foreign_caller_or_pin(self):
        job_path = self.directory / "fresh-job.json"
        raw = encoded(self.job)
        job_path.write_bytes(raw)
        self.append(user("I'm playing"))
        argv = ["active_job_control.py", "--job", str(job_path), "--job-sha256", control.sha(raw)]
        with (
            patch.object(sys, "argv", argv),
            patch.dict(
                os.environ,
                {
                    "CODEX_THREAD_ID": SESSION,
                    "ORCA_TERMINAL_HANDLE": "term_owner",
                },
            ),
            patch("builtins.print"),
        ):
            self.assertEqual(control.main(), 0)
            self.assertTrue(self.read()["active_job_control"]["cancelled"])
            with patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": "term_worker"}):
                with self.assertRaises(control.ControlRefused):
                    control.main()
        with patch.object(sys, "argv", argv[:-1] + ["0" * 64]):
            with self.assertRaises(control.ControlRefused):
                control.main()

    def test_stream_device_inode_survives_append_but_refuses_replacement_after_restart(self):
        self.append(user("You can use the game"))
        self.control.poll()
        replacement = self.directory / "replacement.jsonl"
        replacement.write_bytes(self.rollout.read_bytes() + encoded(user("I'm playing", seconds=2)))
        replacement.replace(self.rollout)
        before = self.state_path.read_bytes()
        with self.assertRaises(control.ControlRefused):
            control.ActiveJobControl(self.job).poll()
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_native_run_generation_coordinator_failure_and_timeout_refuse_without_writes(self):
        good = {
            "ok": True,
            "result": {"run": {"consumer_generation": 9, "coordinator_handle": "term_owner"}},
        }
        responses = [dict(good, ok=False)]
        for key, value in (
            ("consumer_generation", 10),
            ("consumer_generation", True),
            ("coordinator_handle", "term_replaced"),
        ):
            response = copy.deepcopy(good)
            response["result"]["run"][key] = value
            responses.append(response)
        before = self.state_path.read_bytes()
        for response in responses:
            with (
                patch.object(control.subprocess, "check_output", return_value=encoded(response)),
                self.assertRaises(control.ControlRefused),
            ):
                self.control.verify_owner()
            self.assertEqual(self.state_path.read_bytes(), before)
        with patch.object(
            control.subprocess,
            "check_output",
            side_effect=control.subprocess.TimeoutExpired("synthetic-run-show", 3),
        ) as query:
            with self.assertRaises(control.ControlRefused):
                self.control.verify_owner()
            self.assertEqual(query.call_args.kwargs["timeout"], 3)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_emergency_owner_check_does_not_parse_native_or_write_state(self):
        with self.rollout.open("ab") as stream:
            stream.write(b'{"malformed":\n')
        before = self.state_path.read_bytes()
        with patch.object(
            control, "replace_state", side_effect=OSError("synthetic disk failure")
        ) as write:
            self.control.verify_owner()
            with self.control.cleanup_guard():
                pass
            write.assert_not_called()
        self.assertEqual(self.state_path.read_bytes(), before)
        self.state["effective_continuation"]["coordinator"] = "term_replaced"
        self.save(self.state)
        with self.assertRaises(control.ControlRefused):
            self.control.verify_owner()

    def test_mutation_guard_excludes_state_writer_but_does_not_block_later_ingress(self):
        with self.control.mutation_guard():
            with self.assertRaises(control.ControlRefused), control.state_lock(self.state_path):
                self.fail("Writer entered immediate mutation fence")
        self.append(user("I'm playing"))
        with self.assertRaises(control.UserCancelled):
            self.control.poll()
        with self.assertRaises(control.UserCancelled), self.control.mutation_guard():
            self.fail("Cancelled job entered mutation fence")

    def test_project_pause_or_unknown_pause_refuses_new_mutation(self):
        for value in (True, None, 0, "false", "missing"):
            with self.subTest(paused=value):
                state = copy.deepcopy(self.state)
                if value == "missing":
                    state.pop("paused")
                else:
                    state["paused"] = value
                self.save(state)
                before = self.state_path.read_bytes()
                with self.assertRaisesRegex(
                    control.ControlRefused, "Project pause forbids new runtime mutations"
                ):
                    with self.control.mutation_guard():
                        self.fail("Paused or unknown project entered mutation fence")
                self.assertEqual(self.state_path.read_bytes(), before)

    def test_unpaused_job_preserves_stopped_historical_productivity_control(self):
        self.state["work_budget"] = {"status": "stopped", "id": "historical-do-not-reset"}
        self.save(self.state)
        before = self.state_path.read_bytes()
        with self.control.mutation_guard():
            pass
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_paused_project_still_allows_exact_owned_cleanup_and_finalization(self):
        self.state["paused"] = True
        self.save(self.state)
        with self.control.cleanup_guard():
            with self.assertRaises(control.ControlRefused), control.state_lock(self.state_path):
                self.fail("Owner writer entered paused cleanup")
        with self.control.finalization_guard():
            pass
        self.assertTrue(self.read()["paused"])

    def test_paused_project_still_persists_native_handback_before_refusal(self):
        self.state["paused"] = True
        self.save(self.state)
        self.append(user("I'm playing"))
        with self.assertRaises(control.UserCancelled), self.control.mutation_guard():
            self.fail("Paused cancelled job entered mutation fence")
        self.assertTrue(self.read()["active_job_control"]["cancelled"])
        self.assertEqual(self.read()["game_availability"]["status"], "PLAYING")


class ControllerIntegrationTests(Fixture):
    @classmethod
    def setUpClass(cls):
        candidate = os.environ.get("SMR_ACTIVE_JOB_CANDIDATE")
        if not candidate:
            raise unittest.SkipTest("Private controller checks require SMR_ACTIVE_JOB_CANDIDATE")
        path = Path(candidate)
        spec = importlib.util.spec_from_file_location("active_control_candidate", path)
        cls.target = importlib.util.module_from_spec(spec)
        with (
            patch.object(sys, "path", [str(path.parent), *sys.path]),
            patch.dict(sys.modules, {spec.name: cls.target}),
        ):
            spec.loader.exec_module(cls.target)

    def setUp(self):
        super().setUp()
        # Independent synthetic pins exercise the real contract and file checks.
        for name in ("helper", "proof", "release-match"):
            (self.directory / name).write_bytes(("synthetic " + name + "\n").encode())
        self.preinput_contract = {
            "schema": 1,
            "scope": "synthetic-closed-click-before-input-v1",
            "native_helper_path": str(self.directory / "helper"),
            "native_helper_sha256": "900dfd375b15d2f9254cd800fb12598ecaa756d40420f2ce32c2e9d6298c09c8",
            "native_source_blob": "1" * 40,
            "native_source_sha256": "2" * 64,
            "proof_path": str(self.directory / "proof"),
            "proof_sha256": "7f344bb1a5f55f5bd9a9b556a8983f14db20fc4c219c2038e533a3c11a16b6e2",
            "release_match_path": str(self.directory / "release-match"),
            "release_match_sha256": "75b44125b0c240332d54fd8ee77890130507b7e660037a0e29cd0da7d56cf969",
            "binary_reproducible_build_proven": False,
        }
        self.assertEqual(
            {key: type(value) for key, value in self.target.PREINPUT_CONTRACT.items()},
            {key: type(value) for key, value in self.preinput_contract.items()},
        )
        self.enterContext(patch.object(self.target, "PREINPUT_CONTRACT", self.preinput_contract))

    def adapter(self):
        a = self.target.UKCalibration.__new__(self.target.UKCalibration)
        a.control = self.control
        a.job = dict(
            self.job,
            orca=sys.executable,
            preinput_refusal_contract=copy.deepcopy(self.preinput_contract),
        )
        a.directory = self.directory
        a.anchor = time.monotonic()
        a.launch_deadline = a.anchor + 120
        a.observer = None
        a.sample = Mock()
        a.verify_code = Mock()
        a.verify_provider = Mock()
        a.game = SimpleNamespace(pid=999999)
        a.frames = 0
        a.last_frame_complete = time.monotonic()
        a.uncertain_input = False
        a.phases = {}
        a.creation = []
        a.write = Mock()
        return a

    def writer(self):
        # No model callback. A separate synthetic writer simulates native ingress.
        def write():
            limit = time.monotonic() + 3
            while not (self.directory / "Blocked-child-created.json").exists():
                if time.monotonic() > limit:
                    return
                time.sleep(0.01)
            self.append(user("I'm playing"))

        thread = threading.Thread(target=write)
        thread.start()
        self.addCleanup(thread.join, 4)

    def blocked_provider(self, a, mutable):
        original = self.target.bounded_child

        def synthetic(argv, directory, label, tick, timeout, mutable=False, *, spawn=None):
            return original(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                directory,
                label,
                tick,
                timeout,
                mutable,
                spawn=spawn,
            )

        with patch.object(self.target, "bounded_child", side_effect=synthetic):
            action = ["click", "--element-index", "1"] if mutable else ["get-app-state"]
            return a.provider("Blocked", action, mutable=mutable)

    def test_blocked_read_cancels_reaps_and_completes_honest_cleanup_in_order(self):
        a = self.adapter()
        events = []
        a.prepare = lambda: events.append("prepare")
        a.launch = lambda: events.append("launch")
        a.observe = lambda: self.blocked_provider(a, False)
        a.persist_outcome = lambda value: events.append(("outcome", value["cause"]))
        a.stop = lambda: events.append("exact_stop") or True
        a.restore = lambda: events.append("preservation_restore") or True
        a.finish = lambda *args: events.append("terminal_disposition")
        self.writer()
        before = time.monotonic()
        result = self.target.run_calibration(a)
        self.assertLess(time.monotonic() - before, 3)
        self.assertEqual(
            events,
            [
                "prepare",
                "launch",
                ("outcome", "user_requested_game_handback"),
                "exact_stop",
                "preservation_restore",
                "terminal_disposition",
            ],
        )
        self.assertTrue(result["terminal_finished"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["outcome"]["status"], "safety_stop")
        self.assertFalse(result["outcome"]["gameplay_credit"])
        exit_receipt = json.loads((self.directory / "Blocked-child-exit.json").read_bytes())
        self.assertTrue(exit_receipt["reaped"])
        self.assertIsNotNone(exit_receipt["owned_wait_returncode"])

    def test_mutable_cancellation_keeps_pending_refuses_restoration_and_new_input(self):
        a = self.adapter()
        self.writer()
        with self.assertRaises(control.UserCancelled):
            self.blocked_provider(a, True)
        self.assertTrue(a.uncertain_input)
        self.append(user("You can use the game", seconds=2))
        with self.assertRaises(control.UserCancelled):
            a.provider("Forbidden", ["click"], mutable=True)
        self.assertFalse((self.directory / "Forbidden-child-created.json").exists())
        # Actual restore guard must refuse before any profile/preservation action.
        with patch.dict(
            sys.modules,
            {
                "smr_launcher.activation": SimpleNamespace(_fsync_dir=Mock()),
                "smr_launcher.resource_identity": SimpleNamespace(fingerprint_resources=Mock()),
            },
        ):
            with self.assertRaisesRegex(self.target.Refused, "Unsettled mutable"):
                a.restore()
        a.prepare = lambda: None
        a.launch = lambda: None
        a.observe = lambda: a.verify_available()
        a.persist_outcome = Mock()
        a.stop = lambda: True
        a.restore = lambda: False
        a.finish = Mock()
        result = self.target.run_calibration(a)
        self.assertFalse(result["terminal_finished"])
        a.finish.assert_not_called()

    def test_cancellation_between_final_check_and_spawn_delivers_no_input_or_false_uncertainty(
        self,
    ):
        a = self.adapter()
        original = a.verify_available
        polls = []

        def gate():
            original()
            polls.append(True)
            if len(polls) == 3:
                self.append(user("I'm playing"))

        a.verify_available = gate
        with (
            patch.object(self.target.subprocess, "Popen") as spawn,
            self.assertRaises(control.UserCancelled),
        ):
            a.provider("BeforeSpawn", ["click"], mutable=True)
        spawn.assert_not_called()
        self.assertFalse(a.uncertain_input)
        self.assertTrue(self.read()["active_job_control"]["cancelled"])

    def test_launch_refusal_before_spawn_does_not_claim_asynchronous_request(self):
        a = self.adapter()
        a.open_attempted = False

        def attempted():
            a.open_attempted = True

        self.append(user("I'm playing"))
        with (
            patch.object(self.target.subprocess, "Popen") as spawn,
            self.assertRaises(control.UserCancelled),
        ):
            a.spawn_input(
                ["/synthetic/open"], deadline=time.monotonic() + 1, before_spawn=attempted
            )
        spawn.assert_not_called()
        self.assertFalse(a.open_attempted)

    def test_replaced_owner_refuses_game_stop_and_settles_only_owned_observer(self):
        a = self.adapter()
        state = self.read()
        state["effective_continuation"]["coordinator"] = "term_replacement"
        self.save(state)
        a.monitor = Mock()
        a.observer = Mock()
        a.observer.poll.side_effect = [None]
        a.helper_log = (self.directory / "helper.log").open("wb")
        with self.assertRaises(control.ControlRefused):
            a.stop()
        a.monitor.stop.assert_not_called()
        a.monitor.inspect.assert_not_called()
        a.observer.terminate.assert_called_once()
        a.observer.wait.assert_called_once_with(timeout=3)
        self.assertTrue(a.helper_log.closed)

    def test_replaced_game_identity_refuses_stop_and_restore(self):
        a = self.adapter()
        a.game = SimpleNamespace(pid=99, same=lambda other: False)
        a.monitor = Mock()
        a.monitor.inspect.return_value = SimpleNamespace(
            pid=99, birth_us=2, executable="/replacement"
        )
        with self.assertRaisesRegex(self.target.Refused, "Exact game identity changed"):
            a.stop()
        a.monitor.stop.assert_not_called()

    def test_actual_stop_restore_journal_and_final_disposition_after_blocked_read(self):
        from runner_journal import RunJournal

        a = self.adapter()
        events, running, active = [], [True], ["diagnostic"]
        game = SimpleNamespace(pid=99, birth_us=123, executable="/synthetic/game")
        game.same = lambda other: other is game
        a.game = game
        a.open_attempted = True
        a.app = SimpleNamespace(installation=SimpleNamespace(game_running=lambda: running[0]))

        def stop_owned(identity):
            self.assertIs(identity, game)
            events.append("exact_owned_stop")
            running[0] = False

        a.monitor = SimpleNamespace(
            inspect=lambda pid: game if running[0] else None,
            stop=stop_owned,
            games=lambda: [game] if running[0] else [],
        )
        live = self.directory / "synthetic-profile"
        (live / "Saves").mkdir(parents=True)
        retained_save = live / "Saves/new-future-save.smr"
        retained_save.write_bytes(b"synthetic future save must remain intact")
        a.diagnostic = "diagnostic"
        a.job.update(
            return_profile="return", source_profile="canonical", installed_assets_identity="assets"
        )

        def switch(profile, *, mutation_guard=None):
            with mutation_guard() if mutation_guard is not None else nullcontext():
                self.assertFalse(running[0])
                self.assertEqual(profile, "return")
                events.append("restore_profile")
                active[0] = profile

        a.profiles = SimpleNamespace(
            journal=self.directory / "absent-activation-journal",
            live=live,
            _state=lambda: dict(active=active[0]),
            _profile_path=lambda profile: live,
            switch=switch,
        )
        manifest = {"Saves/new-future-save.smr": control.sha(retained_save.read_bytes())}
        a.manifest = lambda path: manifest
        a.metadata = lambda path: [{"mode": 0o600, "mtime_ns": 1}]
        a.baseline = {
            role: dict(manifest=manifest, metadata=a.metadata(live))
            for role in ("live", "canonical", "stock")
        }
        a.original_paths = {"canonical": live, "stock": live}
        a.installed_assets = self.directory / "synthetic-assets"
        a.preferences = []
        journal_dir = self.directory / "journal"
        journal_dir.mkdir()
        a.journal = RunJournal(journal_dir)
        a.journal.append(
            "started",
            dict(
                run_id=self.job["job"],
                input_identity="synthetic-input",
                engine_input_identity="synthetic-engine",
                binding=self.job["root"],
                name="Synthetic calibration",
                scenario="synthetic",
                run_directory=str(journal_dir),
            ),
        )
        a.journal.append("terminal_pending", dict(one_use=True))
        a.write = self.target.UKCalibration.write.__get__(a)
        a.prepare = lambda: events.append("prepare")
        a.launch = lambda: events.append("launch")
        a.observe = lambda: self.blocked_provider(a, False)
        self.writer()
        with patch.dict(
            sys.modules,
            {
                "frozen_mac_processes": SimpleNamespace(
                    MacProcesses=Mock(side_effect=AssertionError("native inspection forbidden"))
                ),
                "smr_launcher.resource_identity": SimpleNamespace(
                    fingerprint_resources=lambda roots: dict(identity="assets")
                ),
            },
        ):
            result = self.target.run_calibration(a)
        self.assertTrue(result["terminal_finished"], result["errors"])
        self.assertEqual(events, ["prepare", "launch", "exact_owned_stop", "restore_profile"])
        records = a.journal.records()
        self.assertEqual(records["raw_outcome"]["payload"]["cause"], "user_requested_game_handback")
        self.assertEqual(records["stopped"]["previous"], records["raw_outcome"]["id"])
        self.assertEqual(records["restored"]["previous"], records["raw_outcome"]["id"])
        self.assertIn("terminal_finished", records)
        completion = json.loads((self.directory / "completion-ready.json").read_bytes())
        self.assertTrue(completion["cancelled"])
        self.assertFalse(completion["calibration_frames_observed"])
        self.assertEqual(completion["terminal_outcome"]["cause"], "user_requested_game_handback")
        self.assertEqual(retained_save.read_bytes(), b"synthetic future save must remain intact")

    def test_outcome_persistence_failure_still_stops_and_keeps_terminal_pending(self):
        events = []

        def fail(value):
            events.append("outcome_failed")
            raise OSError("synthetic full disk")

        a = SimpleNamespace(
            prepare=lambda: None,
            launch=lambda: None,
            observe=lambda: (_ for _ in ()).throw(control.UserCancelled("synthetic cancellation")),
            persist_outcome=fail,
            stop=lambda: events.append("stop") or True,
            restore=Mock(),
            finish=Mock(),
        )
        result = self.target.run_calibration(a)
        self.assertEqual(events, ["outcome_failed", "stop"])
        self.assertFalse(result["terminal_finished"])
        a.restore.assert_not_called()
        a.finish.assert_not_called()

    def test_final_index_and_terminal_append_exclude_cooperative_owner_writer(self):
        a = self.adapter()
        historical = {"status": "safety_stop", "cause": "provider_calibration_frame_observed"}
        checked = []

        def append(phase, value):
            with self.assertRaises(control.ControlRefused), control.state_lock(self.state_path):
                self.fail("Owner writer entered final terminal barrier")
            checked.append(phase)

        a.journal = SimpleNamespace(append=append)
        with patch.dict(
            sys.modules, {"runner_journal": SimpleNamespace(result_row=lambda journal: historical)}
        ):
            a.finish(dict(historical), True, True)
        self.assertEqual(checked, ["terminal_finished"])

    def test_late_cleanup_command_changes_terminal_disposition_without_rewriting_raw_facts(self):
        a = self.adapter()
        historical = dict(status="safety_stop", cause="provider_calibration_frame_observed")
        outcome = dict(historical)
        writes, events = {}, []
        a.write = lambda name, value: (
            writes.update({name: copy.deepcopy(value)}),
            events.append(name),
        )
        a.journal = SimpleNamespace(append=lambda phase, value: events.append(phase))
        self.append(user("I'm playing"))
        with patch.dict(
            sys.modules, {"runner_journal": SimpleNamespace(result_row=lambda journal: historical)}
        ):
            a.finish(outcome, True, True)
        self.assertEqual(historical["cause"], "provider_calibration_frame_observed")
        self.assertEqual(outcome["cause"], "user_requested_game_handback")
        self.assertTrue(writes["completion-ready.json"]["cancelled"])
        self.assertFalse(writes["completion-ready.json"]["calibration_frames_observed"])
        self.assertEqual(events[-1], "terminal_finished")


class ProfileMutationTests(Fixture):
    @classmethod
    def setUpClass(cls):
        candidate = os.environ.get("SMR_ACTIVE_JOB_CANDIDATE")
        if not candidate:
            from smr_launcher import activation

            cls.profiles_source = activation
            return
        root = Path(candidate).parent
        spec = importlib.util.spec_from_file_location(
            "guarded_profile_source", root / "frozen-code/src/smr_launcher/activation.py"
        )
        cls.profiles_source = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.profiles_source)

    def manager(self):
        live, store = self.directory / "live", self.directory / "store"
        for root in (live, self.directory / "prepared"):
            for name in ("CustomAssets", "UserMaps", "Saves"):
                (root / name).mkdir(parents=True)
        (live / "Saves/original.synthetic").write_bytes(b"original existing synthetic save")
        prepared = self.directory / "prepared"
        (prepared / "CustomAssets/map.xml").write_bytes(b"original synthetic asset")
        (prepared / "Saves/initial.synthetic").write_bytes(b"initial diagnostic synthetic save")
        self.running = False

        def exchange(first, second):
            temporary = self.directory / "exchange-temporary"
            first.rename(temporary)
            second.rename(first)
            temporary.rename(second)

        manager = self.profiles_source.FilesystemProfiles(
            live, store, ("CustomAssets", "UserMaps"), lambda: self.running, exchange=exchange
        )
        manager.enroll_original()
        target = manager.register_variant(
            "a" * 64, prepared, mutation_guard=self.control.mutation_guard
        )
        return manager, target, prepared

    def test_playing_at_guard_prevents_profile_exchange_and_launch(self):
        manager, target, prepared = self.manager()
        launch = Mock()

        @contextmanager
        def arriving_playing():
            self.append(user("I'm playing"))
            with self.control.mutation_guard():
                yield

        with self.assertRaises(control.UserCancelled):
            manager.play(target, launch, startup_seconds=0, mutation_guard=arriving_playing)
        launch.assert_not_called()
        self.assertEqual(manager._state()["active"], self.profiles_source.ORIGINAL)
        self.assertEqual(
            (manager.live / "Saves/original.synthetic").read_bytes(),
            b"original existing synthetic save",
        )
        self.assertFalse(manager.journal.exists())

    def test_playing_prevents_registration_before_any_staging_or_publish(self):
        manager, target, prepared = self.manager()
        self.append(user("I'm playing"))
        before = sorted(path.name for path in manager.profiles.iterdir())
        with self.assertRaises(control.UserCancelled):
            manager.register_variant("b" * 64, prepared, mutation_guard=self.control.mutation_guard)
        self.assertEqual(sorted(path.name for path in manager.profiles.iterdir()), before)
        self.assertEqual(list(manager.staging.iterdir()), [])
        self.assertEqual(manager._state()["active"], self.profiles_source.ORIGINAL)

    def test_guarded_operations_refuse_pending_profile_recovery(self):
        manager, target, prepared = self.manager()

        def interrupted(event):
            if event == "after_exchange":
                raise RuntimeError("synthetic interruption")

        manager.fault_hook = interrupted
        with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
            manager.switch(target)
        manager.fault_hook = lambda event: None
        before = manager.journal.read_bytes()
        for operation in (
            lambda: manager.switch(
                self.profiles_source.ORIGINAL, mutation_guard=self.control.cleanup_guard
            ),
            lambda: manager.play(target, Mock(), mutation_guard=self.control.mutation_guard),
            lambda: manager.register_variant(
                "b" * 64, prepared, mutation_guard=self.control.mutation_guard
            ),
        ):
            with self.assertRaises(self.profiles_source.RecoveryError):
                operation()
            self.assertEqual(manager.journal.read_bytes(), before)
        self.assertEqual(
            (manager.live / "Saves/initial.synthetic").read_bytes(),
            b"initial diagnostic synthetic save",
        )

    def test_busy_launcher_lock_refuses_before_state_guard(self):
        manager, target, prepared = self.manager()
        guard = Mock()
        with manager.lock_file.open("rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = time.monotonic()
            with self.assertRaises(BlockingIOError):
                manager.play(target, Mock(), startup_seconds=0, mutation_guard=guard)
            self.assertLess(time.monotonic() - before, 1)
        guard.assert_not_called()
        self.assertEqual(manager._state()["active"], self.profiles_source.ORIGINAL)

    def test_guarded_round_trip_preserves_distinct_original_existing_and_new_saves(self):
        manager, target, prepared = self.manager()
        original = (manager.live / "Saves/original.synthetic").read_bytes()
        prepared_bytes = (prepared / "Saves/initial.synthetic").read_bytes()

        def launch():
            # The profile exchange guard has released STATE before launch polls.
            self.control.poll()
            self.running = True

        manager.play(target, launch, startup_seconds=1, mutation_guard=self.control.mutation_guard)
        (manager.live / "Saves/future.synthetic").write_bytes(b"new synthetic save retained")
        self.running = False
        self.state = self.read()
        self.state["game_availability"] = {
            "status": "PLAYING",
            "updated_at": "2026-10-13T23:00:30+00:00",
        }
        self.save(self.state)
        manager.switch(self.profiles_source.ORIGINAL, mutation_guard=self.control.cleanup_guard)
        stored = manager._profile_path(target)
        self.assertEqual((manager.live / "Saves/original.synthetic").read_bytes(), original)
        self.assertEqual((stored / "Saves/initial.synthetic").read_bytes(), prepared_bytes)
        self.assertEqual(
            (stored / "Saves/future.synthetic").read_bytes(), b"new synthetic save retained"
        )
        self.assertEqual((prepared / "Saves/initial.synthetic").read_bytes(), prepared_bytes)
        self.assertFalse(manager.journal.exists())
        self.assertEqual(self.read()["game_availability"]["status"], "PLAYING")
