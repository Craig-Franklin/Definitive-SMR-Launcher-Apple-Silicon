"""Exercise the maintained read-only bank entry point with invented assets."""

from contextlib import redirect_stdout
from dataclasses import asdict, replace
from io import StringIO
import json
import shutil
import unittest
from unittest.mock import patch

from smr_launcher import cli
from smr_launcher.application import ApplicationError, LauncherApplication, MapRecord
from smr_launcher.variants import assets_manifest_hash
import test_bank_applicability
from test_recipe_service import sha


class BankInspectionCliTests(unittest.TestCase):
    def setUp(self):
        self.bank = test_bank_applicability.BankApplicabilityTests()
        self.bank.setUp()
        self.addCleanup(self.bank.doCleanups)
        f = self.bank.fixture
        installation = f.app.installation
        installation.profile_root = f.root / "live"
        installation.check_current = lambda: None
        installation.game_running = lambda: False
        self.app = LauncherApplication(installation, f.root / "library")
        self.app.imports, self.app.originals = f.imports, f.originals
        self.app._stock_file_context = f.app._stock_file_context
        prepared = self.app.prepared / ("c" * 32)
        shutil.copytree(f.source, prepared, copy_function=shutil.copy2)
        identity = dict(
            schema=1,
            recipe="original",
            archive=f.ARCHIVE,
            game=f.GAME,
            assets=assets_manifest_hash(prepared),
        )
        variant = sha(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())
        self.record = MapRecord(
            "Invented original",
            f.ARCHIVE,
            variant,
            "map-" + variant,
            (f.SCENARIO_PATH,),
            prepared.name,
            f.GAME,
            f.source.name,
        )
        self.write_records([self.record])
        with self.app._locked(recover_removal=False):
            pass
        self.paths = dict(
            scenario_path=f.SCENARIO_PATH,
            industry_path=f.INDUSTRY_PATH,
            model_path=self.bank.model_path,
        )
        self.argv = [
            "inspect-bank",
            self.record.profile_id,
            "--scenario",
            f.SCENARIO_PATH,
            "--industry",
            f.INDUSTRY_PATH,
            "--model",
            self.bank.model_path,
        ]

    def write_records(self, records):
        self.app.library.mkdir(exist_ok=True)
        self.app.catalogue_file.write_text(
            json.dumps(dict(schema=1, maps=[r.as_json() for r in records]))
        )

    def test_real_cli_assessment_preserves_sources_and_never_queries_status_or_recovers(self):
        expected = asdict(self.bank.inspect())
        before = self.bank.fixture.fixture_file_facts()
        catalogue = self.app.catalogue_file.read_bytes()
        with (
            patch.object(LauncherApplication, "discover", return_value=self.app),
            patch.object(cli, "_status", side_effect=AssertionError("unrelated status scan")),
            patch.object(self.app.profiles, "recover", side_effect=AssertionError("recovery")),
            patch.object(self.app, "play", side_effect=AssertionError("launch")),
        ):
            result = cli.run(self.argv)
        self.assertEqual(result, expected)
        self.assertEqual(result["status"], "source-candidate")
        self.assertIn("native-provider-selection", result["unknowns"])
        self.assertEqual(self.bank.fixture.fixture_file_facts(), before)
        self.assertEqual(self.app.catalogue_file.read_bytes(), catalogue)
        self.assertFalse(self.app.profiles.state_file.exists())

    def test_missing_duplicate_and_nonoriginal_profiles_refuse(self):
        for records, error in (
            ([], "unique current original"),
            ([self.record, self.record], "duplicate variants"),
            ([replace(self.record, variant_id="f" * 64)], "unique current original"),
        ):
            with self.subTest(records=records):
                self.write_records(records)
                with self.assertRaisesRegex(ApplicationError, error):
                    self.app.inspect_bank_candidate(self.record.profile_id, **self.paths)

    def test_running_or_unknown_game_and_pending_work_refuse_without_recovery(self):
        for state in (True, None):
            with (
                self.subTest(running=state),
                patch.object(self.app.installation, "game_running", return_value=state),
            ):
                with self.assertRaisesRegex(ApplicationError, "Quit Railroads"):
                    self.app.inspect_bank_candidate(self.record.profile_id, **self.paths)
        for pending in (self.app.profiles.journal, self.app.library / "removal.json"):
            with self.subTest(pending=pending):
                pending.parent.mkdir(parents=True, exist_ok=True)
                pending.write_bytes(b"owned pending operation")
                with self.assertRaisesRegex(ApplicationError, "requires its owner"):
                    self.app.inspect_bank_candidate(self.record.profile_id, **self.paths)
                self.assertEqual(pending.read_bytes(), b"owned pending operation")
                pending.unlink()

    def test_cli_retains_case_unknown_and_returns_structured_operational_refusal(self):
        path = self.bank.fixture.source / self.bank.fixture.INDUSTRY_PATH
        path.write_bytes(path.read_bytes().replace(b"bank.nif", b"BANK.NIF"))
        with patch.object(LauncherApplication, "discover", return_value=self.app):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(self.argv), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["status"], "unknown")
            self.assertIn("reference-case-szModel", result["data"]["unknowns"])
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(self.argv[:-1] + ["../outside.nif"]), 2)
            result = json.loads(output.getvalue())
            self.assertFalse(result["ok"])
            self.assertIn("absent from source context", result["error"])


if __name__ == "__main__":
    unittest.main()
