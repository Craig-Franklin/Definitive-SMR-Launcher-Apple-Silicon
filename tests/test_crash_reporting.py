"""Synthetic risk controls: no real reports, subprocesses, credentials or API calls."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from smr_launcher.crash_reporting import (
    CommandResult, GitHubCrashPublisher, PublicPayloadError,
    render_issue, validate_public_payload,
)

REPO = "Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon"
API = "repos/" + REPO
URL = "https://github.com/" + REPO + "/issues/17"
MARKER = "<!-- smr-crash-fingerprint:" + "a" * 64 + " -->"
CANARY = "PRIVATE_CANARY_/Users/secret/token=not-public\n@someone"


def payload():
    return {
        "schema": 1, "fingerprint": "a" * 64, "report_sha256": "b" * 64,
        "occurred_at": "2026-10-09T12:00:00Z", "game_executable_sha256": "c" * 64,
        "launcher_version": "1.2.3-rc4", "game_version": "1.2", "macos_version": "15.7.1",
        "architecture": "x86_64", "translated": True, "archive_sha256": "d" * 64,
        "variant_id": None, "scenario_key": "e" * 64, "exception_type": "EXC_BAD_ACCESS",
        "signal": "SIGSEGV", "termination_namespace": "SIGNAL", "exception_codes": [1, 4096],
        "frames": [{"index": 0, "image_role": "game", "image_uuid": "01234567-89ab-cdef-0123-456789abcdef", "offset": 4096}],
    }


def issue(url=URL, body=MARKER):
    return {"number": 17, "html_url": url, "body": body}


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.user = {"login": "Craig-Franklin", "type": "User"}
        self.repo = {"id": 1404818508, "full_name": REPO,
                     "owner": {"login": "Craig-Franklin", "type": "User"}, "parent": {"id": 950948787}}
        self.search = {"incomplete_results": False, "total_count": 0, "items": []}
        self.receipt = issue()
        self.readback = issue()
        self.failure = None
        self.second_user = None
        self.second_repo = None
        self.user_reads = self.repo_reads = 0
        self.push_config = b""
        self.remote = ("https://github.com/" + REPO + ".git\n").encode()

    def __call__(self, argv, *, stdin, timeout, max_output_bytes):
        self.calls.append((argv, stdin))
        assert timeout == 20.0 and max_output_bytes == 262144
        if argv[0] == "git":
            if "config" in argv:
                return CommandResult(0 if self.push_config else 1, self.push_config)
            return CommandResult(0, self.remote)
        assert argv[:4] == ("gh", "api", "--hostname", "github.com")
        endpoint, method = argv[6], argv[5]
        if self.failure:
            result = self.failure(endpoint, method)
            if result is not None:
                return result
        if endpoint == "user":
            self.user_reads += 1
            data = self.second_user if self.user_reads > 1 and self.second_user is not None else self.user
        elif endpoint == API:
            self.repo_reads += 1
            data = self.second_repo if self.repo_reads > 1 and self.second_repo is not None else self.repo
        elif endpoint.startswith("search/issues?"):
            q = parse_qs(urlsplit(endpoint).query)["q"][0]
            assert q == f'repo:{REPO} is:issue in:body "smr-crash-fingerprint:' + "a" * 64 + '"'
            data = self.search
        elif endpoint == API + "/issues" and method == "POST":
            assert argv[-2:] == ("--input", "-")
            assert set(json.loads(stdin)) == {"title", "body"}
            data = self.receipt
        elif endpoint == API + "/issues/17" and method == "GET":
            data = self.readback
        else:
            raise AssertionError("Unexpected command")
        return CommandResult(0, json.dumps(data).encode())

    def posts(self):
        return [c for c in self.calls if c[0][0] == "gh" and c[0][5] == "POST"]


class CrashReportingTests(unittest.TestCase):
    def setUp(self):
        self.no_process = patch("smr_launcher.crash_reporting.subprocess.run", side_effect=AssertionError("No real process"))
        self.no_process.start()
        self.addCleanup(self.no_process.stop)

    def test_literal_render_and_deep_copy(self):
        original = payload()
        clean = validate_public_payload(original)
        clean["frames"][0]["offset"] = 1
        clean["exception_codes"].append(2)
        self.assertEqual(original["frames"][0]["offset"], 4096)
        self.assertEqual(original["exception_codes"], [1, 4096])
        title, body = render_issue(original)
        self.assertEqual(title, "Sid Meier's Railroads! crash: EXC_BAD_ACCESS [aaaaaaaaaaaa]")
        self.assertEqual(body.splitlines()[0], MARKER)
        self.assertIn("| 0 | game | 01234567-89ab-cdef-0123-456789abcdef | 0x1000 |", body)
        self.assertIn("Exception codes: 0x1, 0x1000", body)
        self.assertIn("Full diagnostics remain private", body)
        self.assertIn("does not establish its cause", body)
        self.assertEqual((title, body), render_issue(dict(reversed(list(original.items())))))

    def test_canaries_in_every_text_field_are_rejected_before_runner(self):
        base = payload()
        paths = [(k,) for k, v in base.items() if isinstance(v, str)]
        paths += [("variant_id",), ("frames", 0, "image_role"), ("frames", 0, "image_uuid")]
        for path in paths:
            with self.subTest(path=path):
                p = payload()
                node = p
                for step in path[:-1]:
                    node = node[step]
                node[path[-1]] = CANARY
                runner = FakeRunner()
                result = GitHubCrashPublisher(runner).publish(p)
                self.assertEqual(result["status"], "rejected")
                self.assertNotIn(CANARY, json.dumps(result))
                self.assertEqual(runner.calls, [])
                with self.assertRaises(PublicPayloadError) as raised:
                    render_issue(p)
                self.assertNotIn(CANARY, str(raised.exception))

    def test_no_unknown_keys_notes_symbols_or_attachments(self):
        for key in ("note", "user_note", "path", "raw_report", "attachments", "symbols", "environment"):
            p = payload(); p[key] = CANARY
            self.assertEqual(GitHubCrashPublisher(FakeRunner()).publish(p)["status"], "rejected")
        p = payload(); p["frames"][0]["symbol"] = CANARY
        with self.assertRaises(PublicPayloadError):
            validate_public_payload(p)
        p = payload(); del p["translated"]
        with self.assertRaises(PublicPayloadError):
            validate_public_payload(p)

    def test_bounds_and_type_confusion(self):
        bad = [("schema", True), ("translated", 1), ("exception_codes", [True]),
               ("exception_codes", [-1]), ("exception_codes", [2**64]),
               ("exception_codes", [0]*9), ("frames", payload()["frames"]*21),
               ("occurred_at", "2026-10-09T12:00:00"), ("occurred_at", "2026-02-30T12:00:00Z"),
               ("fingerprint", "A"*64), ("launcher_version", "1.2.3-private-secret"),
               ("launcher_version", "1.2.3+secret"), ("game_version", "1.2 secret"),
               ("architecture", "Intel Mac"), ("signal", "SIGPRIVATE")]
        for key, value in bad:
            with self.subTest(key=key, value=value):
                p = payload(); p[key] = value
                with self.assertRaises(PublicPayloadError):
                    validate_public_payload(p)
        for key, value in (("index", True), ("index", 2**31), ("offset", -1), ("offset", 2**64), ("image_uuid", "not-a-uuid")):
            p = payload(); p["frames"][0][key] = value
            with self.assertRaises(PublicPayloadError):
                validate_public_payload(p)

    def test_valid_unknowns_and_maxima(self):
        p = payload()
        for key in ("game_version", "macos_version", "architecture", "exception_type", "signal", "termination_namespace"):
            p[key] = "UNKNOWN"
        p["translated"] = None; p["frames"][0]["image_uuid"] = None
        p["frames"][0]["offset"] = 2**64 - 1; p["exception_codes"] = [2**64-1]*8
        p["occurred_at"] = "2026-10-09T12:00:00.123456-04:00"
        self.assertIn("translated: unknown", render_issue(p)[1])
        for version in ("1.2.3", "1.2.3-alpha1", "1.2.3-beta2", "1.2.3-rc3"):
            p["launcher_version"] = version
            validate_public_payload(p)

    def test_create_only_structured_safe_request_and_fresh_guards(self):
        fake = FakeRunner()
        with patch("builtins.open", side_effect=AssertionError("No secret/report reads")):
            result = GitHubCrashPublisher(fake).publish(payload())
        self.assertEqual(result, {"status": "published", "issue_url": URL, "detail": "created"})
        self.assertEqual(fake.user_reads, 2); self.assertEqual(fake.repo_reads, 2)
        post = fake.posts()[0]
        self.assertEqual(len(fake.posts()), 1)
        data = json.loads(post[1])
        self.assertEqual(data["title"], "Sid Meier's Railroads! crash: EXC_BAD_ACCESS [aaaaaaaaaaaa]")
        self.assertIn("\n\n## Crash summary\n", data["body"])
        self.assertNotIn(CANARY, post[1].decode())
        self.assertFalse(any(c[0][0] == "git" for c in fake.calls))
        index = fake.calls.index(post)
        self.assertEqual([c[0][6] for c in fake.calls[index-2:index]], ["user", API])
        self.assertEqual(fake.calls[-1][0][6], API + "/issues/17")

    def test_account_owner_repository_and_parent_guards(self):
        changes = [("user", "login", "someone"), ("user", "type", "Organization"),
                   ("repo", "id", 950948787), ("repo", "id", True), ("repo", "full_name", "ageekhere/Definitive-SMR-Launcher"),
                   ("repo", "owner", {"login": "Craig-Franklin", "type": "Organization"}),
                   ("repo", "owner", {"login": "other", "type": "User"}),
                   ("repo", "parent", {"id": 1})]
        for target, key, value in changes:
            with self.subTest(target=target, key=key):
                fake = FakeRunner(); getattr(fake, target)[key] = value
                self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["status"], "rejected")
                self.assertFalse(fake.posts())
        fake = FakeRunner(); fake.second_user = {"login": "other", "type": "User"}
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["status"], "rejected")
        self.assertFalse(fake.posts())
        fake = FakeRunner(); fake.second_repo = copy.deepcopy(fake.repo); fake.second_repo["id"] = 1
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["status"], "rejected")
        self.assertFalse(fake.posts())

    def test_checkout_fetch_push_and_configured_pushremote_guards(self):
        fake = FakeRunner(); fake.push_config = b"remote.pushDefault personal\nbranch.main.pushRemote personal\n"
        self.assertEqual(GitHubCrashPublisher(fake, checkout=Path("/synthetic/project")).publish(payload())["status"], "published")
        self.assertTrue(any(c[0][-1] == "personal" for c in fake.calls))
        fake = FakeRunner(); fake.remote = b"https://github.com/ageekhere/Definitive-SMR-Launcher.git\n"
        self.assertEqual(GitHubCrashPublisher(fake, checkout=Path("/synthetic/project")).publish(payload())["status"], "rejected")
        self.assertFalse(fake.posts())
        fake = FakeRunner(); original = fake.__call__
        def push_upstream(argv, **kwargs):
            if "get-url" in argv and "--push" in argv:
                return CommandResult(0, b"https://github.com/other/other.git\n")
            return original(argv, **kwargs)
        self.assertEqual(GitHubCrashPublisher(push_upstream, checkout=Path("/synthetic/project")).publish(payload())["status"], "rejected")
        self.assertFalse(fake.posts())

    def test_duplicate_group_and_existing_url_readback(self):
        fake = FakeRunner(); fake.search["items"] = [issue()]; fake.search["total_count"] = 1
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["detail"], "grouped")
        self.assertFalse(fake.posts())
        fake = FakeRunner()
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload(), existing_issue_url=URL)["detail"], "reconciled")
        self.assertFalse(fake.posts())
        for url in (URL + "?x=1", URL + "/", URL.replace("Craig-Franklin", "other"), URL.replace("/17", "/0"), URL.replace("/17", "/17\n")):
            fake = FakeRunner()
            self.assertEqual(GitHubCrashPublisher(fake).publish(payload(), existing_issue_url=url)["status"], "rejected")
            self.assertFalse(fake.calls)

    def test_ambiguous_search_never_creates(self):
        variants = [
            {"incomplete_results": True, "total_count": 0, "items": []},
            {"incomplete_results": False, "total_count": 1, "items": []},
            {"incomplete_results": False, "total_count": 2, "items": [issue(), issue()]},
            {"incomplete_results": False, "total_count": 101, "items": []},
        ]
        for search in variants:
            fake = FakeRunner(); fake.search = search
            self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["status"], "pending")
            self.assertFalse(fake.posts())
        fake = FakeRunner(); fake.search = {"incomplete_results": False, "total_count": 1, "items": [issue(URL.replace("Craig-Franklin", "other"))]}
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["status"], "rejected")
        self.assertFalse(fake.posts())

    def test_exact_marker_not_substring(self):
        fake = FakeRunner(); fake.search["total_count"] = 1; fake.search["items"] = [issue(body="prefix " + MARKER)]
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["detail"], "created")
        fake = FakeRunner(); fake.readback["body"] = "prefix " + MARKER
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload(), existing_issue_url=URL)["status"], "rejected")

    def test_missing_gh_auth_network_and_malformed_read_return_pending(self):
        for failure in (FileNotFoundError(CANARY), OSError(CANARY), RuntimeError(CANARY), subprocess.TimeoutExpired("gh", 20, stderr=CANARY)):
            def unavailable(*args, **kwargs):
                raise failure
            result = GitHubCrashPublisher(unavailable).publish(payload())
            self.assertEqual(result["status"], "pending")
            self.assertNotIn(CANARY, str(result))
        for reply in (CommandResult(1, CANARY.encode()), CommandResult(0, b"not-json"), CommandResult(0, b"x"*262145)):
            fake = FakeRunner(); fake.failure = lambda endpoint, method: reply
            self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["status"], "pending")
            self.assertFalse(fake.posts())

    def test_timeout_after_post_unknown_no_retry_then_read_only_reconcile(self):
        fake = FakeRunner()
        def timeout(endpoint, method):
            if method == "POST":
                raise subprocess.TimeoutExpired("gh", 20, stderr=CANARY)
        fake.failure = timeout
        result = GitHubCrashPublisher(fake).publish(payload())
        self.assertEqual(result["status"], "unknown"); self.assertEqual(len(fake.posts()), 1)
        self.assertNotIn(CANARY, str(result))
        fake = FakeRunner()
        result = GitHubCrashPublisher(fake).publish(payload(), reconcile_only=True)
        self.assertEqual(result["status"], "unknown"); self.assertFalse(fake.posts())
        fake.search["total_count"] = 1; fake.search["items"] = [issue()]
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload(), reconcile_only=True)["status"], "published")
        self.assertFalse(fake.posts())
        fake.failure = lambda endpoint, method: CommandResult(1)
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload(), reconcile_only=True)["status"], "unknown")

    def test_mutation_receipt_and_readback_failures_are_unknown(self):
        for receipt in (None, {"html_url": URL, "number": 18}, issue(URL.replace("Craig-Franklin", "other"))):
            fake = FakeRunner(); fake.receipt = receipt
            self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["status"], "unknown")
            self.assertEqual(len(fake.posts()), 1)
        fake = FakeRunner(); fake.readback["body"] = "wrong fingerprint"
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["status"], "unknown")
        fake = FakeRunner()
        fake.failure = lambda endpoint, method: CommandResult(1) if endpoint.endswith("/issues/17") else None
        self.assertEqual(GitHubCrashPublisher(fake).publish(payload())["status"], "unknown")
        self.assertEqual(len(fake.posts()), 1)


if __name__ == "__main__":
    unittest.main()
