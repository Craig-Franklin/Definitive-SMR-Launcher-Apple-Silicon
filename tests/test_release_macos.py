"""Synthetic release version and publication gates; no credentials or network."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_macos as release
from release_policy import Commit, ReleasePlan, ReleasePoint, bump_version, plan_release, release_notes


def assets(version="0.3.6"):
    name = release.asset_name(version)
    return {"tag_name": "v" + version, "draft": False, "prerelease": False, "assets": [
        {"name": name, "state": "uploaded", "size": 100, "digest": "sha256:" + "a" * 64},
        {"name": name + ".sha256", "state": "uploaded", "size": 120},
    ]}


class ReleaseTests(unittest.TestCase):
    def test_dmg_release_requires_both_archives_and_matching_checksums(self):
        value = assets()
        dmg = release.asset_name("0.3.6").removesuffix(".zip") + ".dmg"
        value["assets"].extend([
            {"name": dmg, "state": "uploaded", "size": 120, "digest": "sha256:" + "b" * 64},
            {"name": dmg + ".sha256", "state": "uploaded", "size": 120},
        ])
        digests = {release.asset_name("0.3.6"): "a" * 64, dmg: "b" * 64}
        release.validate_assets(value, "0.3.6", digests, require_dmg=True)
        with patch.object(release, "output", side_effect=[
            "a" * 64 + "  " + release.asset_name("0.3.6"), "b" * 64 + "  " + dmg]):
            release.validate_checksum("0.3.6", value)
        with self.assertRaises(RuntimeError):
            release.validate_assets(assets(), "0.3.6", require_dmg=True)
        value["assets"].pop()
        with self.assertRaises(RuntimeError):
            release.validate_assets(value, "0.3.6", require_dmg=True)

    def test_checkout_origin_accepts_only_exact_personal_fork(self):
        for suffix in ("", ".git"):
            release.validate_origin("https://github.com/" + release.REPO + suffix)
        for url in ("https://github.com/ageekhere/Definitive-SMR-Launcher",
                    "https://github.com/" + release.REPO + "/other",
                    "https://github.com.example/" + release.REPO,
                    "https://user:token@github.com/" + release.REPO):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                release.validate_origin(url)

    def test_semantic_bumps_including_breaking_before_one(self):
        self.assertEqual(bump_version("0.3.8", "patch"), "0.3.9")
        self.assertEqual(bump_version("0.3.8", "minor"), "0.4.0")
        self.assertEqual(bump_version("0.3.8", "major"), "1.0.0")
        self.assertGreater(release.version_tuple("0.3.10"), release.version_tuple("0.3.9"))
        self.assertGreater(release.version_tuple("0.4.1"), release.version_tuple("0.3.100"))

    def test_commit_classification_uses_header_or_explicit_breaking_footer(self):
        examples = {
            "feat(maps): show sources": "minor", "FEAT: add support": "minor",
            "fix: repair save handling": "patch", "perf(cache): reduce reads": "patch",
            "feat!: change data format": "major", "chore(api)!: remove compatibility": "major",
            "docs: migration\n\nBREAKING CHANGE: old setting removed": "major",
            "refactor: reorganize\n\nBREAKING-CHANGE: old interface removed": "major",
            "docs: explain feat: examples": "", "test: add coverage": "",
            "chore: update tooling": "", "nonconventional message": "",
            "fix: example body\n\nThe text BREAKING CHANGE: is documentation": "patch",
            "docs: explain\n\nbreaking change: lowercase is not a breaking footer": "",
        }
        for message, expected in examples.items():
            with self.subTest(message=message):
                self.assertEqual(Commit("a" * 40, message).bump, expected)

    def test_highest_bump_over_complete_commit_range_uses_published_baseline(self):
        calls = []
        commits = (Commit("b" * 40, "fix: first"), Commit("c" * 40, "feat: second"), Commit("d" * 40, "docs: last"))
        def history(base, head):
            calls.append((base, head))
            return commits
        plan = plan_release("head", [ReleasePoint("0.3.7", "old"), ReleasePoint("0.3.8", "base")],
                            {}, history, lambda left, right: (left, right) == ("base", "head"))
        self.assertEqual((plan.required, plan.version, plan.bump), (True, "0.4.0", "minor"))
        self.assertEqual(calls, [("base", "head")])
        self.assertEqual(plan.commits, commits)

    def test_docs_tests_chores_and_empty_push_skip_without_stamping(self):
        for messages in ((), ("docs: update guide", "test: add fixture", "chore: bump tool")):
            plan = plan_release("head", [ReleasePoint("0.3.8", "base")], {},
                                lambda *_: tuple(Commit("a" * 40, message) for message in messages),
                                lambda *_: True)
            self.assertFalse(plan.required)
            self.assertEqual(plan.version, "")

    def test_pending_tags_and_untagged_legacy_versions_reserve_numbers(self):
        commits = lambda *_: (Commit("a" * 40, "fix: safe retry"),)
        plan = plan_release("head", [ReleasePoint("0.3.0", "base")],
                            {"0.3.7": "pending-tag", "0.3.8": None}, commits, lambda *_: True)
        self.assertEqual(plan.version, "0.3.9")
        # The reserved tag is not the history baseline and must not hide changes.
        self.assertEqual(plan.previous_sha, "base")
        plan = plan_release("head", [ReleasePoint("0.3.0", "base")],
                            {"0.3.7": "pending-tag", "0.3.8": None},
                            lambda *_: (Commit("a" * 40, "feat: new UI"),), lambda *_: True)
        self.assertEqual(plan.version, "0.4.0")

    def test_same_head_published_and_pending_reruns(self):
        def must_not_read(*_):
            self.fail("Already released HEAD must not recalculate a bump")
        plan = plan_release("head", [ReleasePoint("0.4.0", "head")], {}, must_not_read, must_not_read)
        self.assertFalse(plan.required)
        self.assertTrue(plan.published)
        self.assertEqual(plan.version, "0.4.0")
        plan = plan_release("head", [ReleasePoint("0.3.8", "base")], {"0.4.0": "head"},
                            lambda *_: (Commit("a" * 40, "feat: new UI"),), lambda *_: True)
        self.assertEqual(plan.version, "0.4.0")
        self.assertTrue(plan.required)

    def test_newest_release_ancestry_rejects_unrelated_and_skips_superseded_source(self):
        history = lambda *_: self.fail("Wrong ancestry must not produce release notes")
        with self.assertRaisesRegex(RuntimeError, "ancestry"):
            plan_release("head", [ReleasePoint("0.3.8", "other")], {}, history, lambda *_: False)
        plan = plan_release("head", [ReleasePoint("0.3.8", "newer")], {}, history,
                            lambda left, right: (left, right) == ("head", "newer"))
        self.assertFalse(plan.required)
        self.assertIn("newer source", plan.reason)

    def test_notes_include_exact_range_grouped_changes_and_breaking_details(self):
        commits = (Commit("a" * 40, "feat(maps): show sources"),
                   Commit("b" * 40, "fix: preserve saves"),
                   Commit("c" * 40, "docs!: change setup\n\nBREAKING CHANGE: move config"))
        plan = ReleasePlan(True, "1.0.0", "major", "0.3.8", "d" * 40, "major", commits=commits)
        notes = release_notes(plan, release.REPO, "e" * 40, "123")
        for expected in ("## Features", "## Fixes", "## Breaking changes", "move config", "v1.0.0",
                         "/compare/" + "d" * 40 + "..." + "e" * 40, "/actions/runs/123"):
            self.assertIn(expected, notes)

    def test_real_git_merge_graph_includes_unreleased_branch_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            env = dict(os.environ, GIT_AUTHOR_NAME="Synthetic Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                       GIT_COMMITTER_NAME="Synthetic Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
            def git(*args, input=None):
                return subprocess.check_output(["git", "-C", str(root), *args], input=input,
                                               text=True, env=env).strip()
            tree = git("hash-object", "-w", "-t", "tree", "--stdin", input="")
            def commit(message, *parents):
                args = ["-c", "commit.gpgsign=false", "commit-tree", tree]
                for parent in parents:
                    args.extend(["-p", parent])
                return git(*args, input=message)
            baseline = commit("chore: baseline")
            feature = commit("feat: add maps", baseline)
            docs = commit("docs: explain maps", baseline)
            head = commit("Merge feature branch", docs, feature)
            unrelated = commit("chore: independent history")
            def ancestor(left, right):
                return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", left, right],
                                      capture_output=True).returncode == 0
            with patch.object(release, "output", side_effect=lambda *args: git(*args[1:])):
                plan = plan_release(head, [ReleasePoint("0.3.8", baseline)], {}, release.commits_since, ancestor)
                self.assertEqual((plan.version, plan.previous_sha), ("0.4.0", baseline))
                self.assertEqual({item.sha for item in plan.commits}, {feature, docs, head})
                with self.assertRaisesRegex(RuntimeError, "ancestry"):
                    plan_release(head, [ReleasePoint("0.3.8", unrelated)], {}, release.commits_since, ancestor)

    def test_stamping_keeps_runtime_and_bundle_versions_aligned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src/smr_launcher").mkdir(parents=True)
            (root / "pyproject.toml").write_text('[project]\nversion = "0.3.0"\n')
            runtime = root / "src/smr_launcher/__init__.py"
            runtime.write_text('APP_VERSION = "0.3.0"\n')
            release.stamp_version(root, "0.3.6")
            self.assertIn('version = "0.3.6"', (root / "pyproject.toml").read_text())
            self.assertEqual(runtime.read_text(), 'APP_VERSION = "0.3.6"\n')

    def test_incomplete_or_different_upload_never_passes_gate(self):
        release.validate_assets(assets(), "0.3.6", {release.asset_name("0.3.6"): "a" * 64})
        incomplete = assets()
        incomplete["assets"].pop()
        with self.assertRaises(RuntimeError):
            release.validate_assets(incomplete, "0.3.6")
        with self.assertRaises(RuntimeError):
            release.validate_assets(assets(), "0.3.6", {release.asset_name("0.3.6"): "b" * 64})
        invalid = assets()
        invalid["assets"][0]["digest"] = None
        with self.assertRaises(RuntimeError):
            release.validate_assets(invalid, "0.3.6")

    def test_checksum_file_must_match_uploaded_archive(self):
        line = "a" * 64 + "  " + release.asset_name("0.3.6")
        with patch.object(release, "output", return_value=line):
            release.validate_checksum("0.3.6", assets())
        with patch.object(release, "output", return_value="wrong"), self.assertRaises(RuntimeError):
            release.validate_checksum("0.3.6", assets())

    def test_completed_rerun_preserves_signed_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            with (patch.object(release, "guard", return_value="source-sha"),
                  patch.object(release.subprocess, "run"),
                  patch.object(release, "release_history", return_value=([ReleasePoint("0.3.6", "source-sha")], {})),
                  patch.object(release, "tag_target", return_value="source-sha"),
                  patch.object(release, "api", return_value=assets()),
                  patch.object(release, "validate_checksum"),
                  patch.object(release, "stamp_version") as stamp,
                  patch.dict(release.os.environ, GITHUB_OUTPUT=str(output))):
                release.prepare()
                stamp.assert_not_called()
                self.assertIn("published=true", output.read_text())
                self.assertIn("release_required=false", output.read_text())

    def test_existing_tag_cannot_be_reassigned(self):
        with (patch.object(release, "guard", return_value="new-sha"),
              patch.object(release.subprocess, "run"),
              patch.object(release, "release_history", return_value=([], {})),
              patch.object(release, "plan_release", return_value=ReleasePlan(True, "0.4.0", "minor")),
              patch.object(release, "tag_target", return_value="old-sha"),
              self.assertRaises(RuntimeError)):
            release.prepare()

    def test_newer_source_cannot_regress_release_series(self):
        with (patch.object(release, "guard", return_value="new-source"),
              patch.object(release.subprocess, "run"),
              patch.object(release, "release_history", return_value=([], {})),
              patch.object(release, "plan_release", return_value=ReleasePlan(True, "0.4.0", "minor")),
              patch.object(release, "tag_target", side_effect=[None, "published-source"]),
              patch.object(release, "api", return_value={"tag_name": "v0.4.10"}),
              patch.object(release, "is_ancestor", return_value=False),
              self.assertRaises(RuntimeError)):
            release.prepare()

    def test_prepare_no_release_has_explicit_skip_outputs_and_no_stamp(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "outputs"
            with (patch.object(release, "guard", return_value="head"),
                  patch.object(release.subprocess, "run"),
                  patch.object(release, "release_history", return_value=([ReleasePoint("0.3.8", "base")], {})),
                  patch.object(release, "commits_since", return_value=(Commit("a" * 40, "docs: usage"),)),
                  patch.object(release, "is_ancestor", return_value=True),
                  patch.object(release, "stamp_version") as stamp,
                  patch.dict(release.os.environ, GITHUB_OUTPUT=str(result))):
                release.prepare()
            stamp.assert_not_called()
            self.assertIn("release_required=false", result.read_text())
            self.assertIn("skip_reason=No feat", result.read_text())

    def test_prepare_new_release_stamps_once_and_emits_generated_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "outputs"
            head = "a" * 40
            with (patch.object(release, "guard", return_value=head),
                  patch.object(release.subprocess, "run"),
                  patch.object(release, "release_history", return_value=([ReleasePoint("0.3.0", "base")], {"0.3.8": None})),
                  patch.object(release, "commits_since", return_value=(Commit(head, "feat: add map details"),)),
                  patch.object(release, "is_ancestor", return_value=True),
                  patch.object(release, "tag_target", return_value=None),
                  patch.object(release, "api", return_value={"tag_name": "v0.3.0"}),
                  patch.object(release, "stamp_version") as stamp,
                  patch.dict(release.os.environ, GITHUB_OUTPUT=str(result), RUNNER_TEMP=directory, GITHUB_RUN_ID="42")):
                release.prepare()
            stamp.assert_called_once_with(Path.cwd(), "0.4.0")
            self.assertIn("release_required=true", result.read_text())
            self.assertIn("tag=v0.4.0", result.read_text())
            self.assertIn("previous_tag=v0.3.0", result.read_text())
            notes = Path(directory) / ("smr-release-notes-" + head + ".md")
            self.assertIn("## Features", notes.read_text())
            self.assertIn("add map details", notes.read_text())

    def test_release_history_reserves_drafts_and_legacy_without_treating_them_as_baseline(self):
        def pages(path):
            return ([{"name": "v0.3.0"}, {"name": "v0.3.7"}] if path == "tags" else [
                {"tag_name": "v0.3.0", "draft": False, "prerelease": False},
                {"tag_name": "v0.3.7", "draft": True, "prerelease": False},
                {"tag_name": "v0.4.0-beta", "draft": False, "prerelease": True}])
        with (patch.object(release, "_api_pages", side_effect=pages),
              patch.object(release, "tag_target", side_effect=lambda tag: "base" if tag == "v0.3.0" else "pending"),
              patch.dict(release.os.environ, SMR_RESERVED_VERSIONS="0.3.7,0.3.8")):
            published, reservations = release.release_history()
        self.assertEqual(published, [ReleasePoint("0.3.0", "base")])
        self.assertEqual(reservations, {"0.3.0": "base", "0.3.7": "pending", "0.3.8": None})

    def test_draft_lookup_falls_back_from_tag_404_to_authenticated_release_id(self):
        draft = dict(assets("0.3.7"), id=123, draft=True)
        def api(path, *, optional=False):
            if path == "releases/tags/v0.3.7":
                self.assertTrue(optional)
                return None
            if path == "releases/123":
                return draft
            self.fail("Unexpected endpoint: " + path)
        with (patch.object(release, "api", side_effect=api),
              patch.object(release, "_api_pages", return_value=[{"tag_name": "v0.3.7", "id": 123}])):
            self.assertEqual(release.release_for_tag("v0.3.7", optional=True), draft)
            self.assertEqual(release.release_for_tag("v0.3.7"), draft)

    def test_required_draft_readback_retries_bounded_propagation(self):
        draft = dict(assets("0.3.7"), id=123, draft=True)
        def api(path, *, optional=False):
            return draft if path == "releases/123" else None
        with (patch.object(release, "api", side_effect=api),
              patch.object(release, "_api_pages", side_effect=[[], [{"tag_name": "v0.3.7", "id": 123}]]),
              patch.object(release.time, "sleep") as sleep):
            self.assertEqual(release.release_for_tag("v0.3.7"), draft)
            sleep.assert_called_once_with(1)
        with (patch.object(release, "api", return_value=None),
              patch.object(release, "_api_pages", return_value=[]),
              patch.object(release.time, "sleep") as sleep):
            with self.assertRaisesRegex(RuntimeError, "bounded retries"):
                release.release_for_tag("v0.3.7")
            self.assertEqual(sleep.call_count, 2)

    def test_release_lookup_rejects_duplicate_or_wrong_tag(self):
        with (patch.object(release, "api", return_value=None),
              patch.object(release, "_api_pages", return_value=[{"tag_name": "v0.3.7", "id": 123}] * 2)):
            with self.assertRaisesRegex(RuntimeError, "Multiple releases"):
                release.release_for_tag("v0.3.7")
        with patch.object(release, "api", return_value={"tag_name": "v9.0.0"}):
            with self.assertRaisesRegex(RuntimeError, "another tag"):
                release.release_for_tag("v0.3.7")


if __name__ == "__main__":
    unittest.main()
