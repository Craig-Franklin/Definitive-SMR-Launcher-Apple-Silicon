"""Synthetic ownership controls; never create a Run, owner or game process."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/research")]
from active_job_control import ControlRefused, binding_for
from goal_owner import LEGACY_RUN, verify_root_scope


class GoalOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve() / "host-goal.json"
        self.actor = {
            "session": "00000000-0000-4000-8000-000000000001",
            "terminal": "term_synthetic",
        }
        self.goal = dict(
            threadId=self.actor["session"],
            createdAt=100,
            status="active",
            objective="Preserve the fictional test map through the complete lifecycle.",
        )
        subject = dict(
            archive_sha256="a" * 64, variant_id="b" * 64, scenario="UserMaps/Synthetic/Scenario.xml"
        )
        self.job = dict(
            purpose="ONE_AGENT_PACKAGED_CATALOGUE_LOAD_V1",
            root=dict(
                run="run_1234abcd5678",
                consumer_generation=1,
                session=self.actor["session"],
                coordinator=self.actor["terminal"],
            ),
            source_original=subject,
            goal_scope=dict(
                kind="current-goal-owner-v1",
                thread_id=self.actor["session"],
                created_at=100,
                objective_sha256=hashlib.sha256(self.goal["objective"].encode()).hexdigest(),
                subject=copy.deepcopy(subject),
                path=str(self.path),
                sha256="",
            ),
        )
        self.write_goal()

    def write_goal(self, envelope=None):
        raw = json.dumps({"goal": self.goal} if envelope is None else envelope).encode()
        self.path.write_bytes(raw)
        self.job["goal_scope"]["sha256"] = hashlib.sha256(raw).hexdigest()

    def test_legacy_run_stays_exact_and_does_not_need_goal_metadata(self):
        verify_root_scope({"root": {"run": LEGACY_RUN}}, self.actor)
        with self.assertRaisesRegex(ControlRefused, "Exact root grant"):
            verify_root_scope({"root": {"run": "run_1234abcd5678"}}, self.actor)

    def test_explicit_current_goal_and_separate_primary_owner_are_eligible(self):
        verify_root_scope(self.job, self.actor)

    def test_fresh_generation_composes_with_full_job_binding_without_weakening_legacy(self):
        job = copy.deepcopy(self.job)
        nonce = "00000000-0000-4000-8000-000000000002"
        job["active_control"] = dict(
            nonce=nonce,
            start_offset=1,
            prefix_sha256="c" * 64,
            not_before="2026-10-24T20:00:00+00:00",
            stream_identity={"device": 0, "inode": 1},
        )
        job["actor_binding"] = dict(
            root=job["root"],
            driver_actor=self.actor,
            root_actor=dict(self.actor, rollout="/synthetic/rollout.jsonl"),
        )
        job["job"] = f"finite-goal-{self.actor['session']}-generation1-{nonce}"
        self.assertEqual(binding_for(job)["root"]["consumer_generation"], 1)
        for generation in (0, True):
            invalid = copy.deepcopy(job)
            invalid["root"]["consumer_generation"] = generation
            with self.assertRaisesRegex(ControlRefused, "current job owner"):
                binding_for(invalid)
        del job["goal_scope"]
        job["root"]["run"] = LEGACY_RUN
        job["root"]["consumer_generation"] = 8
        with self.assertRaisesRegex(ControlRefused, "current job owner"):
            binding_for(job)

    def test_other_inactive_replaced_or_changed_goal_is_ineligible(self):
        for field, value in (
            ("status", "blocked"),
            ("status", "complete"),
            ("status", "paused"),
            ("threadId", "other-session"),
            ("createdAt", 101),
            ("createdAt", True),
            ("objective", "A different task"),
        ):
            with self.subTest(field=field, value=value):
                original = self.goal.copy()
                self.goal[field] = value
                self.write_goal()
                with self.assertRaisesRegex(ControlRefused, "active goal identity"):
                    verify_root_scope(self.job, self.actor)
                self.goal = original

    def test_tampered_host_file_and_non_object_envelope_refuse(self):
        self.path.write_bytes(b'{"goal":null}')
        with self.assertRaisesRegex(ControlRefused, "readback changed"):
            verify_root_scope(self.job, self.actor)
        self.write_goal(envelope=[])
        with self.assertRaisesRegex(ControlRefused, "host envelope"):
            verify_root_scope(self.job, self.actor)

    def test_borrowed_coordinator_session_and_legacy_run_refuse(self):
        for key, value in (
            ("coordinator", "other-terminal"),
            ("session", "other-session"),
            ("run", LEGACY_RUN),
        ):
            with self.subTest(key=key):
                job = copy.deepcopy(self.job)
                job["root"][key] = value
                with self.assertRaisesRegex(ControlRefused, "own sole-primary"):
                    verify_root_scope(job, self.actor)

    def test_changed_subject_unknown_purpose_and_extra_scope_refuse(self):
        job = copy.deepcopy(self.job)
        job["source_original"]["scenario"] = "UserMaps/Other/Scenario.xml"
        with self.assertRaisesRegex(ControlRefused, "subject changed"):
            verify_root_scope(job, self.actor)
        for job in (
            dict(self.job, purpose="unreviewed"),
            dict(self.job, goal_scope=dict(self.job["goal_scope"], extra=True)),
            dict(self.job, goal_scope=None),
        ):
            with self.assertRaisesRegex(ControlRefused, "owner scope"):
                verify_root_scope(job, self.actor)
