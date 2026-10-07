"""Fixed synthetic protocols exercise the actual ledger and reducer."""
from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.scenario_evidence import (EvidenceRecord, EvidenceStore, EvidenceError,
    SCHEMA, FEATURES, REQUIRED_ACTIONS, bind_inventory, build_record, scenario_key,
    receipt, canonical, digest, attest_options, engine_context, LEGACY_SCHEMA, context_identity)
from smr_launcher.resource_identity import fingerprint_resources
from smr_launcher.verification import reduce_verification


def h(data):
    return hashlib.sha256(data).hexdigest()


# Independent pre-observation toy oracle retained from the accepted review.
EXPECTED = {'ai_competition': 'AI builds a competing route', 'annex_construction': 'annex collects authored coal', 'autosave': 'autosave exists at authored checkpoint', 'bridge_construction': 'bridge spans authored river', 'camera': 'camera reaches authored northwest marker', 'cargo_transport': 'coal delivery counter increases by 1', 'continue_after_reload': 'restored train completes next delivery', 'difficulty': 'hard difficulty applies authored cost 200', 'era_transition': 'era advances to authored year 1900', 'extended_play': 'simulation reaches final authored checkpoint', 'goals_events': 'coal objective completes and event appears', 'industry_construction_production': 'coal plant produces one unit', 'load': 'scenario A marker visible', 'manual_save': 'named save contains checkpoint A', 'reload': 'named save restores checkpoint A', 'station_construction': 'station available at authored city A', 'track_construction': 'track connects coordinates 1 and 2', 'train_purchase_routing': 'train 1 follows A to B schedule', 'tunnel_construction': 'tunnel traverses authored ridge'}
FEATURE_ACTION = {'animations': 'train_purchase_routing', 'difficulty': 'difficulty', 'eras': 'era_transition', 'events': 'goals_events', 'geometry': 'camera', 'goals': 'goals_events', 'industries': 'industry_construction_production', 'materials': 'camera', 'placements': 'station_construction', 'production': 'industry_construction_production', 'terrain': 'camera', 'transport': 'cargo_transport'}
FEATURE_EXPECTED = {'animations': 'wheel rotates', 'difficulty': 'hard build cost is 200', 'eras': '1900 transition uses authored roster', 'events': 'delivery event has authored text', 'geometry': 'authored triangle is visible', 'goals': 'coal objective awards authored 10 points', 'industries': 'authored coal plant is buildable', 'materials': 'triangle is blue', 'placements': 'city A has its authored station site', 'production': 'plant yields one coal unit', 'terrain': 'authored ridge and river are visible', 'transport': 'coal uses coal car and arrives at B'}
CARGO = {'car_attachment': 'coal car attached', 'delivery': 'one coal delivered to B', 'goods': 'coal'}

def known_options(values=None):
    return attest_options({"ai": 1} if values is None else values,
                          required_fields=["ai"], contract_version="synthetic-options-v1",
                          basis="Independent toy option control fixed before observations")


