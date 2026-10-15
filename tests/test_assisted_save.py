"""Synthetic human acknowledgements and exact temporary saves; no game access."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/research")]
import active_job_control as control
import assisted_save as assisted
from finite_operation import run_calibration
from smr_launcher.scenario_evidence import canonical
from unique_save_file import SaveScope, require_absent


def user(text, *, role="user", kinds=None):
    when = datetime.now(timezone.utc)
    return {
        "type": "response_item",
        "timestamp": when.isoformat(),
        "payload": {
            "type": "message",
            "role": role,
            "id": "msg_synthetic",
            "content": [{"type": "input_text", "text": text}],
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "synthetic",
                "create_time": when.timestamp(),
                "content_item_kinds": ["user.text"] if kinds is None else kinds,
            },
        },
    }


class HumanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.stream = self.directory / "native.jsonl"
        self.stream.write_bytes(b'{"type":"synthetic_prefix"}\n')
        info = self.stream.stat()
        self.control = SimpleNamespace(
            binding={
                "rollout": str(self.stream),
                "stream_identity": {"device": info.st_dev, "inode": info.st_ino},
                "root": {"session": "synthetic-root"},
                "job": "synthetic-job",
            }
        )
        self.clock = 100.0
        self.enterContext(patch.object(assisted.time, "monotonic", side_effect=lambda: self.clock))
        self.human = assisted.HumanConfirmations(self.control, self.directory, 105.0)

    def append(self, row):
        with self.stream.open("ab") as stream:
            stream.write((json.dumps(row) + "\n").encode())

    def deliver(self, transform=lambda text: text, *, extra=None):
        def sleep(_):
            prompt = json.loads((self.directory / "human-world-prompt.json").read_text())
            self.append(user(transform(prompt["reply"])))
            if extra:
                self.append(user(extra(prompt)))
            self.clock += 1

        return patch.object(assisted.time, "sleep", side_effect=sleep)

    def test_native_report_is_fresh_bound_and_never_machine_focus_proof(self):
        with self.deliver():
            report = self.human.request("world", "Observe world", {"pid": 9}, lambda: None)
        self.assertEqual(report["evidence_kind"], "native_direct_human_report")
        self.assertFalse(report["automatic_scene_focus_or_delivery_proof"])
        self.assertEqual(report["process"], {"pid": 9})
        with self.assertRaisesRegex(control.ControlRefused, "repeated"):
            self.human.request("world", "Again", {"pid": 9}, lambda: None)

    def test_embedded_or_wrong_command_never_acknowledges(self):
        for text in ('Example: "pilot abc world"', '"pilot abc world"', "pilot wrong world"):
            self.assertIsNone(control.direct_user_command(user(text), {"pilot abc world": "world"}))
        for role, kinds in (
            ("assistant", None),
            ("tool", None),
            ("user", ["user.text", "user.image"]),
        ):
            self.assertIsNone(
                control.direct_user_command(
                    user("pilot abc world", role=role, kinds=kinds), {"pilot abc world": "world"}
                )
            )

    def test_no_response_finishes_existing_cleanup_without_callback(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as folder:
            human = assisted.HumanConfirmations(self.control, Path(folder), 105.0)
            self.clock = 100.0
            events = []
            adapter = SimpleNamespace(
                prepare=lambda: events.append("prepare"),
                launch=lambda: events.append("launch"),
                observe=lambda: human.request("world", "Observe", {"pid": 9}, lambda: None),
                persist_outcome=lambda outcome: events.append(("raw", outcome["status"])),
                stop=lambda: events.append("stop") or True,
                restore=lambda: events.append("restore") or True,
                finish=lambda *args: events.append("finish") or True,
            )

            def clock_advances(_):
                self.clock += 2

            with patch.object(assisted.time, "sleep", side_effect=clock_advances):
                result = run_calibration(adapter)
            self.assertTrue(result["terminal_finished"])
            self.assertEqual(result["outcome"]["status"], "automation_failed")
            self.assertIn("deadline expired", result["outcome"]["cause"])
            self.assertEqual(events[-3:], ["stop", "restore", "finish"])
            self.assertEqual(events[2], ("raw", "automation_failed"))

    def test_controller_outlives_model_process_and_settles_expired_wait(self):
        # A short-lived synthetic model starts the finite controller, then exits.
        # The controller's real timeout still runs cleanup and writes its result.
        controller = """
