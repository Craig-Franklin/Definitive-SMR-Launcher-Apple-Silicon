"""Synthetic release version and publication gates; no credentials or network."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_macos as release


def assets(version="0.3.6"):
    name = release.asset_name(version)
    return {"draft": False, "prerelease": False, "assets": [
        {"name": name, "state": "uploaded", "size": 100, "digest": "sha256:" + "a" * 64},
        {"name": name + ".sha256", "state": "uploaded", "size": 120},
    ]}


class ReleaseTests(unittest.TestCase):
    def test_checkout_origin_accepts_only_exact_personal_fork(self):
        for suffix in ("", ".git"):
            release.validate_origin("https://github.com/" + release.REPO + suffix)
        for url in ("https://github.com/ageekhere/Definitive-SMR-Launcher",
                    "https://github.com/" + release.REPO + "/other",
                    "https://github.com.example/" + release.REPO,
                    "https://user:token@github.com/" + release.REPO):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                release.validate_origin(url)

    def test_run_version_is_stable_and_monotonic(self):
        self.assertEqual(release.release_version("0.3.0", "6"), "0.3.6")
        self.assertEqual(release.release_version("0.3.0", "6"), "0.3.6")
        self.assertGreater(release.version_tuple("0.3.10"), release.version_tuple("0.3.9"))
        self.assertGreater(release.version_tuple("0.4.1"), release.version_tuple("0.3.100"))

    def test_invalid_or_collision_prone_versions_rejected(self):
        for base, run in [("0.3.1", "6"), ("0.3.0", "0"), ("0.3.0", "01"),
                          ("0.3.0", "6\n"), ("0.3.0-beta", "6")]:
            with self.subTest(base=base, run=run), self.assertRaises(ValueError):
                release.release_version(base, run)

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
        release.validate_assets(assets(), "0.3.6", "a" * 64)
        incomplete = assets()
        incomplete["assets"].pop()
        with self.assertRaises(RuntimeError):
            release.validate_assets(incomplete, "0.3.6")
        with self.assertRaises(RuntimeError):
            release.validate_assets(assets(), "0.3.6", "b" * 64)
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
                  patch.object(release, "tag_target", return_value="source-sha"),
                  patch.object(release, "api", return_value=assets()),
                  patch.object(release, "validate_checksum"),
                  patch.object(release, "stamp_version") as stamp,
                  patch.dict(release.os.environ, GITHUB_RUN_NUMBER="6", GITHUB_OUTPUT=str(output))):
                release.prepare()
                stamp.assert_not_called()
                self.assertIn("published=true", output.read_text())

    def test_existing_tag_cannot_be_reassigned(self):
        with (patch.object(release, "guard", return_value="new-sha"),
              patch.object(release, "tag_target", return_value="old-sha"),
              patch.dict(release.os.environ, GITHUB_RUN_NUMBER="6"),
              self.assertRaises(RuntimeError)):
            release.prepare()

    def test_newer_source_cannot_regress_release_series(self):
        with (patch.object(release, "guard", return_value="new-source"),
              patch.object(release, "tag_target", side_effect=[None, "published-source"]),
              patch.object(release, "api", side_effect=[None, {"tag_name": "v0.4.10"}]),
              patch.object(release.subprocess, "run") as ancestor,
              patch.dict(release.os.environ, GITHUB_RUN_NUMBER="6"),
              self.assertRaises(RuntimeError)):
            ancestor.return_value.returncode = 1
            release.prepare()


if __name__ == "__main__":
    unittest.main()
