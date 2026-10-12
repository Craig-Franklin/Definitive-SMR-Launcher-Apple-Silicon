"""Independent synthetic storage outcomes; no engine or runtime acceptance."""

from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO, StringIO
import hashlib
import json
import os
import tarfile
import unittest
from unittest.mock import patch

import test_application as fixtures
from smr_launcher import cli, recipe_service
from smr_launcher.activation import ORIGINAL
from smr_launcher.application import ApplicationError, LauncherApplication
from smr_launcher.recipe_identity import map_file_context


class RecipeBatchPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApplicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        alpha, rule, binding, self.stock = f.compiled_fixture()
        self.app = f.application
        beta_archive = f.root / "Beta.tar"
        with tarfile.open(beta_archive, "w") as stream:
            for name, data in (
                (
                    "UserMaps/Example/RRT_Scenario_User_Example.xml",
                    b'<Scenario id="invented-beta"/>',
                ),
                ("CustomAssets/Example.txt", b"map asset"),
            ):
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                stream.addfile(entry, BytesIO(data))
        beta = self.app.import_archive(beta_archive)
        self.originals = (alpha, beta)
        self.inputs = {
            alpha.profile_id: (rule, binding),
            beta.profile_id: (
                f.compatibility_rule(beta, rule_id="synthetic-beta-repair"),
                replace(
                    binding,
                    recipe_id="invented-beta-v1",
                    recipe_sha256=hashlib.sha256(b"invented beta recipe").hexdigest(),
                    source_files_sha256=map_file_context(
                        self.app.imports / beta.imported_directory
                    )["identity"],
                ),
            ),
        }
        self.requests = [
            dict(profile_id=r.profile_id, recipe_id=self.inputs[r.profile_id][1].recipe_id)
            for r in self.originals
        ]
        self.addCleanup(patch.stopall)
        patch.object(recipe_service, "resolve_bundled_recipe", side_effect=self.resolve).start()
        patch.object(recipe_service, "plan_recipe_job", side_effect=self.project).start()

    def resolve(self, app, record, recipe):
        rule, binding = self.inputs[record.profile_id]
        if recipe != binding.recipe_id:
            raise ApplicationError("Unsupported invented recipe")
        return (rule,), binding

    def project(self, app, record, recipe):
        _, binding = self.resolve(app, record, recipe)
        return dict(binding=binding.as_json())

    def facts(self):
        return self.fixture.snapshot_files(self.app.library), self.fixture.snapshot_files(
            self.app.profiles.live
        )

    def test_two_originals_exact_outputs_repeat_and_saves(self):
        original_facts = [
            self.fixture.snapshot_files(p) for p in (self.app.imports, self.app.originals)
        ]
        live = self.fixture.snapshot_files(self.app.profiles.live)
        result = self.app.prepare_recipe_batch(self.requests)
        self.assertTrue(result["all_prepared"])
        editions = [j["edition"] for j in result["jobs"]]
        for key in ("variant_id", "profile_id", "prepared_directory"):
            self.assertNotEqual(editions[0][key], editions[1][key])
        for index, job in enumerate(result["jobs"]):
            edition = job["edition"]
            self.assertEqual(
                (
                    self.app.prepared / edition["prepared_directory"] / "CustomAssets/Example.txt"
                ).read_bytes(),
                b"compatible map asset",
            )
            self.assertEqual(job["receipt"]["parent_variant"], self.originals[index].variant_id)
            self.assertEqual(
                job["receipt"]["recipe_binding"]["recipe_id"], self.requests[index]["recipe_id"]
            )
            saved = self.app.profiles._profile_path(edition["profile_id"]) / "Saves/probe.sav"
            saved.write_bytes((b"alpha current save", b"beta future save")[index])
        before = self.facts()
        repeat = self.app.prepare_recipe_batch(self.requests)
        self.assertTrue(repeat["all_prepared"])
        self.assertTrue(all(j["reused"] for j in repeat["jobs"]))
        self.assertEqual([j["edition"] for j in repeat["jobs"]], editions)
        self.assertEqual(self.facts(), before)
        self.assertEqual(
            [self.fixture.snapshot_files(p) for p in (self.app.imports, self.app.originals)],
            original_facts,
        )
        self.assertEqual(self.fixture.snapshot_files(self.app.profiles.live), live)
        self.assertEqual(self.app.profiles._state()["active"], ORIGINAL)
        self.assertFalse(self.app.verification_store.path.exists())

    def test_later_unsupported_and_duplicate_preflight_write_nothing(self):
        for requests in (
            self.requests + [dict(self.requests[0], recipe_id="unknown")],
            self.requests * 2,
        ):
            before = self.facts()
            result = self.app.prepare_recipe_batch(requests)
            self.assertFalse(result["all_prepared"])
            self.assertFalse(any(j["status"] == "prepared" for j in result["jobs"]))
            self.assertEqual(self.facts(), before)

    def test_second_execution_failure_retains_first_and_stops(self):
        third = dict(self.requests[0], recipe_id="third-invented")
        actual = self.app._create_compatibility_edition_locked
        calls = []

        def create(record, *args, **kwargs):
            calls.append(record)
            if len(calls) == 2:
                raise OSError("injected second execution failure")
            return actual(record, *args, **kwargs)

        with (
            patch.object(recipe_service, "plan_recipe_job", return_value={}),
            patch.object(self.app, "_create_compatibility_edition_locked", side_effect=create),
        ):
            result = self.app.prepare_recipe_batch(self.requests + [third])
        self.assertEqual(
            [j["status"] for j in result["jobs"]], ["prepared", "failed", "not_attempted"]
        )
        self.assertTrue(result["partial"])
        self.assertEqual(len(calls), 2)
        self.assertIn(result["jobs"][0]["edition"], [r.as_json() for r in self.app.catalogue()])

    def test_source_and_stock_byte_drift_never_reuse(self):
        self.assertTrue(self.app.prepare_recipe_batch(self.requests)["all_prepared"])
        source = (
            self.app.imports / self.originals[0].imported_directory / "CustomAssets/Example.txt"
        )
        timestamp = source.stat()
        source.chmod(0o600)
        source.write_bytes(b"changed source bytes")
        before = self.facts()
        result = self.app.prepare_recipe_batch(self.requests)
        self.assertEqual(result["jobs"][0]["status"], "refused")
        self.assertEqual(self.facts(), before)
        source.write_bytes(b"map asset")
        os.utime(source, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
        (self.stock / "synthetic.xml").write_bytes(b"changed stock bytes")
        result = self.app.prepare_recipe_batch(self.requests)
        self.assertEqual(result["jobs"][0]["status"], "failed")
        self.assertNotIn("edition", result["jobs"][0])

    def test_pending_journals_and_dangling_links_preserve_layout(self):
        for pending in (self.app.library / "removal.json", self.app.profiles.journal):
            for symlink in (False, True):
                with self.subTest(pending=pending, symlink=symlink):
                    if symlink:
                        pending.symlink_to("invented-missing-journal")
                    else:
                        pending.write_bytes(b"invented pending journal")
                    before = self.facts()
                    with (
                        patch(
                            "smr_launcher.application.removal.resume",
                            side_effect=AssertionError("must not recover"),
                        ),
                        patch.object(
                            self.app.profiles,
                            "_clear_journal",
                            side_effect=AssertionError("must not recover"),
                        ),
                    ):
                        if pending.name == "removal.json":
                            with self.assertRaises(ApplicationError):
                                self.app.prepare_recipe_batch(self.requests)
                        else:
                            self.assertEqual(
                                self.app.prepare_recipe_batch(self.requests)["jobs"][0]["status"],
                                "failed",
                            )
                    self.assertEqual(self.facts(), before)
                    self.assertTrue(pending.is_symlink() if symlink else pending.is_file())
                    pending.unlink()

    def test_activation_journal_appearing_at_registration_refuses(self):
        actual = self.app.profiles.register_variant

        def register(*args, **kwargs):
            self.app.profiles.journal.write_bytes(b"invented during preparation")
            return actual(*args, **kwargs)

        with patch.object(self.app.profiles, "register_variant", side_effect=register):
            result = self.app.prepare_recipe_batch(self.requests)
        self.assertEqual([j["status"] for j in result["jobs"]], ["failed", "not_attempted"])
        self.assertEqual(self.app.profiles.journal.read_bytes(), b"invented during preparation")
        self.assertEqual(self.app.catalogue(), self.originals)

    def test_existing_receipt_and_prepared_output_corruption_refuse(self):
        result = self.app.prepare_recipe_batch(self.requests)
        edition = result["jobs"][0]["edition"]
        output = self.app.prepared / edition["prepared_directory"] / "CustomAssets/Example.txt"
        output.chmod(0o600)
        output.write_bytes(b"corrupt prepared output")
        refused = self.app.prepare_recipe_batch(self.requests)
        self.assertEqual(refused["jobs"][0]["status"], "failed")
        receipt = (
            self.app.library / "compatibility-preparations" / (edition["variant_id"] + ".json")
        )
        receipt.write_text("{}")
        refused = self.app.prepare_recipe_batch(self.requests)
        self.assertEqual(refused["jobs"][0]["status"], "failed")

    def test_removal_appearing_after_app_lock_refuses_without_recovery(self):
        import fcntl

        actual = fcntl.flock
        acquisitions = []

        def lock(fd, operation):
            actual(fd, operation)
            if operation == fcntl.LOCK_EX:
                acquisitions.append(fd)
                if len(acquisitions) == 2:  # Execution lock follows planning lock.
                    (self.app.library / "removal.json").write_bytes(b"invented concurrent removal")

        with (
            patch("smr_launcher.application.fcntl.flock", side_effect=lock),
            patch(
                "smr_launcher.application.removal.resume",
                side_effect=AssertionError("must not recover"),
            ),
        ):
            result = self.app.prepare_recipe_batch(self.requests)
        self.assertEqual([j["status"] for j in result["jobs"]], ["failed", "not_attempted"])
        self.assertEqual(
            (self.app.library / "removal.json").read_bytes(), b"invented concurrent removal"
        )
        self.assertEqual(self.app.catalogue(), self.originals)

    def test_fresh_execution_binding_replaces_planning_context(self):
        original_project = self.project

        def project(app, original, recipe):
            projection = original_project(app, original, recipe)
            if original == self.originals[-1]:
                (self.stock / "synthetic.xml").write_bytes(b"fresh stock context")
                for profile, (rule, binding) in self.inputs.items():
                    self.inputs[profile] = (
                        rule,
                        replace(
                            binding, stock_files_sha256=self.app._stock_file_context()["identity"]
                        ),
                    )
            return projection

        with patch.object(recipe_service, "plan_recipe_job", side_effect=project):
            result = self.app.prepare_recipe_batch(self.requests)
        self.assertTrue(result["all_prepared"])
        for job in result["jobs"]:
            self.assertEqual(
                job["binding"]["stock_files_sha256"], self.app._stock_file_context()["identity"]
            )

    def test_later_stock_drift_invalidates_earlier_success_at_final_audit(self):
        actual = self.app._create_compatibility_edition_locked
        calls = []

        def create(*args, **kwargs):
            edition = actual(*args, **kwargs)
            calls.append(edition)
            if len(calls) == 2:
                (self.stock / "synthetic.xml").write_bytes(b"stock drift after second publication")
            return edition

        with patch.object(self.app, "_create_compatibility_edition_locked", side_effect=create):
            result = self.app.prepare_recipe_batch(self.requests)
        self.assertFalse(result["all_prepared"])
        self.assertTrue(result["partial"])
        self.assertTrue(result["jobs"][0]["audit_failed"])
        self.assertEqual([j["status"] for j in result["jobs"]], ["failed", "failed"])
        self.assertTrue(all(record in self.app.catalogue() for record in calls))

    def test_activation_link_appearing_under_owner_lock_is_preserved(self):
        actual = self.app.profiles._locked
        entries = []

        @contextmanager
        def locked(*args, **kwargs):
            with actual(*args, **kwargs):
                entries.append(True)
                if len(entries) == 3:  # Registration, after both stopped-owner checks.
                    self.app.profiles.journal.symlink_to("invented-missing-journal")
                yield

        with patch.object(self.app.profiles, "_locked", side_effect=locked):
            result = self.app.prepare_recipe_batch(self.requests)
        self.assertEqual([j["status"] for j in result["jobs"]], ["failed", "not_attempted"])
        self.assertTrue(self.app.profiles.journal.is_symlink())
        self.assertEqual(os.readlink(self.app.profiles.journal), "invented-missing-journal")
        self.assertEqual(self.app.catalogue(), self.originals)

    def test_late_activation_journal_never_enters_recovery(self):
        pending = self.app.profiles.journal
        payload = json.dumps(
            dict(schema=1, source=ORIGINAL, target=self.originals[0].profile_id)
        ).encode()
        for boundary in ("_state", "_marker"):
            with self.subTest(boundary=boundary):
                actual = getattr(self.app.profiles, boundary)
                injected = []

                def inject(*args, **kwargs):
                    value = actual(*args, **kwargs)
                    if not injected:
                        pending.write_bytes(payload)
                        injected.append(self.facts())
                    return value

                try:
                    with patch.object(self.app.profiles, boundary, side_effect=inject):
                        result = self.app.prepare_recipe_batch(self.requests)
                    self.assertEqual(
                        [job["status"] for job in result["jobs"]],
                        ["failed", "not_attempted"],
                    )
                    self.assertEqual(pending.read_bytes(), payload)
                    self.assertEqual(self.facts(), injected[0])
                finally:
                    pending.unlink(missing_ok=True)

    def test_later_source_drift_withdraws_earlier_batch_success(self):
        actual = self.app._create_compatibility_edition_locked
        source = (
            self.app.imports / self.originals[0].imported_directory / "CustomAssets/Example.txt"
        )
        editions = []

        def create(*args, **kwargs):
            edition = actual(*args, **kwargs)
            editions.append(edition)
            if len(editions) == 2:
                source.chmod(0o600)
                source.write_bytes(b"source drift after independent later publication")
            return edition

        with patch.object(self.app, "_create_compatibility_edition_locked", side_effect=create):
            result = self.app.prepare_recipe_batch(self.requests)
        self.assertFalse(result["all_prepared"])
        self.assertTrue(result["partial"])
        self.assertEqual([job["status"] for job in result["jobs"]], ["failed", "prepared"])
        self.assertTrue(result["jobs"][0]["audit_failed"])
        self.assertEqual(result["jobs"][0]["edition"], editions[0].as_json())
        self.assertTrue(all(edition in self.app.catalogue() for edition in editions))

    def test_cli_explicit_requests_and_error_plumbing(self):
        args = ["prepare-recipes"]
        for request in self.requests:
            args.extend(["--request", request["profile_id"], request["recipe_id"]])
        with (
            patch.object(LauncherApplication, "discover", return_value=self.app),
            patch.object(cli, "_status", side_effect=AssertionError("must not query status")),
        ):
            self.assertTrue(cli.run(args)["all_prepared"])
            with (
                patch.object(
                    self.app,
                    "prepare_recipe_batch",
                    side_effect=ApplicationError("invented refusal"),
                ),
                patch("sys.stdout", new_callable=StringIO) as output,
            ):
                self.assertEqual(cli.main(args), 2)
                self.assertEqual(
                    json.loads(output.getvalue()), dict(ok=False, error="invented refusal")
                )