class Fixture:
    def __init__(self, root):
        self.root = root
        self.store = EvidenceStore(root / "facts.jsonl")
        assets = root / "assets"; assets.mkdir()
        (assets / "synthetic.txt").write_bytes(b"fixed stock provider")
        self.roots = {"installed_assets":assets, "custom_assets":root / "CustomAssets", "user_maps":root / "UserMaps"}
        for role in ("custom_assets", "user_maps"):
            self.roots[role].mkdir()
        self.resources = fingerprint_resources(self.roots)
        self.context = engine_context(h(b"engine"), h(b"prepared bytes"), self.resources, known_options())
        self.a = scenario_key(h(b"archive"), h(b"edition"), "UserMaps/A.xml", h(b"scenario A"))
        self.b = scenario_key(h(b"archive"), h(b"edition"), "UserMaps/B.xml", h(b"scenario B"))
        self.sibling = scenario_key(h(b"archive"), h(b"sibling edition"), "UserMaps/A.xml", h(b"scenario A"))
        self.inventory = EvidenceRecord.create("inventory", dict(sha256=h(b"accepted inventory"),
            schema="smr-e01-inventory-v1", scenarios=[self.a,self.b,self.sibling]))
        self.store.append(self.inventory)
        self.build = build_record(self.inventory, self.a["archive_sha256"], self.a["edition_id"],
            h(b"prepared bytes"), self.resources, self.context["game_sha256"], self.context["options"])
        self.store.append(self.build)
        (root / "receipt.txt").write_bytes(b"Independent fixed assertion: camera reaches upper-left marker.")
        self.receipt = receipt(root, "receipt.txt")

    def contract(self, scenario=None, unknown=None, feature_unknown=None, not_applicable=None):
        actions = {}
        for action in REQUIRED_ACTIONS:
            actions[action] = dict(applicability="unknown" if action == unknown else
                "not_applicable" if action == not_applicable else "applicable",
                basis="Synthetic isolated fixture authored with a fixed control; no water means no bridge locations." if action == not_applicable else "Independent fixed synthetic scenario contract",
                protocol=dict(setup="Fresh synthetic scenario, one AI, fixed seed 42",
                              trigger="Execute fixed " + action + " sequence",
                              expected=EXPECTED[action],
                              expectation_basis="Independently written synthetic acceptance specification"))
        actions["cargo_transport"]["protocol"]["required_assertions"] = CARGO
        features = {f:dict(state="unknown" if f == feature_unknown else "reviewed",
            authored_intent=FEATURE_EXPECTED[f], expected_loaded=FEATURE_EXPECTED[f],
            basis="Reviewed independent synthetic control", actions=[FEATURE_ACTION[f]]) for f in FEATURES}
        for action in REQUIRED_ACTIONS:
            covered = {f:FEATURE_EXPECTED[f] for f in FEATURES if FEATURE_ACTION[f] == action}
            if covered:
                actions[action]["protocol"]["feature_assertions"] = covered
        c = EvidenceRecord.create("contract", dict(scenario=scenario or self.a, build=self.build.id,
            version=SCHEMA, actions=actions, features=features), (self.build.id,))
        self.store.append(c)
        return c

    def observation(self, contract, action="camera", outcome="pass"):
        p = contract.payload
        return EvidenceRecord.create("observation", dict(scenario=p["scenario"], build=self.build.id,
            contract=contract.id, action=action, protocol=p["actions"][action]["protocol"],
            actual_assertions=dict(expected=EXPECTED[action], satisfied=True,
                                   features={f:FEATURE_EXPECTED[f] for f in FEATURES if FEATURE_ACTION[f] == action},
                                   **(CARGO if action == "cargo_transport" else {})),
            evidence=[self.receipt], actual_context=self.context, outcome=outcome,
            observed_at="2026-10-07T14:00:00+00:00"), (self.build.id,contract.id))

    def interpretation(self, obs, outcome="pass", version="analyzer-v1"):
        return EvidenceRecord.create("interpretation", dict(observation=obs.id, analysis_version=version,
            contract_version=SCHEMA, outcome=outcome, reason="Independent assertion evaluation"), (obs.id,))

    def record(self, contract, action="camera", outcome="pass"):
        obs = self.observation(contract, action, outcome)
        self.store.append(obs); self.store.append(self.interpretation(obs, outcome))
        return obs

    def status(self, scenarios=None, context=None):
        return reduce_verification(self.store.records(), scenarios or [self.a,self.b], context or self.context)


class ScenarioEvidenceTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.root = Path(t.name).resolve(); self.f = Fixture(self.root)

    def test_one_action_leaves_other_actions_and_sibling_unknown(self):
        c = self.f.contract(); self.f.record(c)
        r = self.f.status()
        self.assertEqual(r.status, "Not verified")
        self.assertEqual(dict(r.scenarios[0].actions)["camera"], "pass")
        self.assertEqual(dict(r.scenarios[0].actions)["load"], "unknown")
        self.assertEqual(set(dict(r.scenarios[1].actions).values()), {"unknown"})
        self.assertEqual(len(dict(r.scenarios[1].actions)), 19)

    def test_full_exact_coverage_passes_only_single_scenario(self):
        c = self.f.contract(not_applicable="bridge_construction")
        for a in REQUIRED_ACTIONS:
            if a != "bridge_construction": self.f.record(c,a)
        self.assertEqual(self.f.status([self.f.a]).status, "Verified")
        self.assertEqual(self.f.status().status, "Not verified")
        self.assertEqual(self.f.status([self.f.sibling]).status, "Not verified")
        self.assertEqual(set(dict(self.f.status([self.f.sibling]).scenarios[0].actions).values()), {"unknown"})

    def test_unknown_applicability_or_feature_review_prevents_verified(self):
        for kwargs in (dict(unknown="camera"), dict(feature_unknown="production")):
            c = self.f.contract(**kwargs)
            for a in REQUIRED_ACTIONS: self.f.record(c,a)
            self.assertEqual(self.f.status([self.f.a]).status, "Not verified")

    def test_changed_context_and_scenario_invalidate(self):
        c = self.f.contract(); self.f.record(c)
        for key,value in (("game_sha256",h(b"new engine")),("resources_identity",h(b"mtime")),("options",{"ai":0})):
            context = dict(self.f.context); context[key] = value
            self.assertEqual(dict(self.f.status([self.f.a], context).scenarios[0].actions)["camera"], "unknown")
        for key,value in (("scenario_sha256",h(b"changed scenario")),("archive_sha256",h(b"changed archive")),
                          ("edition_id",h(b"changed edition")),("scenario_path","UserMaps/Different.xml")):
            changed = dict(self.f.a, **{key:value})
            self.assertEqual(set(dict(self.f.status([changed]).scenarios[0].actions).values()), {"unknown"})
        p = self.root / "assets/synthetic.txt"; st = p.stat()
        os.utime(p, ns=(st.st_atime_ns,st.st_mtime_ns+1))
        self.assertNotEqual(fingerprint_resources({"installed_assets":p.parent})["identity"],self.f.context["resources_identity"])
        p.write_bytes(b"changed provider bytes")
        self.assertNotEqual(fingerprint_resources({"installed_assets":p.parent})["identity"],self.f.context["resources_identity"])
        (p.parent / "another.txt").write_bytes(b"additional provider")
        context = dict(self.f.context,resources_identity=fingerprint_resources({"installed_assets":p.parent})["identity"])
        self.assertEqual(dict(self.f.status([self.f.a],context).scenarios[0].actions)["camera"], "unknown")

    def test_logging_provenance_does_not_duplicate_behavior(self):
        c=self.f.contract(); obs=self.f.record(c)
        before=self.f.status([self.f.a]).as_json()
        for version in (b"logging v1",b"logging v2"):
            self.f.store.append(EvidenceRecord.create("provenance",dict(observation=obs.id,
                driver_sha256=h(version),driver_version=version.decode()),(obs.id,)))
            self.assertEqual(self.f.observation(c).id,obs.id)
            self.assertEqual(self.f.observation(c).observed_input_identity,obs.observed_input_identity)
        self.assertEqual(before,self.f.status([self.f.a]).as_json())
        self.assertEqual(sum(r.kind=="observation" for r in self.f.store.records()),1)

    def test_new_interpretation_withdraws_pass_without_deleting_facts(self):
        c=self.f.contract(); obs=self.f.record(c)
        before=self.f.store.path.read_bytes()
        self.f.store.append(self.f.interpretation(obs,"unknown","analyzer-v2"))
        self.assertTrue(self.f.store.path.read_bytes().startswith(before))
        self.assertIn(obs,self.f.store.records())
        self.assertEqual(dict(self.f.status([self.f.a]).scenarios[0].actions)["camera"],"unknown")
        self.f.store.append(self.f.interpretation(obs,"failure","analyzer-v3"))
        self.assertEqual(self.f.status([self.f.a]).status,"Known issue")

    def test_contract_revision_cannot_erase_failure(self):
        c=self.f.contract();obs=self.f.record(c,outcome="failure")
        self.f.contract(unknown="camera")
        self.assertEqual(self.f.status([self.f.a]).status,"Known issue")
        self.f.store.append(self.f.interpretation(obs,"unknown","withdrawal-v2"))
        self.assertEqual(self.f.status([self.f.a]).status,"Not verified")

    def test_missing_feature_or_cargo_assertion_cannot_pass(self):
        c=self.f.contract()
        for action in ("camera","cargo_transport"):
            obs=self.f.observation(c,action)
            actual=dict(obs.payload["actual_assertions"])
            if action=="camera": actual["features"]={}
            else: actual.pop("car_attachment")
            bad=EvidenceRecord.create("observation",dict(obs.payload,actual_assertions=actual),obs.value["refs"])
            self.f.store.append(bad);self.f.store.append(self.f.interpretation(bad))
            self.assertEqual(dict(self.f.status([self.f.a]).scenarios[0].actions)[action],"unknown")

    def test_partial_historical_unknown_and_justified_na_stay_distinct(self):
        c=self.f.contract(unknown="tunnel_construction",not_applicable="bridge_construction")
        self.f.record(c,"load","partial")
        self.f.record(c,"cargo_transport","historical_limited")
        actions=dict(self.f.status([self.f.a]).scenarios[0].actions)
        self.assertEqual(actions["load"],"partial")
        self.assertEqual(actions["cargo_transport"],"historical_limited")
        self.assertEqual(actions["tunnel_construction"],"unknown")
        self.assertEqual(actions["bridge_construction"],"not_applicable")
        self.assertEqual(self.f.status([self.f.a]).status,"Not verified")

    def test_parent_links_directory_files_and_receipt_overflow_are_rejected(self):
        linked=self.root / "linked-dir";linked.symlink_to(self.root / "assets",target_is_directory=True)
        with self.assertRaises(EvidenceError): receipt(self.root,"linked-dir/synthetic.txt")
        with self.assertRaises(EvidenceError): EvidenceStore(self.root / "assets")
        oversized=self.root / "oversized.txt"
        with oversized.open("wb") as stream: stream.truncate(16*1024*1024+1)
        with self.assertRaises(EvidenceError): receipt(self.root,"oversized.txt")
        self.assertEqual(oversized.stat().st_size,16*1024*1024+1)
        target=self.root / "hardlink";os.link(self.f.store.path,target)
        with self.assertRaises(EvidenceError): EvidenceStore(target)
        target.unlink()

    def test_deleted_dependency_and_duplicate_json_field_fail_closed(self):
        c=self.f.contract();obs=self.f.record(c)
        data=self.f.store.path.read_bytes();lines=data.splitlines(keepends=True)
        # Remove a complete earlier build transaction; observation references now dangle.
        self.f.store.path.write_bytes(b"".join(lines[:2]+lines[4:]))
        corrupt=self.f.store.path.read_bytes()
        with self.assertRaises(EvidenceError): self.f.store.records()
        with self.assertRaises(EvidenceError): self.f.store.append(obs)
        self.assertEqual(corrupt,self.f.store.path.read_bytes())
        self.f.store.path.write_bytes(data+b'{"commit":"a","commit":"b","count":1}\n')
        with self.assertRaises(EvidenceError): self.f.store.records()

    def test_dedupe_and_claim_conflict(self):
        before=self.f.store.path.read_bytes()
        self.assertEqual(self.f.store.append(self.f.build),self.f.build.id)
        self.assertEqual(before,self.f.store.path.read_bytes())
        with self.assertRaises(EvidenceError):
            EvidenceRecord.create("build",dict(self.f.build.payload,edition_id=h(b"different")),
                                  (self.f.inventory.id,),claimed_id=self.f.build.id)
        mutable=self.f.build.payload; mutable["context"]["options"]["values"]["ai"]=9
        self.assertEqual(self.f.build.payload["context"]["options"]["values"],{"ai":1})

    def test_corrupt_suffixes_fail_closed_preserve_bytes(self):
        base=self.f.store.path.read_bytes()
        c=self.f.contract(); valid=self.f.store.path.read_bytes()
        for suffix in (b'{"broken":',b'garbage\n',c.encoded+b'\n',b'{}\n',b'x'*1048577+b'\n'):
            self.f.store.path.write_bytes(base+suffix)
            with self.assertRaises(EvidenceError): self.f.store.records()
            before=self.f.store.path.read_bytes()
            with self.assertRaises(EvidenceError): self.f.store.append(c)
            self.assertEqual(before,self.f.store.path.read_bytes())
        self.f.store.path.write_bytes(valid)

    def test_interrupted_write_cannot_count_or_allow_next_append(self):
        c=self.f.contract(); obs=self.f.observation(c); write=os.write
        def interrupted(fd,data): return write(fd,data[:len(obs.encoded)+1])
        with patch("smr_launcher.scenario_evidence.os.write",side_effect=interrupted):
            with self.assertRaises(EvidenceError): self.f.store.append(obs)
        before=self.f.store.path.read_bytes()
        with self.assertRaises(EvidenceError): self.f.store.records()
        with self.assertRaises(EvidenceError): self.f.store.append(obs)
        self.assertEqual(before,self.f.store.path.read_bytes())

    def test_missing_record_receipt_and_corruption(self):
        c=self.f.contract(); obs=self.f.observation(c)
        broken=EvidenceRecord.create("interpretation",dict(observation=h(b"absent"),analysis_version="v1",
            contract_version=SCHEMA,outcome="pass",reason="missing"),(h(b"absent"),))
        before=self.f.store.path.read_bytes()
        with self.assertRaises(EvidenceError): self.f.store.append(broken)
        self.assertEqual(before,self.f.store.path.read_bytes())
        self.f.store.append(obs)
        for mode in ("changed","missing"):
            p=self.root / "receipt.txt"
            if mode=="changed": p.write_text("different")
            else: p.unlink()
            before=self.f.store.path.read_bytes()
            with self.assertRaises(EvidenceError): self.f.store.records()
            with self.assertRaises(EvidenceError): self.f.store.append(self.f.interpretation(obs))
            self.assertEqual(before,self.f.store.path.read_bytes())

    def test_unsafe_paths_overflow_and_single_writer(self):
        link=self.root / "linked"; link.symlink_to(self.f.store.path)
        with self.assertRaises(EvidenceError): EvidenceStore(link)
        with self.assertRaises(EvidenceError): EvidenceStore(self.root / "../escape")
        with self.assertRaises(EvidenceError): receipt(self.root,"../escape")
        before=self.f.store.path.read_bytes()
        tiny=EvidenceStore(self.f.store.path,max_bytes=len(before),max_records=2)
        c=self.f.contract()
        self.f.store.path.write_bytes(before)
        with self.assertRaises(EvidenceError): tiny.append(c)
        self.assertEqual(before,self.f.store.path.read_bytes())
        with self.f.store._writer():
            with self.assertRaises(EvidenceError): self.f.store.append(c)
        with self.assertRaises(EvidenceError): EvidenceRecord.create("finding",dict(scenario=self.f.a,
            build=self.f.build.id,analyzer_version="v",finding="x"*1048576),(self.f.build.id,))

    def test_pass_requires_exact_protocol_and_readable_evidence(self):
        c=self.f.contract(); obs=self.f.observation(c)
        for changes in (dict(evidence=[]),dict(protocol=dict(obs.payload["protocol"],expected="self-issued expectation"))):
            with self.assertRaises(EvidenceError):
                bad=EvidenceRecord.create("observation",dict(obs.payload,**changes),obs.value["refs"])
                self.f.store.append(bad)
        limited=EvidenceRecord.create("observation",dict(obs.payload,evidence=[],outcome="historical_limited"),obs.value["refs"])
        self.f.store.append(limited);self.f.store.append(self.f.interpretation(limited))
        self.assertEqual(dict(self.f.status([self.f.a]).scenarios[0].actions)["camera"],"unknown")

    def test_static_findings_do_not_pass_actions_and_snapshot_is_atomic(self):
        self.f.store.append(EvidenceRecord.create("finding",dict(scenario=self.f.a,build=self.f.build.id,
            analyzer_version="v1",finding="synthetic preparation success"),(self.f.build.id,)))
        self.assertEqual(set(dict(self.f.status().scenarios[0].actions).values()),{"unknown"})
        p=self.f.store.report_path("summary"); result=self.f.status().as_json()
        identity=self.f.store.snapshot(p,result)
        self.assertEqual(identity,h(p.read_bytes()))
        self.assertEqual(json.loads(p.read_bytes())["report"],result)
        with self.assertRaises(EvidenceError): self.f.store.snapshot(self.f.store.path,result)

    def test_inventory_unhashed_prepared_and_unclassified_rows_remain_unbound(self):
        p=self.root / "inventory-with-unbound.json"
        v=dict(schema="smr-e01-inventory-v1",editions=[dict(archive_sha256=self.f.a["archive_sha256"],
            edition_id=self.f.a["edition_id"],scenarios=[dict(scenario_relative_path="UserMaps/A.xml",scenario_sha256=self.f.a["scenario_sha256"])])])
        for kind in ("prepared","unclassified"):
            v["editions"].append(dict(edition_kind=kind,archive_sha256=h(b"archive"),edition_id=h(kind.encode()),scenario_relative_paths=["UserMaps/A.xml"]))
        p.write_bytes(canonical(v));inv=bind_inventory(p,h(p.read_bytes()))
        self.assertEqual(len(inv.payload["scenarios"]),1)
        self.assertEqual(len(inv.payload["unbound_editions"]),2)
        self.f.store.append(inv)
        b=build_record(inv,h(b"archive"),h(b"prepared"),h(b"prepared bytes"),self.f.resources,h(b"engine"),{"ai":1})
        with self.assertRaises(EvidenceError): self.f.store.append(b)

    def test_accepted_inventory_exact_hash_and_compiler_only_edition_isolation(self):
        p=self.root / "inventory.json"
        v=dict(schema="smr-e01-inventory-v1",editions=[dict(archive_sha256=self.f.a["archive_sha256"],
            edition_id=self.f.a["edition_id"],scenarios=[dict(scenario_relative_path="UserMaps/A.xml",scenario_sha256=self.f.a["scenario_sha256"])])])
        p.write_bytes(canonical(v)); identity=h(p.read_bytes())
        inv=bind_inventory(p,identity);self.assertEqual(inv.payload["sha256"],identity)
        p.write_bytes(canonical(dict(v,changed=True)))
        with self.assertRaises(EvidenceError): bind_inventory(p,identity)
        c=self.f.contract();obs=self.f.record(c)
        self.assertEqual(self.f.observation(c).id,obs.id)
        self.assertEqual(set(dict(self.f.status([self.f.sibling]).scenarios[0].actions).values()),{"unknown"})


