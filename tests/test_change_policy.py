"""Real synthetic histories and job results for change-aware CI; no app builds."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from change_policy import (
    Impact,
    changed_impact,
    event_range,
    gate_errors,
    path_impact,
    verification_plan,
)
import release_macos as release
from release_policy import ReleasePoint, plan_release


class ChangePolicyTests(unittest.TestCase):
    def test_relevant_checks_and_no_duplicate_release_build(self):
        cases = (
            ("AGENTS.md", (False, False, False, False)),
            ("docs/guide.md", (False, False, False, False)),
            ("tests/test_app.py", (True, True, False, False)),
            ("scripts/check_session.py", (True, True, False, False)),
            (".github/workflows/macos-app.yml", (True, True, False, False)),
            ("src/smr_launcher/gui.py", (True, True, False, True)),
            ("docs/UPSTREAM_ASSETS.md", (True, True, True, True)),
            ("requirements-build.txt", (True, True, True, True)),
            ("requirements-runtime.txt", (True, True, True, True)),
            ("requirements-dev.txt", (True, False, False, False)),
            ("scripts/build_macos.sh", (True, True, True, True)),
            ("scripts/translate_macos.swift", (True, True, True, True)),
            ("Definitive-SMR-Launcher/icon/icon.ico", (True, True, True, True)),
            ("Definitive-SMR-Launcher/DSMRL_data/RRT_Difficulty.xml", (True, True, True, True)),
            ("unexpected-input.cfg", (True, True, True, True)),
        )
        for path, flags in cases:
            with self.subTest(path=path):
                self.assertEqual(path_impact(path), Impact(*flags))
        self.assertEqual(
            verification_plan(Impact(), False),
            dict(quality=False, tests=False, build=False, release=False),
        )
        self.assertEqual(
            verification_plan(Impact(), True),
            dict(quality=True, tests=True, build=False, release=True),
        )
        self.assertTrue(verification_plan(Impact(package=True), False)["build"])
        self.assertFalse(verification_plan(Impact(package=True), True)["build"])
        self.assertTrue(verification_plan(Impact(), False, build_requested=True)["build"])

    def test_gate_accepts_only_success_and_intentional_skips(self):
        plan = dict(quality="false", tests="false", build="false", release="false")
        results = dict(plan="success", quality="success", tests="skipped", build="skipped")
        self.assertEqual(gate_errors(plan, results), [])
        for name in results:
            for outcome in ("failure", "cancelled", None):
                with self.subTest(name=name, outcome=outcome):
                    self.assertTrue(gate_errors(plan, dict(results, **{name: outcome})))
        self.assertTrue(gate_errors(dict(plan, tests="true"), results))
        self.assertTrue(gate_errors(dict(plan, release="true"), results))
        self.assertTrue(gate_errors({}, results))
        self.assertTrue(gate_errors(plan, dict(results, quality="skipped")))

    def test_gate_rejects_inconsistent_build_and_release_plans(self):
        results = dict(plan="success", quality="success", tests="success", build="skipped")
        for flags in (
            dict(quality="false", tests="true", build="false", release="true"),
            dict(quality="false", tests="true", build="true", release="false"),
            dict(quality="true", tests="true", build="true", release="true"),
        ):
            with self.subTest(flags=flags):
                observed = dict(results, build="success" if flags["build"] == "true" else "skipped")
                self.assertTrue(gate_errors(flags, observed))
        release_plan = dict(quality="true", tests="true", build="false", release="true")
        self.assertEqual(gate_errors(release_plan, results), [])
        build_plan = dict(quality="true", tests="true", build="true", release="false")
        self.assertEqual(gate_errors(build_plan, dict(results, build="success")), [])


class GitImpactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(
            os.environ,
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="Fixture",
            GIT_AUTHOR_EMAIL="fixture@example.invalid",
            GIT_COMMITTER_NAME="Fixture",
            GIT_COMMITTER_EMAIL="fixture@example.invalid",
        )
        self.git("init", "--quiet")
        self.base = self.commit("chore: baseline", {"README.md": "Example\n"})

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True, env=self.env
        )

    def commit(self, message, files):
        for name, text in files.items():
            path = self.root / name
            if text is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        self.git("add", ".")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def release_plan(self, head):
        with patch.object(release, "output", side_effect=lambda *args: self.git(*args[1:]).strip()):
            return plan_release(
                head,
                [ReleasePoint("0.13.0", self.base)],
                {},
                release.commits_since,
                lambda left, right: self.git("merge-base", left, right).strip() == left,
            )

    def test_internal_changes_do_not_release_even_with_feature_or_breaking_intent(self):
        for message, files in (
            ("fix: update guide", {"README.md": "Revised\n"}),
            ("feat: improve tests", {"tests/test_example.py": "value = 1\n"}),
            ("fix!: update tooling", {"scripts/check_example.py": "value = 1\n"}),
            ("fix: empty commit", {}),
        ):
            head = self.commit(message, files)
            self.assertFalse(self.release_plan(head).required, message)
        self.assertFalse(changed_impact(self.base, head, self.git).package)

    def test_docs_head_does_not_hide_pending_app_fix(self):
        fix = self.commit("fix: repair app", {"src/smr_launcher/example.py": "value = 1\n"})
        head = self.commit("docs: explain fix", {"README.md": "Use it\n"})
        self.assertEqual(changed_impact(fix, head, self.git), Impact())
        plan = self.release_plan(head)
        self.assertTrue(plan.required)
        self.assertEqual(plan.version, "0.13.1")
        self.assertTrue(verification_plan(Impact(), plan.required)["tests"])
        self.assertFalse(verification_plan(Impact(), plan.required)["build"])

    def test_packaged_docs_and_deleted_or_renamed_app_inputs_count(self):
        app = self.commit("fix: update attribution", {"docs/UPSTREAM_ASSETS.md": "Attribution\n"})
        self.assertTrue(self.release_plan(app).required)
        renamed = self.commit(
            "chore: move attribution",
            {"docs/UPSTREAM_ASSETS.md": None, "docs/old-attribution.md": "Attribution\n"},
        )
        self.assertTrue(changed_impact(app, renamed, self.git).package)
        source = self.commit("feat: example", {"src/smr_launcher/example.py": "value = 1\n"})
        removed = self.commit("fix: remove example", {"src/smr_launcher/example.py": None})
        self.assertTrue(changed_impact(source, removed, self.git).app)

    def test_tool_config_does_not_change_app_metadata(self):
        config = '[project]\nname = "example"\nversion = "1.0.0"\n'
        baseline = self.commit("chore: metadata", {"pyproject.toml": config})
        tool = self.commit(
            "fix: lint config", {"pyproject.toml": config + "[tool.ruff]\nline-length = 100\n"}
        )
        self.assertEqual(changed_impact(baseline, tool, self.git), Impact(quality=True))
        app = self.commit(
            "fix: package metadata", {"pyproject.toml": config.replace("1.0.0", "1.0.1")}
        )
        self.assertTrue(changed_impact(tool, app, self.git).package)

    def test_missing_history_and_malformed_metadata_fail(self):
        with self.assertRaises(subprocess.CalledProcessError):
            changed_impact("f" * 40, self.base, self.git)
        broken = self.commit("chore: malformed metadata", {"pyproject.toml": "[broken"})
        with self.assertRaises(ValueError):
            changed_impact(self.base, broken, self.git)

    def test_event_base_and_checkout_are_verified(self):
        head = self.commit("docs: change", {"README.md": "Two\n"})
        self.assertEqual(
            event_range({"before": self.base}, "push", head, self.git), (self.base, head)
        )
        self.assertEqual(
            event_range(
                {"pull_request": {"base": {"sha": self.base}}}, "pull_request", head, self.git
            ),
            (self.base, head),
        )
        with self.assertRaises(ValueError):
            event_range({"before": "0" * 40}, "push", head, self.git)
        with self.assertRaises(ValueError):
            event_range({"before": self.base}, "push", self.base, self.git)


if __name__ == "__main__":
    unittest.main()