import json,sys,time
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from assisted_save import HumanConfirmations
from finite_operation import run_calibration
directory=Path(sys.argv[3]);stream=directory/'native.jsonl';info=stream.stat()
control=SimpleNamespace(binding={'rollout':str(stream),'stream_identity':{'device':info.st_dev,'inode':info.st_ino},'root':{'session':'synthetic'},'job':'synthetic'})
human=HumanConfirmations(control,directory,time.monotonic()+0.3);events=[]
adapter=SimpleNamespace(prepare=lambda:None,launch=lambda:None,observe=lambda:human.request('world','Observe',{'pid':9},lambda:None),persist_outcome=lambda outcome:events.append('raw'),stop=lambda:events.append('stop') or True,restore=lambda:events.append('restore') or True,finish=lambda *args:events.append('finish') or True)
result=run_calibration(adapter)
temporary=directory/'result.tmp'
temporary.write_text(json.dumps({'result':result,'events':events}))
temporary.replace(directory/'result.json')
"""
        spawner = """
import subprocess,sys
subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],*sys.argv[2:]],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
"""
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder).resolve()
            (directory / "native.jsonl").write_text('{"type":"synthetic_prefix"}\n')
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    spawner,
                    controller,
                    str(ROOT / "scripts/research"),
                    str(ROOT / "src"),
                    str(directory),
                ],
                check=True,
                timeout=5,
            )
            deadline = time.perf_counter() + 5
            result_file = directory / "result.json"
            while not result_file.exists() and time.perf_counter() < deadline:
                # setUp patches monotonic for other tests; use the real sleeper.
                time.sleep(0.02)
            self.assertTrue(result_file.exists(), "Independent controller did not settle")
            record = json.loads(result_file.read_text())
            self.assertEqual(record["events"], ["raw", "stop", "restore", "finish"])
            self.assertTrue(record["result"]["terminal_finished"])
            self.assertIn("deadline expired", record["result"]["outcome"]["cause"])

    def test_stop_beats_acknowledgement_in_same_batch(self):
        with self.deliver(extra=lambda prompt: prompt["stop_reply"]):
            with self.assertRaises(control.UserCancelled):
                self.human.request("world", "Observe", {"pid": 9}, lambda: None)
        self.assertFalse((self.directory / "human-world-report.json").exists())

    def test_owner_change_after_report_cannot_publish_success(self):
        calls = []

        def guard():
            calls.append(1)
            if len(calls) == 4:
                raise control.ControlRefused("Synthetic owner changed")

        with self.deliver(), self.assertRaisesRegex(control.ControlRefused, "owner changed"):
            self.human.request("world", "Observe", {"pid": 9}, guard)
        self.assertFalse((self.directory / "human-world-report.json").exists())


class AssistedCycleTests(unittest.TestCase):
    def test_complete_human_cycle_binds_one_new_file_and_preserves_old_save(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder).resolve()
            saves = profile / "Saves"
            saves.mkdir()
            keep = saves / "original.save"
            keep.write_bytes(b"original synthetic save")
            original = keep.stat()
            target = saves / "SyntheticNew.save"
            clock = [100.0]
            stages, writes, relaunches = [], {}, []

            def human(stage, instruction, **kwargs):
                stages.append(stage)
                if kwargs.get("validate"):
                    kwargs["validate"]()
                if stage == "saved":
                    target.write_bytes(b"new exact synthetic save")
                return {"stage": stage, "evidence_kind": "synthetic_human_report"}

            def relaunch(validate):
                validate()
                relaunches.append(1)

            owner = SimpleNamespace(
                human=human,
                profiles=SimpleNamespace(live=profile),
                canonical=canonical,
                write=lambda name, value: writes.__setitem__(name, value),
                guard_save=require_absent,
                guard_running=lambda: None,
                wait_stability=lambda until, deadline: clock.__setitem__(0, until),
                stop_for_relaunch=lambda: True,
                relaunch=relaunch,
                require_normal_exit=lambda: stages.append("normal_exit_verified"),
            )
            scope = SaveScope(
                "synthetic", "a" * 64, "b" * 64, "scenario", "c" * 64, "diagnostic", str(profile)
            )
            with patch.object(assisted.time, "monotonic", side_effect=lambda: clock[0]):
                result = assisted.observe_assisted_save(
                    owner,
                    scope,
                    "SyntheticNew",
                    target.name,
                    deadline=200,
                    scenario_label="synthetic",
                )
            self.assertEqual(
                stages,
                [
                    "world",
                    "action",
                    "filename",
                    "saved",
                    "quit",
                    "reopened",
                    "continued",
                    "finished",
                    "normal_exit_verified",
                ],
            )
            self.assertEqual(relaunches, [1])
            self.assertEqual(keep.read_bytes(), b"original synthetic save")
            self.assertEqual(
                (keep.stat().st_ino, keep.stat().st_mtime_ns),
                (original.st_ino, original.st_mtime_ns),
            )
            target_fact = writes["save-after-reopen-identity.json"]["exact_target"]
            self.assertEqual(
                target_fact["identity"][:2], (target.stat().st_dev, target.stat().st_ino)
            )
            self.assertFalse(result["save_reopen_credit"])
            self.assertEqual(result["world_loaded"], "human_reported")


if __name__ == "__main__":
    unittest.main()
