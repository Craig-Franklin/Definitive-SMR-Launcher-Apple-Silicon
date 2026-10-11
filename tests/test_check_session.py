"""Session routing must reject stale identity without operating Orca state."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_session import SessionError, check_session


class SessionCheckTests(unittest.TestCase):
    def setUp(self):
        self.cwd = Path.cwd()
        self.env = {"ORCA_TERMINAL_HANDLE": "term_test", "ORCA_WORKTREE_ID": "repo::test"}
        self.worktree = {"id": "repo::test", "path": str(self.cwd)}
        self.terminal = {
            "handle": "term_test",
            "worktreeId": "repo::test",
            "orphaned": False,
            "connected": True,
            "writable": True,
        }

    def runner(self):
        def run(args, **kwargs):
            self.assertEqual(kwargs["cwd"], self.cwd)
            if args[1:3] == ["worktree", "current"]:
                result = {"worktree": self.worktree}
            else:
                self.assertEqual(
                    args[1:], ["terminal", "show", "--terminal", "term_test", "--json"]
                )
                result = {"terminal": self.terminal}
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"ok": True, "result": result}), ""
            )

        return Mock(side_effect=run)

    def test_non_orca_session_does_not_call_cli(self):
        run = Mock(side_effect=AssertionError("CLI must not run"))
        self.assertEqual(check_session(env={}, run=run)["host"], "standard")

    def test_installed_cli_alone_does_not_mean_hosted_session(self):
        run = Mock(side_effect=AssertionError("CLI must not run"))
        self.assertEqual(
            check_session(env={"ORCA_CLI_COMMAND": "/some/orca"}, run=run)["host"], "standard"
        )

    def test_matching_live_caller_passes_with_read_only_queries(self):
        run = self.runner()
        self.assertTrue(check_session(self.env, self.cwd, run)["verified"])
        self.assertEqual(run.call_count, 2)

    def test_different_terminal_workspace_is_refused(self):
        self.terminal["worktreeId"] = "another"
        with self.assertRaisesRegex(SessionError, "different workspace"):
            check_session(self.env, self.cwd, self.runner())

    def test_stale_advertised_workspace_is_refused(self):
        self.env["ORCA_WORKTREE_ID"] = "stale"
        with self.assertRaisesRegex(SessionError, "Advertised"):
            check_session(self.env, self.cwd, self.runner())

    def test_every_advertised_workspace_address_must_match(self):
        for worktree_id in (None, "repo::test"):
            with self.subTest(worktree_id=worktree_id):
                env = {"ORCA_TERMINAL_HANDLE": "term_test", "ORCA_WORKSPACE_ID": "stale"}
                if worktree_id:
                    env["ORCA_WORKTREE_ID"] = worktree_id
                with self.assertRaisesRegex(SessionError, "ORCA_WORKSPACE_ID"):
                    check_session(env, self.cwd, self.runner())
        self.env["ORCA_WORKSPACE_ID"] = "repo::test"
        self.assertTrue(check_session(self.env, self.cwd, self.runner())["verified"])

    def test_missing_or_invalid_live_identity_is_refused(self):
        for value in (None, "", " ", 7):
            with self.subTest(value=value):
                self.worktree["id"] = value
                self.terminal["worktreeId"] = value
                with self.assertRaisesRegex(SessionError, "invalid workspace identity"):
                    check_session({"ORCA_TERMINAL_HANDLE": "term_test"}, self.cwd, self.runner())

    def test_workspace_path_must_be_absolute_and_nonempty(self):
        for value in (None, "", " ", "relative/path", 7):
            with self.subTest(value=value):
                self.worktree["path"] = value
                with self.assertRaisesRegex(SessionError, "workspace path"):
                    check_session(self.env, self.cwd, self.runner())
        self.worktree["path"] = str(self.cwd.parent)
        self.assertTrue(check_session(self.env, self.cwd, self.runner())["verified"])
        self.worktree["path"] = str(self.cwd / "different-workspace")
        with self.assertRaisesRegex(SessionError, "does not contain"):
            check_session(self.env, self.cwd, self.runner())

    def test_orphaned_disconnected_and_unknown_state_are_refused(self):
        for field, value in (
            ("orphaned", True),
            ("connected", False),
            ("connected", None),
            ("writable", False),
        ):
            with self.subTest(field=field, value=value):
                original = self.terminal[field]
                self.terminal[field] = value
                with self.assertRaises(SessionError):
                    check_session(self.env, self.cwd, self.runner())
                self.terminal[field] = original

    def test_missing_caller_handle_is_refused(self):
        with self.assertRaisesRegex(SessionError, "without a caller"):
            check_session({"ORCA_WORKSPACE_ID": "test"})

    def test_unavailable_selected_cli_does_not_fall_back(self):
        self.env["ORCA_CLI_COMMAND"] = "/selected/orca"
        run = Mock(side_effect=FileNotFoundError("selected CLI missing"))
        with self.assertRaisesRegex(SessionError, "selected CLI missing"):
            check_session(self.env, self.cwd, run)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "/selected/orca")

    def test_invalid_cli_response_is_refused(self):
        for output in ("not json", "{}", '{"ok":true,"result":{}}'):
            with self.subTest(output=output), self.assertRaises(SessionError):
                check_session(
                    self.env,
                    self.cwd,
                    Mock(return_value=subprocess.CompletedProcess([], 0, output, "")),
                )


if __name__ == "__main__":
    unittest.main()