class E02CorrectionTests(unittest.TestCase):
    """Regressions for accepted review R1-R5, with a pre-observation toy oracle."""
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name).resolve();self.f=Fixture(self.root)

    def complete(self):
        c=self.f.contract()
        for action in EXPECTED: self.f.record(c,action)
        self.assertEqual(self.f.status([self.f.a]).status,"Verified")
        return c

    def test_r1_matching_unknown_empty_plain_options_never_pass(self):
        for index, options in enumerate((None, {}, {"coverage":"unknown"}, {"ai":1}, {"ai":None})):
            root=self.root / str(index);root.mkdir();f=Fixture(root)
            f.build=build_record(f.inventory,f.a["archive_sha256"],f.a["edition_id"],
                h(b"prepared bytes"),f.resources,h(b"engine"),options)
            f.store.append(f.build);f.context=f.build.payload["context"]
            c=f.contract()
            for action in EXPECTED: f.record(c,action)
            status=f.status([f.a])
            self.assertEqual(status.status,"Not verified")
            self.assertEqual(set(dict(status.scenarios[0].actions).values()),{"unknown"})
            self.assertIsNone(context_identity(f.context))

    def test_r1_attestation_requires_nonempty_complete_resolved_contract(self):
        for values,fields in (({},[]),({"ai":1},["ai","difficulty"]),({"ai":"unknown"},["ai"]),
                              ({"ai":{}},["ai"]),({"ai":{"players":None}},["ai"])):
            with self.assertRaises(EvidenceError):
                attest_options(values,required_fields=fields,contract_version="toy-v1",basis="Independent control")
        known=known_options({"ai":{"players":1,"enabled":True,"scale":1.0}})
        self.assertEqual(known["state"],"known")

    def test_r4_nested_bool_int_float_contexts_are_distinct(self):
        self.f.build=build_record(self.f.inventory,self.f.a["archive_sha256"],self.f.a["edition_id"],
            h(b"prepared bytes"),self.f.resources,h(b"engine"),known_options({"ai":{"players":[1]}}))
        self.f.store.append(self.f.build);self.f.context=self.f.build.payload["context"]
        self.complete()
        for alternate in (True,1.0):
            context=dict(self.f.context,options=known_options({"ai":{"players":[alternate]}}))
            self.assertNotEqual(context_identity(context),context_identity(self.f.context))
            status=self.f.status([self.f.a],context)
            self.assertEqual(status.status,"Not verified")
            self.assertEqual(set(dict(status.scenarios[0].actions).values()),{"unknown"})
        # Also keep float-to-bool and nested zero/False distinct.
        for left,right in ((True,1.0),(0,False),(0.0,False)):
            a=dict(self.f.context,options=known_options({"ai":{"players":[left]}}))
            b=dict(self.f.context,options=known_options({"ai":{"players":[right]}}))
            self.assertNotEqual(context_identity(a),context_identity(b))

    def test_r3_omitted_provider_rejected_empty_existing_roots_valid(self):
        self.complete()
        self.assertEqual([r["role"] for r in self.f.resources["roots"]],["custom_assets","installed_assets","user_maps"])
        for subset in ({"installed_assets":self.f.roots["installed_assets"]},
                       {k:v for k,v in self.f.roots.items() if k!="user_maps"}):
            resources=fingerprint_resources(subset)
            with self.assertRaises(EvidenceError):
                build_record(self.f.inventory,self.f.a["archive_sha256"],self.f.a["edition_id"],
                             h(b"prepared bytes"),resources,h(b"engine"),known_options())
            context=dict(self.f.context,resources_identity=resources["identity"],provider_roles=sorted(subset))
            self.assertEqual(self.f.status([self.f.a],context).status,"Not verified")
        before=self.f.resources["identity"]
        (self.f.roots["custom_assets"] / "new-provider.txt").write_bytes(b"New independently supplied provider")
        after=fingerprint_resources(self.f.roots)["identity"]
        self.assertNotEqual(before,after)
        self.assertEqual(self.f.status([self.f.a],dict(self.f.context,resources_identity=after)).status,"Not verified")

    def test_r2_standalone_pending_outcomes_block_earlier_pass_after_restart(self):
        c=self.complete();failure=None
        for index,outcome in enumerate(("unknown","partial","pass","failure")):
            raw=self.f.observation(c,"load",outcome)
            raw=EvidenceRecord.create("observation",dict(raw.payload,observed_at=f"2026-10-07T14:0{index+1}:00+00:00"),raw.value["refs"])
            self.f.store.append(raw)
            restarted=EvidenceStore(self.f.store.path)
            status=reduce_verification(restarted.records(),[self.f.a],self.f.context)
            self.assertNotEqual(status.status,"Verified")
            self.assertEqual(dict(status.scenarios[0].actions)["load"],"failure" if outcome=="failure" else "unknown")
            if outcome=="failure": failure=raw
            else:
                # Explicit review resolves the pending observation before next case.
                restarted.append(self.f.interpretation(raw,"pass" if outcome=="pass" else "unknown",f"resolved-{index}"))
        self.assertEqual(self.f.status([self.f.a]).status,"Known issue")
        self.f.store.append(self.f.interpretation(failure,"unknown","explicit-withdrawal"))
        self.assertIn(failure,self.f.store.records())
        self.assertEqual(self.f.status([self.f.a]).status,"Not verified")
        # A later independently reviewed successful run can resolve coverage.
        new=self.f.observation(c,"load")
        new=EvidenceRecord.create("observation",dict(new.payload,observed_at="2026-10-07T15:00:00+00:00"),new.value["refs"])
        self.f.store.append(new);self.f.store.append(self.f.interpretation(new))
        self.assertEqual(self.f.status([self.f.a]).status,"Verified")

    def test_r2_public_pair_second_validation_failure_keeps_raw_fact(self):
        from smr_launcher.application import LauncherApplication
        c=self.complete();failed=self.f.observation(c,"load","failure")
        app=LauncherApplication.__new__(LauncherApplication);app.scenario_evidence_store=self.f.store
        wrong=self.f.interpretation(self.f.observation(c,"camera"),"failure")
        before=self.f.store.path.read_bytes()
        with self.assertRaises(EvidenceError): app.record_scenario_action(failed,wrong)
        self.assertTrue(self.f.store.path.read_bytes().startswith(before))
        self.assertIn(failed,EvidenceStore(self.f.store.path).records())
        self.assertEqual(self.f.status([self.f.a]).status,"Known issue")

    def test_r2_second_append_interrupt_and_corrupt_tail_are_conservative(self):
        from smr_launcher.application import LauncherApplication
        c=self.complete();failed=self.f.observation(c,"load","failure")
        app=LauncherApplication.__new__(LauncherApplication);app.scenario_evidence_store=self.f.store
        original=self.f.store.append
        def interrupted(record):
            if record.kind=="interpretation": raise OSError("Independent interruption after raw commit")
            return original(record)
        with patch.object(self.f.store,"append",side_effect=interrupted):
            with self.assertRaises(OSError): app.record_scenario_action(failed,self.f.interpretation(failed,"failure"))
        restarted=EvidenceStore(self.f.store.path)
        self.assertIn(failed,restarted.records())
        self.assertEqual(reduce_verification(restarted.records(),[self.f.a],self.f.context).status,"Known issue")
        write=os.write
        def short(fd,data): return write(fd,data[:-20])
        with patch("smr_launcher.scenario_evidence.os.write",side_effect=short):
            with self.assertRaises(EvidenceError): restarted.append(self.f.interpretation(failed,"failure"))
        corrupt=self.f.store.path.read_bytes()
        with self.assertRaises(EvidenceError): EvidenceStore(self.f.store.path).records()
        with self.assertRaises(EvidenceError): restarted.append(self.f.interpretation(failed,"failure"))
        self.assertEqual(corrupt,self.f.store.path.read_bytes())

    def test_r5_snapshot_cannot_overwrite_receipt_ledger_or_unowned_artifact(self):
        c=self.complete();report=self.f.status([self.f.a]).as_json()
        foreign=EvidenceStore(self.f.store.report_path("foreign-ledger"));foreign.append(self.f.inventory)
        mimicked=self.f.store.report_path("mimicked")
        mimicked.write_bytes(canonical({"schema":SCHEMA,"report":"mimicked opaque artifact"})+b"\n")
        for target in (self.root / "receipt.txt",foreign.path,mimicked):
            before=target.read_bytes();ledger=self.f.store.path.read_bytes()
            with self.assertRaises(EvidenceError): self.f.store.snapshot(target,report)
            self.assertEqual(before,target.read_bytes());self.assertEqual(ledger,self.f.store.path.read_bytes())
        self.assertEqual(self.f.status([self.f.a]).status,"Verified")
        owned=self.f.store.report_path("summary")
        first=self.f.store.snapshot(owned,report)
        self.assertEqual(first,h(owned.read_bytes()))
        second=self.f.store.snapshot(owned,{"status":"Not verified","basis":"Independent updated report"})
        self.assertNotEqual(first,second)
        changed=owned.read_bytes()+b" "
        owned.write_bytes(changed)
        with self.assertRaises(EvidenceError): self.f.store.snapshot(owned,report)
        self.assertEqual(changed,owned.read_bytes())
        forged=EvidenceRecord.create("report_target",dict(path=owned.name,ledger_name=self.f.store.path.name))
        with self.assertRaises(EvidenceError): self.f.store.append(forged)

    def test_r5_interrupted_publication_is_not_adopted_as_owned_report(self):
        target=self.f.store.report_path("interrupted")
        original=self.f.store._append_locked
        def interrupt(record,existing):
            if record.kind=="report_publication": raise OSError("Independent interruption before publication receipt")
            return original(record,existing)
        with patch.object(self.f.store,"_append_locked",side_effect=interrupt):
            with self.assertRaises(OSError): self.f.store.snapshot(target,{"status":"Not verified"})
        before=target.read_bytes();ledger=self.f.store.path.read_bytes()
        with self.assertRaises(EvidenceError): self.f.store.snapshot(target,{"status":"Verified"})
        self.assertEqual(before,target.read_bytes());self.assertEqual(ledger,self.f.store.path.read_bytes())

    def test_v1_records_remain_readable_and_cannot_gain_current_pass(self):
        # Original wire envelopes stay intact; there is no rewrite migration.
        def old(kind,payload,refs=()):
            body=dict(schema=LEGACY_SCHEMA,kind=kind,payload=payload,refs=list(refs))
            return EvidenceRecord(canonical(dict(body,id=digest(body))))
        store=EvidenceStore(self.root / "old.jsonl")
        inv=old("inventory",self.f.inventory.payload);store.append(inv)
        subset=fingerprint_resources({"installed_assets":self.f.roots["installed_assets"]})
        resource={k:subset[k] for k in ("schema","algorithm","identity")}
        resource["roots"]=[{"role":r["role"],"entries":r["entries"]} for r in subset["roots"]]
        context=dict(game_sha256=h(b"engine"),prepared_output_sha256=h(b"prepared bytes"),resources_identity=subset["identity"],options={"coverage":"unknown"})
        build=old("build",dict(inventory=inv.id,archive_sha256=self.f.a["archive_sha256"],edition_id=self.f.a["edition_id"],
            prepared_output_sha256=h(b"prepared bytes"),context=context,resource_receipt=resource),(inv.id,))
        store.append(build);before=store.path.read_bytes()
        template=self.f.contract();cp=dict(template.payload,build=build.id,version=LEGACY_SCHEMA)
        contract=old("contract",cp,(build.id,));store.append(contract)
        for action in EXPECTED:
            op=self.f.observation(template,action).payload
            observation=old("observation",dict(op,build=build.id,contract=contract.id,actual_context=context),(build.id,contract.id))
            store.append(observation)
            interpreted=old("interpretation",dict(observation=observation.id,analysis_version="old-v1",contract_version=LEGACY_SCHEMA,outcome="pass",reason="Original toy result"),(observation.id,))
            store.append(interpreted)
        data=store.path.read_bytes();self.assertTrue(data.startswith(before))
        result=reduce_verification(store.records(),[self.f.a],context)
        self.assertEqual(result.status,"Not verified")
        self.assertEqual(set(dict(result.scenarios[0].actions).values()),{"unknown"})
        self.assertEqual(data,store.path.read_bytes())
        self.assertEqual(len(result.limited),19)
        first=next(r for r in store.records() if r.kind=="observation" and r.payload["action"]=="load")
        failure=old("interpretation",dict(observation=first.id,analysis_version="old-failure-v2",contract_version=LEGACY_SCHEMA,
                                         outcome="failure",reason="Retained original toy crash"),(first.id,))
        store.append(failure)
        result=reduce_verification(store.records(),[self.f.a],context)
        self.assertEqual(result.status,"Known issue")
        self.assertEqual(set(dict(result.scenarios[0].actions).values()),{"unknown"})
        self.assertIn("Historical scenario issue",result.issue)
        from smr_launcher.gui import LauncherWindow
        details=LauncherWindow._verification_details(result)
        self.assertIn("Earlier scenario observation",details)
        self.assertIn("review failure",details)
        sibling=reduce_verification(store.records(),[self.f.b],context)
        self.assertEqual(sibling.limited,())
        self.assertEqual(sibling.status,"Not verified")
