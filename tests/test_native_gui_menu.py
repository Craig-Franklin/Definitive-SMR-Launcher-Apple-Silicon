"""Closed GUI dispatch controls; synthetic data, no launcher or game execution."""

from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/research")]
import agent_driver as driver
import catalogue_menu as catalogue
import native_gui_menu as gui
from active_job_control import ControlRefused
import test_catalogue_menu as fixtures
from test_agent_driver import Process


def simulated_229(test, job):
    """Seal temporary provider bytes while retaining the actual 229 validator."""
    path = Path(job["native_source"])
    expected = dict(driver.PREINPUT_229)
    expected["native_helper_path"] = str(path)
    digest = gui.sha(path.read_bytes())
    for key in (
        "native_helper_sha256",
        "native_source_sha256",
        "proof_sha256",
        "release_match_sha256",
        "request_loop_source_sha256",
    ):
        expected[key] = digest
    job["preinput_refusal_contract"] = dict(
        expected,
        proof_path=str(path),
        release_match_path=str(path),
        request_loop_source_path=str(path),
    )
    test.enterContext(patch.object(driver, "PREINPUT_229", expected))


def scroll_detail_tree(name, paragraph=0):
    return (
        "0 standard window Map Library\n"
        "\t20 scroll area, Secondary Actions: scroll up, scroll down\n"
        "\t\t21 text Gallery\n"
        "\t30 scroll area, Secondary Actions: scroll up, scroll down\n"
        f"\t\t31 text {name}\n"
        "\t\t32 pop up button, Value: Issues & Evidence\n"
        "\t\t33 text Known issue\n"
        f"\t\t34 text Synthetic historical evidence paragraph {paragraph}\n"
    )


def review_tree(owner, *, initial, flatten=True):
    body = gui.review_body(owner, initial=initial)
    if flatten:
        body = " ".join(body.split())
    button = (
        "10 button Play Original Edition" if initial else "11 button Launch This Edition Anyway"
    )
    return (
        "0 standard window Map Library\n\t92 sheet alert\n\t\t94 text " + body + "\n\t\t" + button
    )


