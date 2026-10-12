"""Synthetic complete workflow regressions, never visible UI or real services."""

import importlib.util
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "workflow_fixture", ROOT / "scripts/workflow_fixture.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class WorkflowProviderTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.provider = fixture.WorkflowFixture(temporary.name)
        self.record = self.provider.records[0]
        self.params = dict(
            profile_id=self.record.profile_id,
            **fixture.context(self.record, fixture.BUILD, fixture.SCENARIO),
            attempt_id=fixture.ATTEMPT,
            kind="playable",
            game_id=fixture.GAME,
        )

    def test_closed_provider_refuses_live_operations_and_unselected_save(self):
        for method in (
            "workflow",
            "maptools.play_commit",
            "tools.start",
            "update.install",
            "collector.poll",
        ):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.provider.call(method, self.params)
        with self.assertRaises(ValueError):
            self.provider.call("history.save_digest", {"path": "/unselected/private.sav"})
        self.assertEqual(
            self.provider.call(
                "history.save_digest", {"path": str(self.provider.assets / "synthetic.sav")}
            )["save_id"],
            fixture.SAVE,
        )

    def test_history_is_real_validation_and_annotations_do_not_change_it(self):
        before = self.provider.store.records()
        with self.assertRaisesRegex(ValueError, "Choose the scenario"):
            self.provider.call("history.record", {**self.params, "scenario": ""})
        self.assertEqual(self.provider.store.records(), before)
        self.provider.call("history.record", self.params)
        rows = self.provider.store.records()
        self.assertEqual(len(rows), len(before) + 1)
        self.provider.call(
            "annotations.save",
            {**self.params, "title": "Personal synthetic title", "notes": "New note"},
        )
        self.assertEqual(self.provider.store.records(), rows)
        self.assertEqual(
            self.provider.presentation(self.record)["metadata"]["author"], "Synthetic Author"
        )
        with self.assertRaisesRegex(ValueError, "edition or game build changed"):
            self.provider.call("annotations.save", {**self.params, "variant_id": "changed"})

    def test_native_partial_append_has_unknown_prefix_and_prewrite_validation_does_not(self):
        self.provider.case = "unknown"
        with self.assertRaisesRegex(RuntimeError, "History outcome needs inspection") as failed:
            self.provider.call("history.record", self.params)
        self.assertEqual(type(failed.exception.__cause__).__name__, "HistoryWriteOutcomeUnknown")
        with self.assertRaisesRegex(ValueError, "Interrupted history append"):
            self.provider.store.records()

    @skipUnless(platform.system() == "Darwin", "Native checks require macOS")
    def test_compiled_image_drop_with_closed_temporary_provider(self):
        self.compile_native("ImageDropChecks", "src/native/NativePersonalImageDrop.swift")

    @skipUnless(platform.system() == "Darwin", "Native checks require macOS")
    def test_compiled_reporting_forms_with_closed_temporary_provider(self):
        self.compile_native("ReportingChecks", "src/native/NativeOutcomeView.swift")

    def compile_native(self, name, view):
        with TemporaryDirectory() as directory:
            executable = Path(directory) / name
            subprocess.run(
                [
                    "xcrun",
                    "swiftc",
                    "-parse-as-library",
                    "-swift-version",
                    "5",
                    "-target",
                    "arm64-apple-macosx13.0",
                    "src/native/Backend.swift",
                    "src/native/LauncherModel.swift",
                    view,
                    f"tests/native/{name}.swift",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                check=True,
                timeout=90,
            )
            subprocess.run(
                [str(executable), sys.executable, str(ROOT / "scripts/workflow_fixture.py")],
                cwd=ROOT,
                check=True,
                timeout=45,
            )
