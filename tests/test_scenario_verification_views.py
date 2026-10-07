"""Application/catalogue/launcher methods, without constructing or running a GUI."""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import os
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_application import FakeInstallation
from test_scenario_evidence import Fixture, h, known_options
from smr_launcher.application import LauncherApplication
from smr_launcher.gui import LauncherWindow
from smr_launcher.scenario_evidence import EvidenceRecord, EvidenceError, SCHEMA, REQUIRED_ACTIONS
from smr_launcher.verification import CHECKS, GameplayVerification


class Value:
    def __init__(self): self.value=""
    def set(self,value): self.value=value
    def get(self): return self.value


class ScenarioViewsTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name).resolve();self.installation=FakeInstallation(self.root)
        self.options=known_options()
        stock=self.installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        stock.mkdir(parents=True);(stock / "stock.txt").write_bytes(b"synthetic stock")
        self.app=LauncherApplication(self.installation,self.root / "library")
        self.app.setup();archive=self.root / "synthetic.tar"
        with tarfile.open(archive,"w") as stream:
            for name,data in (("UserMaps/A/RRT_Scenario_User_A.xml",b"<Scenario>A</Scenario>"),
                              ("UserMaps/B/RRT_Scenario_User_B.xml",b"<Scenario>B</Scenario>"),
                              ("CustomAssets/provider.txt",b"synthetic provider")):
                entry=tarfile.TarInfo(name);entry.size=len(data);stream.addfile(entry,BytesIO(data))
        self.record=self.app.import_archive(archive)
        self.app.activate(self.record.profile_id)
        scenarios,context,resources,marker=self.app._scenario_context(self.record,self.options)
        self.a,self.b=scenarios
        v=dict(schema="smr-e01-inventory-v1",editions=[dict(archive_sha256=self.record.archive_sha256,
            edition_id=self.record.variant_id,scenarios=[dict(scenario_relative_path=s["scenario_path"],
            scenario_sha256=s["scenario_sha256"]) for s in scenarios])])
        inventory=self.root / "accepted-inventory.json";inventory.write_text(json.dumps(v))
        iid=self.app.bind_scenario_inventory(inventory,h(inventory.read_bytes()))
        self.build=self.app.prepare_scenario_evidence_build(self.record,iid,options=self.options)
        fixture_root=self.root / "fixture";fixture_root.mkdir();self.f=Fixture(fixture_root)
        self.f.store=self.app.scenario_evidence_store;self.f.build=self.build;self.f.a=self.a;self.f.b=self.b
        self.f.context=context
        p=self.f.store.root / "receipt.txt";p.write_bytes(b"Fixed synthetic evidence")
        from smr_launcher.scenario_evidence import receipt
        self.f.receipt=receipt(self.f.store.root,"receipt.txt")
        self.gui=LauncherWindow.__new__(LauncherWindow);self.gui.app=self.app;self.gui.status=Value()

    def test_one_scenario_one_action_both_consumers_leave_sibling_unknown(self):
        c=self.f.contract();obs=self.f.observation(c)
        self.app.record_scenario_action(obs,self.f.interpretation(obs))
        result=self.app.catalogue_verification(options=self.options)[self.record.variant_id]
        self.assertEqual(result.status,"Not verified")
        self.assertEqual(dict(result.scenarios[0].actions)["camera"],"pass")
        self.assertEqual(dict(result.scenarios[0].actions)["load"],"unknown")
        self.assertEqual(set(dict(result.scenarios[1].actions).values()),{"unknown"})
        self.assertEqual(self.gui._verification_text(self.record),"Not verified")
        details=self.gui._verification_details(self.app.verification_for(self.record,options=self.options))
        self.assertIn("Camera: pass",details);self.assertIn("Load: unknown",details)
        self.assertIn(self.b["scenario_path"] + " · Not verified",details)

    def test_launcher_filter_and_cli_use_authoritative_status(self):
        from smr_launcher.cli import _status
        c=self.f.contract();self.f.record(c)
        self.gui.search=Value();self.gui.map_filter=Value();self.gui.sort_order=Value()
        self.gui.metadata_cache={}
        self.gui.map_filter.set("Verified")
        self.assertEqual(self.gui._filtered(),[])
        self.gui.map_filter.set("Not verified")
        self.assertEqual(self.gui._filtered(),[self.record])
        self.assertEqual(_status(self.app)["maps"][0]["verification"],"Not verified")

    def test_partial_catalogue_edition_cannot_hide_sibling(self):
        from dataclasses import replace
        c=self.f.contract()
        for action in REQUIRED_ACTIONS: self.f.record(c,action)
        with self.assertRaises(EvidenceError):
            self.app.verification_for(replace(self.record,scenarios=(self.a["scenario_path"],)),options=self.options)

    def test_unknown_actual_options_prevent_broad_verified_badge(self):
        c=self.f.contract()
        for a in REQUIRED_ACTIONS: self.f.record(c,a)
        result=self.app.scenario_status_for(self.record,options=self.options)
        self.assertEqual(result.scenarios[0].status,"Verified")
        self.assertEqual(result.scenarios[1].status,"Not verified")
        self.assertEqual(result.status,"Not verified")
        self.assertEqual(self.gui._verification_text(self.record),"Not verified")
        self.assertEqual(set(dict(self.app.verification_for(self.record).scenarios[0].actions).values()),{"unknown"})

    def test_resource_bytes_metadata_provider_options_scenario_game_invalidation(self):
        c=self.f.contract();self.f.record(c)
        def camera(): return dict(self.app.scenario_status_for(self.record,options=self.options).scenarios[0].actions)["camera"]
        self.assertEqual(camera(),"pass")
        root=self.app.profiles.live
        p=root / "CustomAssets/provider.txt";st=p.stat();data=p.read_bytes()
        os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns+1));self.assertEqual(camera(),"unknown")
        os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns));self.assertEqual(camera(),"pass")
        # Profile guards reject altered authored assets before any status can grant a pass.
        p.chmod(0o600);p.write_bytes(b"changed")
        with self.assertRaises(Exception): self.app.scenario_status_for(self.record,options=self.options)
        p.write_bytes(data);os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))
        scenario=root / self.a["scenario_path"]
        before=scenario.stat();original=scenario.read_bytes()
        scenario.chmod(0o600);scenario.write_bytes(b"<Scenario>Changed</Scenario>")
        with self.assertRaises(Exception): self.app.scenario_status_for(self.record,options=self.options)
        scenario.write_bytes(original);os.utime(scenario,ns=(before.st_atime_ns,before.st_mtime_ns))
        scenario.chmod(before.st_mode & 0o777)
        stock=self.installation.steamapps_root / "common/Sid Meier's Railroads/SMRailroadsData/assets"
        extra=stock / "new-provider.txt";extra.write_bytes(b"extra")
        self.assertEqual(camera(),"unknown");extra.unlink()
        self.assertEqual(dict(self.app.scenario_status_for(self.record,options=known_options({"ai":0})).scenarios[0].actions)["camera"],"unknown")
        self.installation.executable_sha256=h(b"different executable")
        with self.assertRaises(Exception): self.app.scenario_status_for(self.record,options=self.options)

    def test_legacy_preserves_every_check_issue_unscoped_and_no_pass(self):
        marker=self.app.profiles._marker(self.app.profiles.live,self.record.profile_id)
        old=GameplayVerification(self.record.archive_sha256,self.record.game_executable_sha256,
            self.record.variant_id,marker["assets_sha256"],"2026-10-07T00:00:00+00:00",
            "Synthetic player",dict.fromkeys(CHECKS,True),"Limited old check","Historical synthetic failure")
        self.app.verification_store.append(old)
        success=GameplayVerification.from_json(dict(old.as_json(),issue="",observed_at="2026-10-07T01:00:00+00:00"))
        self.app.verification_store.append(success)
        before=self.app.verification_store.path.read_bytes()
        result=self.app.verification_for(self.record)
        self.assertEqual(result.status,"Known issue")
        self.assertEqual(result.historical,(old,success))
        self.assertEqual(set(dict(result.scenarios[0].actions).values()),{"unknown"})
        self.assertIn("Historical unscoped issue: Historical synthetic failure",self.gui._verification_details(result))
        self.assertEqual(before,self.app.verification_store.path.read_bytes())
        self.assertEqual(success.status,"Not verified")

    def test_corrupt_receipt_propagates_to_all_views(self):
        c=self.f.contract();self.f.record(c)
        p=self.f.store.root / "receipt.txt";p.unlink()
        with self.assertRaises(EvidenceError): self.app.catalogue_verification(options=self.options)
        self.assertEqual(self.gui._verification_text(self.record),"Needs inspection")
        self.assertIn("needs inspection",self.gui.status.value)

    def test_r1_matching_unknown_options_cross_all_two_scenario_consumers(self):
        from smr_launcher.cli import _status
        for raw_options in ({}, {"coverage":"unknown"}, {"ai":1}):
            inv=next(r for r in self.app.scenario_evidence_store.records() if r.kind=="inventory")
            self.f.build=self.app.prepare_scenario_evidence_build(self.record,inv.id,options=raw_options)
            self.f.context=self.f.build.payload["context"]
            for scenario in (self.a,self.b):
                c=self.f.contract(scenario)
                for action in REQUIRED_ACTIONS:
                    obs=self.f.observation(c,action)
                    self.app.record_scenario_action(obs,self.f.interpretation(obs))
            explicit=self.app.verification_for(self.record,options=raw_options)
            default=self.app.verification_for(self.record)
            catalogue=self.app.catalogue_verification()[self.record.variant_id]
            for result in (explicit,default,catalogue):
                self.assertEqual(result.status,"Not verified")
                for scenario in result.scenarios:
                    self.assertEqual(set(dict(scenario.actions).values()),{"unknown"})
            self.assertEqual(_status(self.app)["maps"][0]["verification"],"Not verified")
            self.assertEqual(self.gui._verification_text(self.record),"Not verified")
            details=self.gui._verification_details(default)
            self.assertNotIn(": pass",details)
            self.assertIn("Exact game options or resource coverage has not been confirmed",details)
            self.gui.search=Value();self.gui.map_filter=Value();self.gui.sort_order=Value();self.gui.metadata_cache={}
            self.gui.map_filter.set("Verified");self.assertEqual(self.gui._filtered(),[])
            self.gui.map_filter.set("Not verified");self.assertEqual(self.gui._filtered(),[self.record])

    def test_explicit_known_option_control_can_verify_both_exact_scenarios(self):
        for scenario in (self.a,self.b):
            c=self.f.contract(scenario)
            for action in REQUIRED_ACTIONS: self.f.record(c,action)
        self.assertEqual(self.app.verification_for(self.record,options=self.options).status,"Verified")
        self.assertEqual(self.app.catalogue_verification(options=self.options)[self.record.variant_id].status,"Verified")
        # Actual options collection is future work; default user-facing status
        # never substitutes the prior run's attestation for current options.
        self.assertEqual(self.gui._verification_text(self.record),"Not verified")

    def test_pending_failure_cross_layer_with_exact_unknown_options(self):
        from smr_launcher.cli import _status
        inv=next(r for r in self.app.scenario_evidence_store.records() if r.kind=="inventory")
        self.f.build=self.app.prepare_scenario_evidence_build(self.record,inv.id,options={})
        self.f.context=self.f.build.payload["context"]
        c=self.f.contract()
        failed=self.f.observation(c,"load","failure")
        wrong=self.f.interpretation(self.f.observation(c,"camera"),"failure")
        with self.assertRaises(EvidenceError): self.app.record_scenario_action(failed,wrong)
        self.assertIn(failed,self.app.scenario_evidence_store.records())
        self.assertEqual(self.app.verification_for(self.record).status,"Known issue")
        self.assertEqual(self.app.catalogue_verification()[self.record.variant_id].status,"Known issue")
        self.assertEqual(_status(self.app)["maps"][0]["verification"],"Known issue")
        self.assertEqual(self.gui._verification_text(self.record),"Known issue")
        result=self.app.verification_for(self.record)
        self.assertEqual(dict(result.scenarios[0].actions)["load"],"failure")
        self.assertEqual(set(dict(result.scenarios[1].actions).values()),{"unknown"})