class GUIControls(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.CatalogueRestoreTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        common = SimpleNamespace(UKCalibration=SimpleNamespace)
        base = type(
            "Base", (), {"stop": lambda owner: self.fail("Unknown GUI reached common stop")}
        )
        catalogue_class = type("Catalogue", (base,), {})
        cls = gui.pilot_class(common, catalogue_class)
        self.owner = cls.__new__(cls)
        owner = self.owner
        owner.__dict__.update(vars(fixture.owner))
        owner.job = dict(fixture.owner.job, purpose=gui.PURPOSE, game_sha256="c" * 64)
        owner.config = dict(fixture.owner.config)
        owner.config["target"] = dict(
            owner.config["target"], variant_id="b" * 64, archive_sha256="a" * 64
        )
        owner.config["warning"] = "Final original warning."
        owner.gui = dict(display_name="Synthetic Canyon", initial_warning="Initial cached warning.")
        owner.frontend = Process(21, 101, "/synthetic/frontend")
        owner.backend = Process(22, 102, "/synthetic/backend")
        self.alive = {21: owner.frontend, 22: owner.backend}
        owner.monitor = SimpleNamespace(inspect=self.alive.get, games=lambda: [])
        owner.gui_phase = owner.gui_launch_started = True
        owner.gui_selection_bound = owner.gui_play_sent = owner.gui_original_sent = False
        owner.gui_confirm_sent = owner.gui_quit_sent = owner.gui_bound = owner.gui_closed = False
        owner.dispatch_settled = False
        owner.gui_scroll_dispatches = 0
        owner.gui_scroll_permit = None
        owner.frame_started = time.monotonic()
        owner.observer = SimpleNamespace(poll=lambda: None)
        owner.open_child = SimpleNamespace(poll=lambda: 0)
        owner.verify_cleanup_owner = lambda: None
        self.frame = dict(
            treeText="",
            window=dict(id=33, width=1000, height=800),
            snapshot="fresh",
            sha256="d" * 64,
        )

    def selected(self):
        self.frame["treeText"] = (
            "Synthetic Canyon\nArchive SHA-256: "
            + "a" * 64
            + "\nVariant: "
            + "b" * 64
            + "\nExecutable SHA-256: "
            + "c" * 64
            + "\n9 button\n"
        )
        return self.frame

    def decision(self, action=(), receiver="owned_window"):
        return dict(action=list(action), receiver=receiver)

    def local_config(self):
        job, admission = self.fixture.entry_contract(gui.PURPOSE)
        config = job["catalogue_menu"]
        config.pop("selection")
        config.pop("preparation")
        config["prepare_compatibility"] = False
        config["warning"] = "Final synthetic warning"
        root = self.fixture.owner.directory / "development-source"
        root.mkdir()
        (root / "requirements-build.txt").write_text("SyntheticBuildTool==1.0\n")
        self.enterContext(patch.object(gui, "SOURCE_ROOT", root))
        snapshot = dict(base=gui.LOCAL_BASE, files={"synthetic": {"sha256": "a" * 64}})
        self.snapshot = self.enterContext(
            patch.object(gui, "local_source_snapshot", return_value=snapshot)
        )
        bundle = root / ".local/native-gui-development/Definitive SMR Launcher Apple Silicon.app"
        front = bundle / "Contents/MacOS/SMRLauncher"
        helper = front.with_name("Definitive SMR Launcher Apple Silicon")
        front.parent.mkdir(parents=True)
        front.write_bytes(b"synthetic frontend; never executed")
        helper.write_bytes(b"synthetic backend; never executed")
        self.local_receipt = dict(
            artifact_mode="local-development",
            source_root=str(root),
            source_snapshot=snapshot,
            build=dict(
                command=["./scripts/build_macos.sh"],
                returncode=0,
                signing="ad-hoc",
                dist_root=str(bundle.parent),
                dependencies={"SyntheticBuildTool": "1.0"},
            ),
            bundle_sha256=gui.local_bundle_digest(bundle),
            frontend_sha256=gui.sha(front.read_bytes()),
            executable_sha256=gui.sha(helper.read_bytes()),
        )
        self.local_receipt_path = root / "build-receipt.json"
        config["package"] = dict(
            artifact_mode="local-development",
            bundle=str(bundle),
            executable=str(helper),
            executable_sha256=gui.sha(helper.read_bytes()),
            receipt_path=str(self.local_receipt_path),
        )
        config["native_gui"] = dict(
            display_name="Synthetic Canyon",
            initial_warning="Initial synthetic warning",
            frontend_sha256=gui.sha(front.read_bytes()),
            launcher_files={},
        )
        self.save_local_receipt(config)
        job["agent_save"] = dict(
            token="SyntheticToken12345", filename="SyntheticToken12345.SMRailroadsSavedGame"
        )
        simulated_229(self, job)
        return job, admission

    def save_local_receipt(self, config):
        self.local_receipt_path.write_bytes(catalogue.canonical(self.local_receipt))
        config["package"]["receipt_sha256"] = gui.sha(self.local_receipt_path.read_bytes())

    def test_local_artifact_actual_entry_and_wrong_purpose_refusal(self):
        job, admission = self.local_config()
        driver.verify_entry(job, admission, "a" * 64)
        job["purpose"] = catalogue.DIRECT_LOAD_PURPOSE
        with self.assertRaisesRegex(ControlRefused, "native GUI menu-only"):
            gui.verify_local_artifact(job, self.local_receipt)

    def test_local_artifact_rejects_mixed_release_mode_snapshot_and_build_drift(self):
        import copy

        job, _ = self.local_config()
        original = copy.deepcopy(self.local_receipt)
        for field, value in (
            ("tag", "v0.28.3"),
            ("zip_sha256", "b" * 64),
            ("gatekeeper_accepted", True),
        ):
            with self.subTest(field=field):
                self.local_receipt = dict(original, **{field: value})
                self.save_local_receipt(job["catalogue_menu"])
                with self.assertRaisesRegex(ControlRefused, "unmixed"):
                    catalogue.verify_config(job)
        self.local_receipt = original
        self.local_receipt["source_snapshot"] = dict(base="f" * 40, files={})
        self.save_local_receipt(job["catalogue_menu"])
        with self.assertRaisesRegex(ControlRefused, "source drift"):
            catalogue.verify_config(job)
        self.local_receipt["source_snapshot"] = self.snapshot.return_value
        self.local_receipt["build"]["returncode"] = 1
        self.save_local_receipt(job["catalogue_menu"])
        with self.assertRaisesRegex(ControlRefused, "build inputs"):
            catalogue.verify_config(job)
        self.local_receipt["build"]["returncode"] = 0
        self.save_local_receipt(job["catalogue_menu"])
        job["catalogue_menu"]["package"]["artifact_mode"] = "released"
        with self.assertRaisesRegex(ControlRefused, "Artifact mode refused"):
            catalogue.verify_config(job)

    def test_local_artifact_rejects_changed_missing_and_external_bundle_content(self):
        job, _ = self.local_config()
        package = job["catalogue_menu"]["package"]
        helper = Path(package["executable"])
        original = helper.read_bytes()
        helper.write_bytes(original + b"changed")
        with self.assertRaisesRegex(ControlRefused, "bundle/build"):
            catalogue.verify_config(job)
        helper.unlink()
        with self.assertRaisesRegex(ControlRefused, "bundle/build"):
            catalogue.verify_config(job)
        helper.write_bytes(original)
        (helper.parent / "outside").symlink_to(self.local_receipt_path)
        with self.assertRaisesRegex(ControlRefused, "External bundle link"):
            catalogue.verify_config(job)

    def test_released_artifact_checks_remain_default_and_never_fall_back(self):
        job = self.fixture.save_config(empty=True)
        self.assertIs(catalogue.verify_config(job), job["catalogue_menu"])
        path = Path(job["catalogue_menu"]["package"]["receipt_path"])
        receipt = catalogue.decode(path.read_bytes())
        receipt["gatekeeper_accepted"] = False
        path.write_bytes(catalogue.canonical(receipt))
        job["catalogue_menu"]["package"]["receipt_sha256"] = gui.sha(path.read_bytes())
        with patch.object(gui, "verify_local_artifact") as local:
            with self.assertRaisesRegex(ControlRefused, "released artifact"):
                catalogue.verify_config(job)
        local.assert_not_called()

    def test_local_bundle_internal_links_are_bound_and_adhoc_identity_is_real(self):
        job, _ = self.local_config()
        bundle = Path(job["catalogue_menu"]["package"]["bundle"])
        (bundle / "frontend-link").symlink_to("Contents/MacOS/SMRLauncher")
        digest = gui.local_bundle_digest(bundle)
        self.assertNotEqual(digest, self.local_receipt["bundle_sha256"])
        with patch.object(
            gui.subprocess,
            "run",
            return_value=SimpleNamespace(
                stdout="", stderr="Signature=adhoc\nTeamIdentifier=not set\n"
            ),
        ) as sign:
            gui.verify_adhoc_signature(bundle)
            self.assertEqual(sign.call_count, 3)
            sign.return_value.stderr = "Signature=Developer ID\nTeamIdentifier=ABCDEFGHIJ\n"
            with self.assertRaisesRegex(ControlRefused, "ad-hoc identity"):
                gui.verify_adhoc_signature(bundle)

    def test_local_gui_direct_selection_and_no_scroll_fallback(self):
        owner = self.owner
        owner.job["agent_save"] = dict(token="SyntheticToken12345")
        owner.config["package"] = dict(artifact_mode="local-development")
        frame = self.selected()
        self.assertEqual(
            owner.validate_gui_action(
                "gui_select",
                self.decision(["click", "--x", "400", "--y", "200"], "provenance_control"),
                frame,
            ),
            ["click", "--x", "400", "--y", "200"],
        )
        owner.validate_gui_observation("gui_select", frame, self.decision())
        frame["treeText"] = scroll_detail_tree("Synthetic Canyon")
        with self.assertRaisesRegex(ControlRefused, "without scrolling"):
            owner.validate_gui_action(
                "gui_select",
                self.decision(
                    [
                        "perform-secondary-action",
                        "--element-index",
                        "30",
                        "--action",
                        "scroll down",
                    ],
                    "provenance_control",
                ),
                frame,
            )

    def test_initial_and_final_reviews_never_substitute_for_each_other(self):
        owner = self.owner
        owner.gui_play_sent = True
        self.frame["treeText"] = review_tree(owner, initial=True).replace("10 button", "4 button")
        action = ["click", "--element-index", "4"]
        self.assertEqual(
            owner.validate_gui_action("gui_original", self.decision(action), self.frame), action
        )
        with self.assertRaisesRegex(ControlRefused, "Exact current review"):
            owner.validate_gui_action(
                "gui_confirm", self.decision(["click", "--element-index", "5"]), self.frame
            )
        self.frame["treeText"] = review_tree(owner, initial=False).replace("11 button", "5 button")
        action = ["click", "--element-index", "5"]
        self.assertEqual(
            owner.validate_gui_action("gui_confirm", self.decision(action), self.frame), action
        )
        with self.assertRaisesRegex(ControlRefused, "One final confirmation"):
            owner.validate_gui_action("gui_confirm", self.decision(action), self.frame)

    def test_exact_identity_required_despite_five_name_substrings(self):
        frame = self.selected()
        frame["treeText"] += "\n" + "\n".join(
            "Synthetic Canyon edition " + str(i) for i in range(4)
        )
        self.owner.validate_gui_observation("gui_select", frame, self.decision())
        self.assertTrue(self.owner.gui_selection_bound)
        frame["treeText"] = frame["treeText"].replace("a" * 64, "f" * 64)
        with self.assertRaisesRegex(ControlRefused, "Exact selected UI identity"):
            self.owner.validate_gui_observation("gui_select", frame, self.decision())

    def test_full_sheet_body_allows_whitespace_and_nested_warning_not_substitution(self):
        owner = self.owner
        owner.gui["initial_warning"] = owner.config["warning"] + "\nAdditional preparation warning."
        for initial in (True, False):
            for flatten in (True, False):
                frame = dict(treeText=review_tree(owner, initial=initial, flatten=flatten))
                gui.exact_review(owner, frame, initial=initial)
                with self.assertRaisesRegex(ControlRefused, "Exact current review"):
                    gui.exact_review(owner, frame, initial=not initial)
        original = review_tree(owner, initial=True)
        for changed in (
            original.replace("Additional", "Different"),
            original.replace("warning.\n", "warning. OMITTED\n"),
            original.replace("b" * 64, "d" * 64),
            original + "\n\t\t95 text " + " ".join(gui.review_body(owner, initial=True).split()),
        ):
            with self.assertRaisesRegex(ControlRefused, "Exact current review"):
                gui.exact_review(owner, dict(treeText=changed), initial=True)
        with self.assertRaisesRegex(ControlRefused, "One GUI review sheet"):
            gui.exact_review(owner, dict(treeText=original + "\n\t99 sheet alert"), initial=True)

    def test_review_windows_only_select_bound_main_for_exact_owned_modal_pair(self):
        owner = self.owner
        owner.gui_play_sent = owner.gui_selection_bound = True
        owner.request = time.monotonic()
        owner.launch_deadline = owner.request + 390
        owner.last_agent_window = 33
        owner.last_agent_window_process = owner.frontend
        main = dict(
            id=33, title="Map Library", app=dict(pid=21), isOffscreen=False, isMinimized=False
        )
        sheet = dict(main, id=34, title="")
        rows = [main, sheet]
        self.assertEqual(owner.gui_review_windows("MachineOriginalReview", rows), [main])
        self.assertEqual(rows, [main, sheet])
        self.assertIs(owner.gui_review_windows("Agent008", rows), rows)
        for changed in (
            [main, dict(sheet, app=dict(pid=999))],
            [main, dict(sheet, id=33)],
            [dict(main, id=35), sheet],
            [main, dict(sheet, title="Unrelated window")],
            [main, dict(sheet, isOffscreen=True)],
            [main, sheet, dict(sheet, id=35)],
        ):
            with self.assertRaises(ControlRefused):
                owner.gui_review_windows("MachineFinalReview", changed)
        owner.last_agent_window_process = Process(21, 999, "/synthetic/frontend")
        with self.assertRaises(ControlRefused):
            owner.gui_review_windows("MachineOriginalReview", rows)

    def test_no_preparation_or_unreviewed_confirmation(self):
        self.owner.gui_play_sent = True
        self.frame["treeText"] = (
            review_tree(self.owner, initial=True).replace("10 button", "4 button")
            + "\n\t\t5 button Prepare & Play Compatibility Edition"
        )
        with self.assertRaisesRegex(ControlRefused, "Preparation refused"):
            self.owner.validate_gui_action(
                "gui_original", self.decision(["click", "--element-index", "5"]), self.frame
            )
        with self.assertRaisesRegex(ControlRefused, "One final confirmation"):
            self.owner.validate_gui_action(
                "gui_confirm", self.decision(["click", "--element-index", "4"]), self.frame
            )

    def test_stale_success_and_unfocused_search_refuse(self):
        frame = self.selected()
        frame["treeText"] += gui.SUCCESS
        with self.assertRaisesRegex(ControlRefused, "Stale success"):
            self.owner.validate_gui_observation("gui_select", frame, self.decision())
        with self.assertRaisesRegex(ControlRefused, "Current search focus"):
            self.owner.validate_gui_action(
                "gui_select",
                self.decision(["type-text", "--text", "Synthetic Canyon"], "search_field"),
                frame,
            )
        with self.assertRaisesRegex(ControlRefused, "Fresh launch success"):
            self.owner.validate_gui_observation("gui_success", frame, self.decision())

    def test_fresh_focused_search_uses_current_provider_grammar(self):
        self.frame["treeText"] = (
            "The focused UI element is 84 search text field (settable) Search maps."
        )
        self.assertTrue(gui.search_focused(self.frame))
        action = ["type-text", "--text", "Synthetic Canyon"]
        self.assertEqual(
            self.owner.validate_gui_action(
                "gui_select", self.decision(action, "search_field"), self.frame
            ),
            action,
        )
        self.frame["treeText"] = (
            "The focused UI element is 84 search text field (settable) Search maps, Value: Synthetic Canyon."
        )
        action = ["press-key", "--key", "Return"]
        self.assertEqual(
            self.owner.validate_gui_action(
                "gui_select", self.decision(action, "search_field"), self.frame
            ),
            action,
        )
        self.frame["treeText"] = (
            "84 search text field (settable) Search maps\nThe focused UI element is 3 list Sidebar."
        )
        self.assertFalse(gui.search_focused(self.frame))

    def test_current_advertised_provenance_scroll_accepts_fresh_index(self):
        self.frame["treeText"] = scroll_detail_tree("Synthetic Canyon").replace(
            "30 scroll area", "45 scroll area"
        )
        action = ["perform-secondary-action", "--element-index", "45", "--action", "scroll down"]
        self.assertEqual(
            self.owner.validate_gui_action(
                "gui_select", self.decision(action, "provenance_control"), self.frame
            ),
            action,
        )
        self.assertEqual(self.owner.gui_scroll_dispatches, 0)
        with self.assertRaisesRegex(ControlRefused, "Exact selected UI identity"):
            self.owner.validate_gui_observation("gui_select", self.frame, self.decision())

    def test_semantic_scroll_refuses_wrong_scope_direction_index_body_and_cap(self):
        self.frame["treeText"] = scroll_detail_tree("Synthetic Canyon")
        action = ["perform-secondary-action", "--element-index", "30", "--action", "scroll down"]
        cases = [
            ("gui_play", "provenance_control", action, {}),
            ("gui_select", "owned_window", action, {}),
            ("gui_select", "provenance_control", action[:-1] + ["scroll up"], {}),
            ("gui_select", "provenance_control", action[:2] + ["99"] + action[3:], {}),
            ("gui_select", "provenance_control", action[:2] + ["20"] + action[3:], {}),
            ("gui_select", "provenance_control", action, {"gui_phase": False}),
            ("gui_select", "provenance_control", action, {"gui_play_sent": True}),
            ("gui_select", "provenance_control", action, {"request": time.monotonic()}),
            ("gui_select", "provenance_control", action, {"gui_scroll_dispatches": 2}),
        ]
        for stage, receiver, candidate, changes in cases:
            with self.subTest(stage=stage, receiver=receiver, action=candidate, changes=changes):
                with patch.dict(vars(self.owner), changes):
                    with self.assertRaises(ControlRefused):
                        self.owner.validate_gui_action(
                            stage, self.decision(candidate, receiver), self.frame
                        )
        for tree in (
            scroll_detail_tree("Different target"),
            self.frame["treeText"].replace("Issues & Evidence", "Overview"),
            self.frame["treeText"].replace("scroll up, scroll down", "scroll up"),
        ):
            with self.subTest(tree=tree):
                with self.assertRaises(ControlRefused):
                    self.owner.validate_gui_action(
                        "gui_select",
                        self.decision(action, "provenance_control"),
                        dict(self.frame, treeText=tree),
                    )

    def test_none_service_and_gui_death_do_not_settle_unknown_dispatch(self):
        owner = self.owner
        self.alive.clear()
        self.assertIsNone(owner.service)
        with self.assertRaisesRegex(ControlRefused, "observer retained"):
            owner.stop()
        self.assertFalse(owner.dispatch_settled)
        with self.assertRaisesRegex(ControlRefused, "Unsettled packaged dispatch"):
            catalogue.restore_catalogue(owner)

    def test_restoration_checks_both_gui_owners_before_any_switch(self):
        owner = self.owner
        owner.dispatch_settled = owner.gui_bound = owner.gui_closed = owner.gui_quit_sent = True
        owner.launcher_rows = lambda: []
        owner.require_gui_identity = lambda **kw: None
        owner.app.library.mkdir(exist_ok=True)
        self.alive.pop(21)
        with self.assertRaisesRegex(ControlRefused, "Frontend/backend absence"):
            catalogue.restore_catalogue(owner)
        self.assertEqual(owner.profiles._state()["active"], self.fixture.target.profile_id)
        self.alive.clear()
        self.assertTrue(catalogue.restore_catalogue(owner))
        self.assertEqual(owner.profiles._state()["active"], owner.job["return_profile"])
        target = owner.profiles._profile_path(self.fixture.target.profile_id)
        self.assertEqual((target / "Saves/existing.sav").read_bytes(), b"existing target save")
        self.assertEqual((target / "Saves/new.sav").read_bytes(), b"new save remains")
        self.assertEqual((owner.profiles.live / "Saves/personal.sav").read_bytes(), b"return save")

    def test_gui_observer_interval_cannot_be_overridden(self):
        with self.assertRaises(TypeError):
            catalogue.bind_observer(self.owner, {}, deadline=self.owner.anchor + 390)

    def test_real_entry_accepts_menu_metadata_and_requires_gui_source_closure(self):
        job, admission = self.fixture.entry_contract(gui.PURPOSE)
        config = job["catalogue_menu"]
        config.pop("selection")
        config.pop("preparation")
        config["prepare_compatibility"] = False
        config["warning"] = "Final synthetic warning"
        front = Path(config["package"]["bundle"]) / "Contents/MacOS/SMRLauncher"
        front.write_bytes(b"synthetic frontend")
        config["native_gui"] = dict(
            display_name="Synthetic Canyon",
            initial_warning="Initial synthetic warning",
            frontend_sha256=gui.sha(front.read_bytes()),
            launcher_files={},
        )
        job["agent_save"] = dict(
            token="SyntheticToken12345", filename="SyntheticToken12345.SMRailroadsSavedGame"
        )
        simulated_229(self, job)
        driver.verify_entry(job, admission, "a" * 64)
        job["closure"] = [
            row for row in job["closure"] if row["path"] != str(Path(gui.__file__).resolve())
        ]
        with self.assertRaisesRegex(ControlRefused, "Actual imported source paths"):
            driver.verify_entry(job, admission, "a" * 64)


# The composed check deliberately uses the retained common implementation. It is
# local-only because that reviewed owner is private evidence, not a redistributable
# fixture. The ordinary controls above remain available in CI without that tree.
import base64
from dataclasses import asdict
import importlib.util
import os
import struct
import zlib
import uuid
from contextlib import ExitStack
import test_active_job_control as control_fixtures
from runner_journal import RunJournal


@unittest.skipUnless(os.environ.get("SMR_GUI_COMMON_ROOT"), "Retained private common required")
class ComposedGUIOwner(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.catalogue_fixture = fixtures.CatalogueRestoreTests()
        self.catalogue_fixture.setUp()
        self.addCleanup(self.catalogue_fixture.doCleanups)
        f = self.catalogue_fixture
        self.assertTrue(catalogue.restore_catalogue(f.owner))
        c = control_fixtures.Fixture()
        c.setUp()
        self.addCleanup(c.doCleanups)
        self.addCleanup(self.stack.close)
        self.control_fixture = c
        root = Path(os.environ["SMR_GUI_COMMON_ROOT"]).resolve()
        self.stack.enter_context(
            patch.object(sys, "path", [str(root), str(root / "stop-helper"), *sys.path])
        )
        name = "native_gui_composed_common"
        spec = importlib.util.spec_from_file_location(name, root / "agent_calibration.py")
        common = importlib.util.module_from_spec(spec)
        sys.modules[name] = common
        spec.loader.exec_module(common)
        self.common = common
        import frozen_mac_processes

        self.process_module = frozen_mac_processes
        cls = gui.pilot_class(common, catalogue.pilot_class(common, driver.pilot_class(common)))
        owner = cls.__new__(cls)
        self.owner = owner
        owner.__dict__.update({k: v for k, v in vars(f.owner).items() if not callable(v)})
        job = dict(c.job)
        job.update(f.owner.job)
        job.update(
            purpose=gui.PURPOSE,
            common_root=str(root),
            orca="/synthetic/orca",
            game_sha256=f.fixture.installation.executable_sha256,
        )
        owner.job = job
        owner.root = root
        owner.config = dict(f.owner.config)
        owner.config["target"] = dict(
            profile_id=f.target.profile_id,
            archive_sha256=f.target.archive_sha256,
            variant_id=f.target.variant_id,
            scenarios=list(f.target.scenarios),
            assets_sha256=owner.profiles._asset_hash(
                owner.profiles._profile_path(f.target.profile_id)
            ),
        )
        owner.config["warning"] = "Final original synthetic warning."
        owner.config["prepare_compatibility"] = False
        bundle = owner.directory / "Synthetic.app"
        front = bundle / "Contents/MacOS/SMRLauncher"
        helper = front.with_name("Definitive SMR Launcher Apple Silicon")
        front.parent.mkdir(parents=True)
        front.write_bytes(b"Synthetic frontend; never executable")
        helper.write_bytes(b"Synthetic helper; never executable")
        artifact = owner.directory / "artifact.json"
        artifact.write_bytes(
            catalogue.canonical(
                dict(
                    source_commit="synthetic",
                    tag="synthetic",
                    zip_sha256="a" * 64,
                    codesign_verified=True,
                    gatekeeper_accepted=True,
                )
            )
        )
        owner.config["package"] = dict(
            bundle=str(bundle),
            executable=str(helper),
            executable_sha256=gui.sha(helper.read_bytes()),
            receipt_path=str(artifact),
            receipt_sha256=gui.sha(artifact.read_bytes()),
            source_commit="synthetic",
            tag="synthetic",
            zip_sha256="a" * 64,
        )
        owner.gui = owner.config["native_gui"] = dict(
            display_name=f.target.name,
            initial_warning="Initial cached synthetic warning.",
            frontend_sha256=gui.sha(front.read_bytes()),
            launcher_files={
                str(owner.app.library / "update-preferences.json"): None,
                str(owner.app.library / "updates/pending.json"): None,
            },
        )
        job["catalogue_menu"] = owner.config
        job["source_original"] = dict(
            archive_sha256=f.target.archive_sha256,
            variant_id=f.target.variant_id,
            scenario=f.target.scenarios[0],
        )
        job["agent_save"] = dict(
            token="SyntheticToken12345", filename="SyntheticToken12345.SMRailroadsSavedGame"
        )
        actor = dict(
            session="synthetic-actor", terminal="term_synthetic_actor", cwd=str(Path.cwd())
        )
        job["actor_binding"].update(driver_actor=actor, provider_runtime_id="synthetic-runtime")
        job["root"]["run"] = "run_9c111880b18c"
        job["agent_owner_state"] = dict(
            c.state["effective_continuation"],
            **job["root"],
            exclusive_runtime_owner=actor["terminal"],
        )
        c.state["effective_continuation"] = job["agent_owner_state"]
        c.state["active_job_control"]["binding"] = driver.AgentJobControl(job).binding
        c.save(c.state)
        self.stack.enter_context(
            patch.dict(
                os.environ, CODEX_THREAD_ID=actor["session"], ORCA_TERMINAL_HANDLE=actor["terminal"]
            )
        )
        authority = owner.directory / "authority.txt"
        authority.write_bytes(b"Synthetic one-use source-test authority; no runtime admission")
        job.update(
            authority_path=str(authority),
            authority_sha256=gui.sha(authority.read_bytes()),
            queue_module=str(owner.directory / "queue.synthetic.py"),
            native_source=str(root / "frozen-code/scripts/research/native.synthetic"),
        )
        Path(job["queue_module"]).write_bytes(b"Synthetic queue boundary; no live queue")
        # Native input source is synthetic and sealed; no executable provider is used.
        native = owner.directory / "native.synthetic"
        native.write_bytes(b"Synthetic native provider boundary")
        job["native_source"] = str(native)
        self.stack.enter_context(
            patch.object(driver, "NATIVE_SOURCE_SHA256", gui.sha(native.read_bytes()))
        )
        manifest = root / "source-manifest.json"
        job["source_manifest_sha256"] = gui.sha(manifest.read_bytes())
        paths = set((ROOT / "src").rglob("*.py")) | set((ROOT / "scripts/research").rglob("*.py"))
        paths.update(Path(row["path"]) for row in catalogue.decode(manifest.read_bytes())["files"])
        paths.update(
            (
                root / "agent_calibration.py",
                root / "stop-helper/agent_host.py",
                native,
                Path(job["queue_module"]),
            )
        )
        job["closure"] = [
            dict(path=str(p), bytes=p.stat().st_size, sha256=gui.sha(p.read_bytes()))
            for p in sorted(paths)
        ]
        decision, review_decision = driver.admission_decisions(gui.PURPOSE)
        review = owner.directory / "review.json"
        review.write_bytes(
            catalogue.canonical(
                dict(
                    decision=review_decision,
                    job_sha256="a" * 64,
                    closure=job["closure"],
                    findings=[],
                )
            )
        )
        now = time.time()
        job["provider_processes"] = []
        simulated_229(self, job)
        job_sha = gui.sha(catalogue.canonical(job))
        review_data = catalogue.decode(review.read_bytes())
        review_data["job_sha256"] = job_sha
        review.write_bytes(catalogue.canonical(review_data))
        owner.admission = dict(
            decision=decision,
            job_sha256=job_sha,
            runtime_owner=actor["terminal"],
            root=job["root"],
            issued_epoch=now - 1,
            expires_epoch=now + 899,
            review=dict(path=str(review), sha256=gui.sha(review.read_bytes())),
        )
        driver.verify_entry(job, owner.admission, job_sha)
        owner.control = driver.AgentJobControl(job)
        owner.lease_guard = lambda: None  # Queue authority is a fixture, never a live queue.
        owner.directory = owner.operation_directory = owner.directory / "composed"
        owner.directory.mkdir()
        owner.anchor = time.monotonic()
        owner.launch_deadline = owner.anchor + 120
        owner.game = owner.observer = owner.frontend = owner.backend = owner.open_child = None
        owner.gui_phase = True
        owner.gui_bound = owner.gui_closed = owner.gui_quit_sent = owner.gui_selection_bound = False
        owner.gui_play_sent = owner.gui_original_sent = owner.gui_confirm_sent = (
            owner.gui_launch_started
        ) = False
        owner.gui_frames = None
        owner.gui_scroll_dispatches = 0
        owner.gui_scroll_permit = None
        owner.dispatch_settled = owner.open_attempted = owner.uncertain_input = False
        owner.helper_packet = owner.directory / "observer"
        owner.helper_packet.mkdir(mode=0o700)
        owner.creation = []
        owner.phases = {}
        owner.frames = owner.launches = owner.seq = 0
        owner.next_sample = owner.anchor
        owner.delivery_validation = owner.completed_input = None
        owner.transport_zoom_attempts = 0
        owner.journal = RunJournal(owner.directory)
        owner.journal.append(
            "started",
            dict(
                run_id=job["job"],
                input_identity=job["source_original"]["archive_sha256"],
                engine_input_identity=job["game_sha256"],
                binding=job["root"],
                name="Synthetic composed GUI",
                scenario=job["source_original"]["scenario"],
                run_directory=str(owner.directory),
            ),
        )
        owner.journal.append("terminal_pending", dict(one_use=True, automatic_successor=False))
        owner.decisions = driver.AgentDecisions(owner)
        self.processes = {}
        self.serial = 1000
        self.state = "selected"
        self.stage_counts = {}
        self.observer_child = None
        self.native_lock_fd = None
        self.addCleanup(self.release_lock)
        self.fail_mode = None
        self.actions = []
        self.metadata_offscreen = False
        self.selection_scroll_goal = 1
        self.semantic_scrolls = 0
        self.cancel_on_input = False
        self.observer_api = None
        self.first_bound_record = None
        self.actor_delays = {}
        self.final_ingress_delay = 0
        self.gui_play_decided_at = None
        self.delayed_requests = set()
        self.provider_delay = 0
        self.observer_stop_requests = []
        installation = owner.app.installation
        installation.executable = f.fixture.root / "SyntheticGame"
        test = self

        class Monitor:
            def __init__(self, executable):
                self.executable = str(executable)

            def inspect(self, pid):
                return test.processes.get(pid)

            def games(self):
                return [
                    p
                    for p in test.processes.values()
                    if p.executable == str(installation.executable)
                ]

            def stop(self, process):
                test.observer_stop_requests.append(process)
                test.processes.pop(process.pid, None)

        class ObserverMonitor(Monitor):
            def inspect(self, pid):
                current = super().inspect(pid)
                if test.fail_mode == "observer-pid-mismatch" and pid == 73 and current is not None:
                    return test.process_module.Process(74, current.birth_us, current.executable, 0)
                return current

        self.observer_monitor_factory = ObserverMonitor
        owner.monitor = Monitor(installation.executable)
        owner.controller_identity = dict(pid=os.getpid(), birth_us=1, executable=sys.executable)
        self.stack.enter_context(patch.object(frozen_mac_processes, "MacProcesses", Monitor))
        self.stack.enter_context(
            patch.object(driver.subprocess, "check_output", side_effect=self.read_boundary)
        )
        self.stack.enter_context(patch.object(driver.subprocess, "Popen", side_effect=self.spawn))
        self.clock = [owner.anchor + 0.01]
        self.stack.enter_context(patch.object(time, "monotonic", side_effect=lambda: self.clock[0]))
        self.stack.enter_context(patch.object(time, "sleep", side_effect=self.advance))
        self.addCleanup(lambda: getattr(owner, "helper_log", None) and owner.helper_log.close())

    def release_lock(self):
        if self.native_lock_fd is not None:
            os.close(self.native_lock_fd)
            self.native_lock_fd = None

    def write(self, name, value):
        path = self.owner.helper_packet / name
        path.write_bytes(catalogue.canonical(value))

    def read_boundary(self, argv, **kwargs):
        owner = self.owner
        if argv[:2] == ["/bin/ps", "-axo"]:
            rows = []
            for p in self.processes.values():
                parent = (
                    owner.frontend.pid
                    if owner.frontend is not None
                    and p.executable == owner.config["package"]["executable"]
                    else 1
                )
                rows.append(f"{p.pid} {parent} {p.executable}")
            return "\n".join(rows)
        if argv[1:3] == ["orchestration", "run-show"]:
            if (
                self.final_ingress_delay
                and owner.delivery_validation is not None
                and owner.observer is not None
                and owner.gui_play_sent
                and not hasattr(owner, "request")
            ):
                self.clock[0] += self.final_ingress_delay
                self.final_ingress_delay = 0
                self.step_observer()
            if self.cancel_on_input and owner.delivery_validation is not None:
                self.cancel_on_input = False
                self.control_fixture.append(control_fixtures.user("I'm playing", seconds=1))
            return catalogue.canonical(
                dict(
                    ok=True,
                    result=dict(
                        run=dict(
                            consumer_generation=owner.job["root"]["consumer_generation"],
                            coordinator_handle=owner.job["root"]["coordinator"],
                        )
                    ),
                    _meta=dict(runtimeId="synthetic-runtime"),
                )
            )
        if argv[1:] == ["status", "--json"]:
            return catalogue.canonical(dict(ok=True, _meta=dict(runtimeId="synthetic-runtime")))
        raise AssertionError("Unexpected read boundary: " + repr(argv))

    def png(self):
        def chunk(kind, payload):
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        data = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1000, 800, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x00" * 3000) * 800))
            + chunk(b"IEND", b"")
        )
        return base64.b64encode(data).decode()

    def tree(self):
        owner = self.owner
        target = owner.config["target"]
        selected = (
            owner.gui["display_name"]
            + "\nArchive SHA-256: "
            + target["archive_sha256"]
            + "\nVariant: "
            + target["variant_id"]
            + "\nExecutable SHA-256: "
            + owner.job["game_sha256"]
        )
        if self.state == "selected" and self.metadata_offscreen:
            return scroll_detail_tree(owner.gui["display_name"], self.semantic_scrolls)
        return {
            "selected": selected + "\n9 button",
            "initial": review_tree(owner, initial=True),
            "final": review_tree(owner, initial=False),
            "success": selected
            + ("\n" + gui.SUCCESS if self.fail_mode != "unknown-success" else "\nOutcome unknown"),
            "game-launcher": "18 button Play",
            "game-menu": "0 standard window Main Menu",
        }[self.state]

    def spawn(self, argv, **kwargs):
        owner = self.owner
        self.serial += 1
        test = self

        class Child:
            def __init__(self):
                self.pid = test.serial
                self.returncode = 0

            def poll(self):
                if self is test.observer_child:
                    test.step_observer()
                    if test.observer_api.terminal:
                        self.returncode = (
                            0
                            if test.observer_api.state.phase == "observed_exited"
                            and test.observer_api.error is None
                            else 2
                        )
                        test.processes.pop(self.pid, None)
                return self.returncode

            def wait(self, timeout=5, **kw):
                limit = time.monotonic() + timeout
                while self.poll() is None:
                    if time.monotonic() >= limit:
                        raise driver.subprocess.TimeoutExpired("synthetic owned observer", timeout)
                    test.advance(0.05)
                return self.returncode

            def terminate(self):
                self.returncode = -15
                test.processes.pop(self.pid, None)

        child = Child()
        if argv[0] == "/usr/bin/open":
            self.assertNotIn("-n", argv)
            self.assertEqual(argv[-2:], ["--play-deadline", str(owner.anchor + 390)])
            birth = time.time_ns() // 1000
            self.processes[71] = self.process_module.Process(71, birth, owner.frontend_path, 0)
            self.processes[72] = self.process_module.Process(
                72, birth + 1, owner.config["package"]["executable"], 0
            )
            path = owner.app.library / ".native-instance.lock"
            path.write_bytes(catalogue.canonical(dict(pid=71, executable=owner.frontend_path)))
            self.native_lock_fd = os.open(path, os.O_RDONLY)
            import fcntl

            fcntl.flock(self.native_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return child
        if "agent_host.py" in str(argv[2]):
            self.observer_child = child
            child.returncode = None
            process = self.process_module.Process(
                child.pid, time.time_ns() // 1000, sys.executable, 0
            )
            self.processes[child.pid] = process
            self.write(
                "bootstrap.json",
                dict(
                    process=asdict(process),
                    interpreter=sys.executable,
                    prefix=sys.prefix,
                    monotonic=time.monotonic(),
                    clock=vars(time.get_clock_info("monotonic")),
                ),
            )
            import wrapper

            raw = catalogue.decode((owner.helper_packet / "admission.json").read_bytes())
            self.observer_api = wrapper.Wrapper(
                wrapper.RootAdmission(**raw["admission"]),
                owner.helper_packet,
                raw["approval"],
                raw["source_acceptance"],
                self.observer_monitor_factory,
                time.monotonic,
                self.advance,
            )
            self.observer_api.run_once()
            self.observer_ready_at = catalogue.decode(
                (owner.helper_packet / "ready.json").read_bytes()
            )["observed"]
            return child
        self.assertEqual(argv[:2], ["/synthetic/orca", "computer"])
        action = argv[2 : argv.index("--app")]
        pid = int(argv[argv.index("--app") + 1][4:])
        window = dict(
            id=33,
            x=0,
            y=0,
            width=1000,
            height=800,
            title="Sid Meier's Railroads! Options"
            if self.state == "game-launcher"
            else ("Map Library" if owner.gui_phase else "Main Menu"),
        )
        if action == ["list-windows"]:
            result = dict(
                app=dict(pid=pid),
                windows=[dict(window, app=dict(pid=pid), isOffscreen=False, isMinimized=False)],
            )
            if self.state in ("initial", "final"):
                result["windows"].append(
                    dict(
                        window,
                        id=34,
                        title="",
                        app=dict(pid=pid),
                        isOffscreen=False,
                        isMinimized=False,
                    )
                )
        else:
            if action == ["get-app-state"] and self.state == "initial" and self.provider_delay:
                self.clock[0] += self.provider_delay
                self.provider_delay = 0
                self.step_observer()
            result = dict(
                snapshot=dict(
                    id=str(uuid.uuid4()), app=dict(pid=pid), window=window, treeText=self.tree()
                ),
                screenshot=dict(data=self.png(), scale=1),
            )
            if action != ["get-app-state"]:
                self.actions.append((self.state, action))
                if action[0] == "perform-secondary-action":
                    self.assertEqual(self.state, "selected")
                    self.semantic_scrolls += 1
                    if (
                        self.selection_scroll_goal
                        and self.semantic_scrolls >= self.selection_scroll_goal
                    ):
                        self.metadata_offscreen = False
                elif self.state == "selected":
                    self.play_input_at = time.monotonic()
                    self.assertEqual(self.observer_api.state.phase, "awaiting_binding")
                    self.state = "initial"
                elif self.state == "initial":
                    self.state = "final"
                elif self.state == "final":
                    owner.app.activate(owner.config["target"]["profile_id"])
                    (owner.profiles.live / "Logs").mkdir(exist_ok=True)
                    self.processes[73] = self.process_module.Process(
                        73, time.time_ns() // 1000, str(owner.app.installation.executable), 0
                    )
                    self.state = "success"
                    if self.fail_mode == "expiry-inflight":
                        self.clock[0] = owner.anchor + 391
                    if self.fail_mode == "foreign-active":
                        owner.app.activate("original-game")
                    if self.fail_mode == "pid-mismatch":
                        self.processes[73] = self.process_module.Process(
                            73, 1, str(owner.app.installation.executable), 0
                        )
                elif self.state == "success":
                    self.assertEqual(action, ["hotkey", "--key", "CmdOrCtrl+Q"])
                    self.assertIsNone(owner.observer.poll())
                    self.processes.pop(71)
                    if self.fail_mode != "service-live":
                        self.processes.pop(72)
                    else:
                        self.clock[0] = owner.anchor + 480
                    self.release_lock()
                    self.state = "game-launcher"
                elif self.state == "game-launcher":
                    self.state = "game-menu"
                elif action == ["hotkey", "--key", "CmdOrCtrl+Q"]:
                    self.processes.pop(owner.game.pid)
                result.pop("screenshot")
        kwargs["stdout"].write(
            catalogue.canonical(
                dict(ok=True, result=result, _meta=dict(runtimeId="synthetic-runtime"))
            )
        )
        return child

    def step_observer(self):
        if self.observer_api is not None:
            state = self.observer_api.run_once()
            if state.bound and self.first_bound_record is None:
                self.first_bound_record = catalogue.decode(
                    (self.owner.helper_packet / "bound.json").read_bytes()
                )

    def advance(self, seconds):
        self.clock[0] += min(seconds, 0.05)
        self.step_observer()
        owner = self.owner
        for path in owner.directory.glob("Agent*-request.json"):
            reply = path.with_name(path.name.replace("-request", "-decision"))
            if reply.exists():
                continue
            request = catalogue.decode(path.read_bytes())
            stage = request["stage"]
            if path not in self.delayed_requests:
                self.delayed_requests.add(path)
                self.clock[0] += self.actor_delays.get(stage, 0)
                self.step_observer()
            count = self.stage_counts.get(stage, 0)
            self.stage_counts[stage] = count + 1
            actions = {
                "gui_play": ["click", "--element-index", "9"],
                "gui_quit": ["hotkey", "--key", "CmdOrCtrl+Q"],
                "menu": ["click", "--x", "10", "--y", "10"],
                "returned": ["press-key", "--key", "Escape"],
                "finished": ["hotkey", "--key", "CmdOrCtrl+Q"],
            }
            action = actions.get(stage, []) if count == 0 else []
            choice = "action" if action else "observed"
            receiver = "owned_window"
            if stage == "gui_select" and self.metadata_offscreen:
                if self.selection_scroll_goal == 0:
                    choice, action = "stop", []
                else:
                    choice = "action"
                    action = [
                        "perform-secondary-action",
                        "--element-index",
                        "30",
                        "--action",
                        "scroll down",
                    ]
                    receiver = "provenance_control"
            if stage == "gui_play":
                self.assertIsNone(owner.observer)
                self.assertFalse(hasattr(owner, "gui_arm_started"))
                self.gui_play_decided_at = time.monotonic()
            reply.write_bytes(
                catalogue.canonical(
                    dict(
                        request_sha256=gui.sha(path.read_bytes()),
                        evidence_kind="agent_visual_decision",
                        choice=choice,
                        action=action,
                        receiver=receiver,
                        observation="Synthetic fresh viewport observation; inspect movement before any second scroll.",
                        exact_token="",
                        overwrite_visible=False,
                    )
                )
            )

    def test_actual_decisions_entry_observer_close_game_quit_and_restoration(self):
        owner = self.owner
        owner.launch()
        self.assertTrue(owner.dispatch_settled)
        self.assertIsNone(owner.service)
        self.assertTrue(owner.gui_closed)
        outcome = owner.observe()
        self.assertFalse(outcome["gameplay_credit"])
        self.assertFalse(outcome["save_reopen_credit"])
        owner.persist_outcome(outcome)
        self.assertTrue(owner.stop())
        self.assertTrue(owner.restore())
        self.assertEqual(self.observer_stop_requests, [])
        self.assertEqual(owner.profiles._state()["active"], owner.job["return_profile"])
        target = owner.profiles._profile_path(self.catalogue_fixture.target.profile_id)
        self.assertEqual((target / "Saves/existing.sav").read_bytes(), b"existing target save")
        self.assertEqual((target / "Saves/new.sav").read_bytes(), b"new save remains")
        self.assertEqual((owner.profiles.live / "Saves/personal.sav").read_bytes(), b"return save")

    def test_two_native_review_windows_keep_raw_receipts_and_full_nested_bodies(self):
        self.owner.gui["initial_warning"] = (
            self.owner.config["warning"] + "\nAdditional original-edition preparation warning."
        )
        self.test_actual_decisions_entry_observer_close_game_quit_and_restoration()
        for label in ("MachineOriginalReview", "MachineFinalReview"):
            raw = catalogue.decode(
                (self.owner.directory / (label + "Windows01.stdout")).read_bytes()
            )
            self.assertEqual([w["id"] for w in raw["result"]["windows"]], [33, 34])
            self.assertEqual(raw["result"]["windows"][1]["title"], "")
            frame = catalogue.decode((self.owner.directory / (label + "-frame.json")).read_bytes())
            self.assertEqual(frame["window"]["id"], 33)
        self.assertLess(self.first_bound_record["observed"] - self.owner.request, 20)

    def start_offscreen_selection_hold(self):
        self.metadata_offscreen = True
        self.selection_scroll_goal = 0
        with self.assertRaisesRegex(ValueError, "Agent stopped uncertain scene"):
            self.owner.launch()
        self.assertFalse(hasattr(self.owner, "request"))
        self.assertIsNone(self.owner.observer)

    def current_scroll_plan(self, label):
        owner = self.owner
        _, frame, window = owner.agent_frame(label, owner.anchor + 390)
        action = ["perform-secondary-action", "--element-index", "30", "--action", "scroll down"]
        action = owner.validate_gui_action(
            "gui_select", dict(action=action, receiver="provenance_control"), frame
        )
        return action, frame, window, owner.frame_started

    def test_offscreen_metadata_semantic_scroll_then_actual_bound_menu_restore(self):
        self.metadata_offscreen = True
        self.test_actual_decisions_entry_observer_close_game_quit_and_restoration()
        self.assertEqual(self.semantic_scrolls, 1)
        self.assertEqual(self.owner.gui_scroll_dispatches, 1)
        self.assertEqual(self.owner.transport_zoom_attempts, 0)
        self.assertEqual(self.stage_counts["gui_select"], 2)

    def test_final_scroll_dispatch_cap_has_no_third_popen_or_uncertainty(self):
        self.start_offscreen_selection_hold()
        owner = self.owner
        for label in ("ScrollOne", "ScrollTwo"):
            action, frame, window, captured = self.current_scroll_plan(label)
            permit = owner.gui_scroll_permit
            owner.agent_input(label, action, window, frame, captured, owner.anchor + 390, None)
        self.assertEqual(owner.gui_scroll_dispatches, 2)
        self.assertEqual(self.semantic_scrolls, 2)
        # Re-deliver the already validated second plan: the final cap must
        # refuse independently of the planning validator, before mark/Popen.
        owner.gui_scroll_permit = permit
        before = self.serial
        with self.assertRaisesRegex(ControlRefused, "Two provenance scroll"):
            owner.agent_input(
                "ScrollThird", action, window, frame, captured, owner.anchor + 390, None
            )
        self.assertEqual(self.serial, before)
        self.assertEqual(self.semantic_scrolls, 2)
        self.assertEqual(owner.gui_scroll_dispatches, 2)
        self.assertEqual(owner.transport_zoom_attempts, 0)
        self.assertFalse(owner.uncertain_input)

    def test_final_scroll_cancellation_does_not_count_or_spawn(self):
        self.start_offscreen_selection_hold()
        owner = self.owner
        action, frame, window, captured = self.current_scroll_plan("ScrollCancel")
        self.cancel_on_input = True
        before = self.serial
        with self.assertRaises(driver.UserCancelled):
            owner.agent_input(
                "ScrollCancel", action, window, frame, captured, owner.anchor + 390, None
            )
        self.assertTrue(owner.control.cancelled)
        self.assertEqual(self.serial, before)
        self.assertEqual(owner.gui_scroll_dispatches, 0)
        self.assertEqual(self.semantic_scrolls, 0)
        self.assertFalse(owner.uncertain_input)

    def test_final_scroll_stale_frame_does_not_count_or_spawn(self):
        self.start_offscreen_selection_hold()
        owner = self.owner
        action, frame, window, captured = self.current_scroll_plan("ScrollStale")
        self.clock[0] += driver.FRAME_SECONDS + 0.01
        before = self.serial
        with self.assertRaises(driver.StaleFrame):
            owner.agent_input(
                "ScrollStale", action, window, frame, captured, owner.anchor + 390, None
            )
        self.assertEqual(self.serial, before)
        self.assertEqual(owner.gui_scroll_dispatches, 0)
        self.assertEqual(self.semantic_scrolls, 0)
        self.assertFalse(owner.uncertain_input)

    def test_eight_second_actor_delays_bind_actual_observer_within_twenty(self):
        self.actor_delays = {"gui_select": 8, "gui_play": 8, "gui_quit": 8}
        owner = self.owner
        owner.launch()
        self.assertTrue(owner.dispatch_settled)
        self.assertTrue(self.observer_api.state.bound)
        self.assertGreaterEqual(owner.gui_arm_started, self.gui_play_decided_at)
        self.assertGreaterEqual(owner.request, self.observer_ready_at)
        bound = self.first_bound_record
        self.assertIsNotNone(bound)
        self.assertLess(bound["observed"] - owner.request, 20)
        self.r1_measurement = dict(
            case="delayed_gui_actor_8s",
            ready_to_request=owner.request - self.observer_ready_at,
            request_to_play=self.play_input_at - owner.request,
            request_to_first_bound=bound["observed"] - owner.request,
            ready_request_limit=20,
            request_bind_limit=20,
            actual_observer_phase=bound["state"]["phase"],
            mechanical_receipts=3,
        )
        self.assertLess(owner.request - self.observer_ready_at, 20)
        self.assertEqual(self.stage_counts, {"gui_select": 1, "gui_play": 1, "gui_quit": 1})
        mechanical = list(owner.directory.glob("*-mechanical.json"))
        self.assertEqual(len(mechanical), 3)
        self.assertTrue(
            all(
                catalogue.decode(p.read_bytes())["evidence_kind"].startswith("machine_exact_gui_")
                for p in mechanical
            )
        )
        outcome = owner.observe()
        owner.persist_outcome(outcome)
        self.assertTrue(owner.stop())
        self.assertTrue(owner.restore())
        self.assertEqual(self.observer_stop_requests, [])

    def test_actor_twenty_point_eight_sixteen_then_single_arm_and_actual_bind(self):
        self.actor_delays = {"gui_play": 20.816}
        self.test_actual_decisions_entry_observer_close_game_quit_and_restoration()
        owner = self.owner
        self.assertGreaterEqual(owner.gui_arm_started, self.gui_play_decided_at)
        self.assertGreater(self.gui_play_decided_at - owner.anchor, 20.816)
        self.assertLess(owner.request - self.observer_ready_at, 20)
        self.assertLess(self.first_bound_record["observed"] - owner.request, 20)
        self.assertEqual(len([p for p in owner.creation if p["role"] == "observer"]), 1)
        original_arm = owner.gui_arm_started
        before = self.serial
        with self.assertRaisesRegex(ControlRefused, "One observer arm"):
            owner.arm_gui_play_observer()
        self.assertEqual(owner.gui_arm_started, original_arm)
        self.assertEqual(self.serial, before)
        self.r1_measurement = dict(
            case="actor_20.816_then_single_arm",
            actor_delay=20.816,
            decision_to_arm=owner.gui_arm_started - self.gui_play_decided_at,
            ready_to_request=owner.request - self.observer_ready_at,
            request_to_play=self.play_input_at - owner.request,
            request_to_first_bound=self.first_bound_record["observed"] - owner.request,
            ready_request_limit=20,
            request_bind_limit=20,
            actual_observer_phase=self.first_bound_record["state"]["phase"],
            observer_arms=1,
        )

    def test_final_ingress_after_ready_plus_twenty_refuses_before_any_play_input(self):
        self.final_ingress_delay = 20.01
        owner = self.owner
        with self.assertRaisesRegex(ControlRefused, r"observer exited|ready\+20"):
            owner.launch()
        self.assertEqual(self.actions, [])
        self.assertFalse(hasattr(owner, "request"))
        self.assertFalse((owner.helper_packet / "request.json").exists())
        self.assertFalse((owner.helper_packet / "bound.json").exists())
        self.assertEqual(self.observer_api.state.reason, "request_timeout")
        self.assertFalse(owner.dispatch_settled)
        self.r1_measurement = dict(
            case="final_ingress_after_ready_plus20",
            elapsed_since_ready=time.monotonic() - self.observer_ready_at,
            actual_observer_phase=self.observer_api.state.phase,
            reason=self.observer_api.state.reason,
            request_published=False,
            inputs=len(self.actions),
            bound_receipt=False,
        )

    def test_review_after_request_plus_twenty_refuses_new_input_and_false_bind(self):
        self.provider_delay = 20.01
        owner = self.owner
        with self.assertRaisesRegex(ControlRefused, r"request\+20"):
            owner.launch()
        self.assertEqual([state for state, _ in self.actions], ["selected"])
        request = catalogue.decode((owner.helper_packet / "request.json").read_bytes())
        self.assertEqual(request["request"], owner.request)
        self.assertFalse((owner.helper_packet / "bound.json").exists())
        self.assertEqual(self.observer_api.state.reason, "binding_deadline")
        self.assertFalse(owner.dispatch_settled)
        self.assertEqual(owner.profiles._state()["active"], owner.job["return_profile"])
        self.assertFalse(owner.monitor.games())
        self.r1_measurement = dict(
            case="initial_review_after_request_plus20",
            elapsed_since_request=time.monotonic() - owner.request,
            actual_observer_phase=self.observer_api.state.phase,
            reason=self.observer_api.state.reason,
            original_request=request["request"],
            inputs=len(self.actions),
            bound_receipt=False,
        )

    def assert_unknown_holds(self, mode):
        self.fail_mode = mode
        owner = self.owner
        with self.assertRaises((ControlRefused, self.common.Refused)):
            owner.launch()
        self.assertFalse(owner.dispatch_settled)
        if mode == "service-live":
            self.assertEqual(owner.observer.poll(), 0)
            result = catalogue.decode((owner.helper_packet / "result.json").read_bytes())
            self.assertTrue(result["result"]["stop_requested"])
            self.assertTrue(self.observer_api.state.terminal)
        elif mode in ("observer-pid-mismatch", "expiry-inflight"):
            self.assertEqual(owner.observer.poll(), 2)
            self.assertTrue(self.observer_api.state.terminal)
        else:
            self.assertIsNone(owner.observer.poll())
        with self.assertRaisesRegex(ControlRefused, "observer retained"):
            owner.stop()
        reason = (
            "Uncompleted provider work" if owner.uncertain_input else "Unsettled packaged dispatch"
        )
        with self.assertRaisesRegex(ControlRefused, reason):
            owner.restore()

    def test_unknown_success_never_becomes_a_protocol_response(self):
        self.assert_unknown_holds("unknown-success")
        self.processes.pop(71)
        self.processes.pop(72)
        self.release_lock()
        with self.assertRaisesRegex(ControlRefused, "observer retained"):
            self.owner.stop()
        self.assertIsNone(self.owner.observer.poll())

    def test_observer_pid_mismatch_cannot_settle_status_and_game(self):
        self.assert_unknown_holds("observer-pid-mismatch")

    def test_pid_birth_mismatch_retains_observer_and_data(self):
        self.assert_unknown_holds("pid-mismatch")

    def test_frontend_exit_with_live_backend_cannot_restore(self):
        self.assert_unknown_holds("service-live")
        self.assertNotIn(71, self.processes)
        self.assertIn(72, self.processes)

    def test_original_expiry_with_commit_inflight_remains_hold(self):
        self.assert_unknown_holds("expiry-inflight")
        self.assertEqual(self.owner.launch_deadline, self.owner.anchor + 390)

    def test_foreign_activation_during_commit_refuses(self):
        self.assert_unknown_holds("foreign-active")

    def test_foreign_active_before_open_refuses_without_frontend(self):
        self.owner.app.activate("original-game")
        with self.assertRaisesRegex(ControlRefused, "Prelaunch return profile"):
            self.owner.launch()
        self.assertFalse(self.processes)
        self.assertFalse(self.owner.gui_launch_started)


if __name__ == "__main__":
    unittest.main()
