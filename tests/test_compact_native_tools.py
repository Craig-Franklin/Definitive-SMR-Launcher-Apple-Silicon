"""Bundled compact-storage operations use the existing native job bridge."""

from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import test_application as fixture
import test_native_tools as native_fixture
from smr_launcher.native_tools import NativeTools


class CompactNativeBaselineTests(unittest.TestCase):
    setUp = fixture.ApplicationTests.setUp

    def tools(self):
        app = self.application
        app.setup()
        record = app.import_archive(self.archive)
        owner = native_fixture.owner(self.root)
        owner.app = app
        return NativeTools(owner), record

    def test_unknown_operation_and_bad_cancellation_never_start_or_mutate(self):
        tools, _ = self.tools()
        before = fixture.ApplicationTests.snapshot_files(self.application.library)
        with self.assertRaisesRegex(ValueError, "Unknown native tool action"):
            tools.start("unrecognized", {})
        with self.assertRaises(ValueError):
            tools.cancel("unknown")
        tools.owner._background.assert_not_called()
        self.assertEqual(fixture.ApplicationTests.snapshot_files(self.application.library), before)

    def test_bundled_plan_apply_prepare_and_rollback_keep_raw_manifest_out_of_status(self):
        tools, record = self.tools()
        app = self.application
        tools.start("library.compact.plan", {})
        native_fixture.finish(tools.owner)
        projected = tools.status()["compact_proposal"]
        seal = projected["sha256"]
        self.assertEqual(len(seal), 64)
        self.assertNotIn("entries", json.dumps(projected))
        self.assertNotIn("RRT_Scenario", json.dumps(projected))
        path = app.library / "compact-proposals" / (seal + ".json")
        self.assertTrue(path.is_file())
        for params in ({}, {"consent": True, "plan_sha256": "../escape"}):
            with self.assertRaises(ValueError):
                tools.start("library.compact.apply", params)
        tools.start("library.compact.apply", dict(consent=True, plan_sha256=seal))
        native_fixture.finish(tools.owner)
        self.assertFalse(app.profiles._profile_path(record.profile_id).exists())
        tools.start("library.prepare", dict(consent=True, profile_id=record.profile_id))
        native_fixture.finish(tools.owner)
        self.assertTrue(app.profiles._profile_path(record.profile_id).is_dir())
        tools.start("library.compact.rollback", dict(consent=True, plan_sha256=seal))
        native_fixture.finish(tools.owner)
        self.assertFalse((app.library / "compact-library.json").exists())

    def test_native_cancellation_never_reaches_compaction_or_launch(self):
        tools, _ = self.tools()
        tools.start("library.compact.plan", {})
        tools.cancel(tools.job["job_id"])
        native_fixture.finish(tools.owner)
        self.assertEqual(tools.job["state"], "cancelled")
        self.assertFalse((self.application.library / "compact-library.json").exists())
        self.assertEqual(tools.status()["compact_proposal"], {})

    def test_native_apply_binds_exact_retained_proposal(self):
        tools, _ = self.tools()
        tools.start("library.compact.plan", {})
        native_fixture.finish(tools.owner)
        seal = tools.status()["compact_proposal"]["sha256"]
        path = self.application.library / "compact-proposals" / (seal + ".json")
        path.write_text("{}")
        tools.start("library.compact.apply", dict(consent=True, plan_sha256=seal))
        operation, callback = tools.owner._background.call_args.args
        with self.assertRaisesRegex(ValueError, "identity"):
            operation()
        self.assertFalse((self.application.library / "compact-library.json").exists())
