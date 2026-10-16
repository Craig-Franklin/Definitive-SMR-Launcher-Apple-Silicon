"""Native identity collection tolerates image records without relaxing identity checks."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/research"))
from agent_actor_binding import collect_actor
from active_job_control import ControlRefused


class ActorBindingTests(unittest.TestCase):
    def collect(self, *, large_context=False, wrong_session=False):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            session = "11111111-2222-3333-4444-555555555555"
            rollout = home / ".codex/sessions" / ("rollout-" + session + ".jsonl")
            rollout.parent.mkdir(parents=True)
            actor = dict(
                session=session,
                terminal="synthetic-terminal",
                incarnation_id="synthetic-incarnation",
                cwd=str(home),
                rollout=str(rollout),
            )
            context = dict(
                cwd=str(home),
                model="gpt-6.1-sol",
                effort="xhigh",
                approval_policy="never",
                sandbox_policy={"type": "danger-full-access"},
            )
            if large_context:
                context["padding"] = "x" * (1024 * 1024)
            rows = [
                dict(
                    type="session_meta",
                    payload=dict(id="wrong" if wrong_session else session, cwd=str(home)),
                ),
                dict(type="turn_context", payload={**context, "effort": "high"}),
                dict(
                    type="response_item",
                    payload=dict(type="custom_tool_call_output", output="x" * 7_568_554),
                ),
                dict(type="turn_context", payload=context),
            ]
            rollout.write_text("".join(json.dumps(row) + "\n" for row in rows))
            terminal = dict(
                handle=actor["terminal"],
                incarnationId=actor["incarnation_id"],
                worktreePath=actor["cwd"],
                connected=True,
                writable=True,
                orphaned=False,
                executionHostId="local",
                agentIdentity="codex",
            )
            receipt = dict(
                ok=True, _meta=dict(runtimeId="synthetic-runtime"), result=dict(terminal=terminal)
            )
            with (
                patch("agent_actor_binding.Path.home", return_value=home),
                patch("agent_actor_binding.Path.cwd", return_value=home),
                patch.dict(
                    "os.environ", CODEX_THREAD_ID=session, ORCA_TERMINAL_HANDLE=actor["terminal"]
                ),
                patch(
                    "agent_actor_binding.subprocess.check_output",
                    return_value=json.dumps(receipt).encode(),
                ),
            ):
                return collect_actor(
                    dict(
                        driver_actor=actor,
                        orca="synthetic-orca",
                        provider_runtime_id="synthetic-runtime",
                    )
                )

    def test_large_tool_image_does_not_hide_latest_identity(self):
        result = self.collect()
        self.assertEqual((result["model"], result["effort"]), ("gpt-6.1-sol", "xhigh"))

    def test_oversized_identity_record_still_refuses(self):
        with self.assertRaisesRegex(ControlRefused, "identity row bound"):
            self.collect(large_context=True)

    def test_wrong_native_session_still_refuses(self):
        with self.assertRaisesRegex(ControlRefused, "metadata drift"):
            self.collect(wrong_session=True)


if __name__ == "__main__":
    unittest.main()
