"""Command bridge storage operations use the real application guards."""

from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import test_application as fixture
from smr_launcher import cli


class CompactCommandBaselineTests(unittest.TestCase):
    setUp = fixture.ApplicationTests.setUp

    def test_status_is_readonly_for_existing_import_and_original_profile(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        before = fixture.ApplicationTests.snapshot_files(app.library)
        with patch.object(cli.LauncherApplication, "discover", return_value=app):
            result = cli.run(["status"])
        self.assertEqual(result["maps"][0]["profile_id"], record.profile_id)
        self.assertEqual(fixture.ApplicationTests.snapshot_files(app.library), before)

    def test_plan_apply_prepare_and_rollback_use_private_manifest_file(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        manifest = self.root / "private-plan.json"
        with patch.object(cli.LauncherApplication, "discover", return_value=app):
            planned = cli.run(["compact-plan", "--output", str(manifest)])
            self.assertTrue(planned["planned"])
            self.assertTrue(manifest.is_file())
            cli.run(["compact-apply", "--plan", str(manifest)])
            self.assertFalse(app._prepared_root(record).exists())
            cli.run(["prepare-map", record.profile_id])
            self.assertTrue(app._prepared_root(record).exists())
            cli.run(["compact-rollback", "--plan", str(manifest)])
        self.assertEqual(json.loads(manifest.read_bytes())["schema"], 1)
        self.assertFalse((app.library / "compact-library.json").exists())
