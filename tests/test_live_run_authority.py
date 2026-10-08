"""Actual synthetic runner leaves and fixed negative authority expectations.

Process/GUI providers are explicit trusted fixtures. No engine evidence, actual
Mac process probes or game operations are performed by these tests.
"""
from contextlib import ExitStack
import json
import hashlib
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import test_batch_smoke as fixtures
from smr_launcher import live_run_authority as live


class LiveRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DurabilityIntegrationTests("test_raw_selected_inputs_and_watchdog_cause_are_durable_before_stop_and_restore")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setup_application()
        self.batch = fixtures.batch
        self.app = self.fixture.app
        self.stock = self.fixture.job
        self.targets = [dict(self.stock, input_identity=key * 64, name=name)
                        for key, name in (("c", "A"), ("d", "B"))]
        self.output = self.fixture.root / "workflow"
        self.calls = []
        self.owner = None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(self.fixture.fixtures(dict(status="loaded", cause="fixture_load")))
        def constructor(executable):
            owner = live._observing.get()
            self.assertIs(owner, self.owner)
            self.assertTrue(owner.lock._is_owned())
            acquired = []
            def contender():
                ok = owner.lock.acquire(blocking=False)
                acquired.append(ok)
                if ok:
                    owner.lock.release()
            thread = threading.Thread(target=contender)
            thread.start(); thread.join(timeout=1)
            self.assertEqual(acquired, [False])
            self.calls.append(executable)
            return self.fixture.processes
        self.stack.enter_context(patch.object(self.batch, "MacProcesses", side_effect=constructor))
        # The original fixture locates raw facts under its current output. Point
        # it at each real per-case namespace without replacing the runner.
        def stopped(process):
            self.assertTrue(process.same(self.fixture.processes.current))
            raws = list(self.output.rglob("raw_outcome.json"))
            self.assertTrue(raws)
            self.fixture.processes.current = None
        self.fixture.processes.stop = stopped
        self.stack.enter_context(patch.object(self.batch, "stop_driver", return_value=None))

    def install(self, **kwargs):
        self.owner = self.batch._trusted_install(self.app, self.stock, self.targets,
            [os.sys.executable], self.output, 90, 6 * 1024**3,
            execution_mode="synthetic", **kwargs)
        self.addCleanup(self.owner._supervisor.close)
        self.addCleanup(self.owner.close)
        return self.owner

    def invoke(self, **kwargs):
        return self.batch.main(["--calibrate-and-batch", "--run", "--allow-ui-control"], **kwargs)

    def assert_denied(self, token=None):
        for purpose in live.CONSUMERS:
            self.assertEqual(self.batch.authority_decision(token, purpose=purpose), live.INSPECTION)

    def test_installed_stock_A_B_actual_io_and_lock(self):
        self.install()
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(len(self.calls), 3)
        issued = tuple(self.owner._registry.values())
        self.assertEqual([v.role for v in issued], ["calibration", "batch", "batch"])
        self.assertTrue(self.owner.decide(issued[0].token).calibration)
        self.assertFalse(self.owner.decide(issued[1].token).calibration)
        for v in issued:
            self.assertTrue(self.owner.decide(v.token).completion)
            names = [name for name, result in v.events]
            for name in ("file.write_count", "file.flush", "file.fsync", "file.close",
                         "directory.fsync", "directory.close", "activation.lock_close",
                         "application.lock_close", "runner.outer_return"):
                self.assertIn(name, names)
            for purpose in live.CONSUMERS:
                self.assertEqual(self.batch.authority_decision(v.token, purpose=purpose),
                                 self.owner.decide(v.token))
        before = len(self.calls)
        with self.assertRaises(self.batch.SafetyError): self.invoke()
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.batch.manifest(self.app.profiles.live), self.fixture.original)

    def test_fake_owner_and_dictionary_default_trust_deny_before_lookup(self):
        self.install()
        class Fake:
            calls = 0
            def decide(self, *args, **kwargs):
                self.calls += 1
                return {"calibration": True, "authenticated": True}
        fake = Fake()
        for key in ("owner", "facade", "entrypoint", "provenance", "trusted"):
            with self.assertRaises(self.batch.SafetyError): self.invoke(**{key: fake})
        with self.assertRaises(self.batch.SafetyError):
            self.invoke(calibration={"calibration": True, "authenticated": True})
        self.assertEqual(fake.calls, 0)
        self.assertEqual(self.calls, [])
        self.assert_denied({"calibration": True})

    def test_file_only_admission_zero_discovery_and_constructor(self):
        for argv in (["--batch"], ["--retry-authority", "unread-file"],
                     ["--recover-session", str(self.output), "--run"],
                     ["--run", "--allow-ui-control", "--driver", "unread-command", "--output", str(self.output)]):
            with patch.object(self.batch.LauncherApplication, "discover", side_effect=AssertionError("discovery")) as discover:
                with self.assertRaises(SystemExit): self.batch.main(argv)
                self.assertEqual(discover.call_count, 0)
        self.assertEqual(self.calls, [])

    def test_second_installation_and_foreign_genuine_owner(self):
        self.install()
        with self.assertRaises(self.batch.SafetyError): self.install()
        foreign = live._new_owner(self.batch._source_closure(), [self.stock],
            live.FiniteSupervisor(time.monotonic() + 500), lambda: True)
        self.addCleanup(foreign._supervisor.close)
        with self.assertRaises(self.batch.SafetyError): self.invoke(owner=foreign)
        with self.assertRaises(self.batch.SafetyError): self.invoke(calibration=object())
        self.assertEqual(self.calls, [])

    def test_original_index_cleanup_and_compound_checkpoint_failures(self):
        # Each fixed fault receives a fresh module/root; its literal outcome is
        # deny-all and zero target constructors, including lost error writes.
        for mode in ("index", "cleanup", "rollback_oserror", "rollback_interrupt", "report", "outer"):
            with self.subTest(mode=mode):
                if self.owner is not None:
                    self.owner.close(); self.owner._supervisor.close()
                    self.stack.close()
                    self.setUp()
                sequence = []
                real_sync = self.batch._fsync_dir
                real_replace = os.replace
                real_write = self.batch.journal_api.write_once
                def sync(path):
                    checkpoint = Path(path) / "recovery.json"
                    if mode == "index" and (Path(path) / "results.jsonl").exists():
                        sequence.append("index_parent_sync")
                        raise OSError("original index failure")
                    if mode.startswith("rollback") and checkpoint.exists() and json.loads(checkpoint.read_bytes()).get("terminal_state") == "finished":
                        sequence.append("final_checkpoint_parent_sync")
                        raise OSError("original final checkpoint parent sync")
                    return real_sync(path)
                def replace(source, destination, *args, **kwargs):
                    if mode.startswith("rollback") and Path(source).name == "checkpoint-rollback.json":
                        sequence.append("rollback_replace")
                        if mode == "rollback_interrupt": raise KeyboardInterrupt("original rollback interrupted")
                        raise OSError("original rollback unavailable")
                    return real_replace(source, destination, *args, **kwargs)
                def write(path, value):
                    if Path(path).stem in ("harness_failure", "restoration_failure", "cleanup_failure"):
                        raise OSError("error recording unavailable")
                    return real_write(path, value)
                self.stack.enter_context(patch.object(self.batch, "_fsync_dir", side_effect=sync))
                self.stack.enter_context(patch.object(os, "replace", side_effect=replace))
                self.stack.enter_context(patch.object(self.batch.journal_api, "write_once", side_effect=write))
                if mode == "cleanup":
                    self.stack.enter_context(patch.object(self.batch, "cleanup_diagnostic", side_effect=OSError("original cleanup failure")))
                if mode == "report":
                    self.stack.enter_context(patch.object(self.batch, "print", side_effect=OSError("report print lost"), create=True))
                if mode == "outer":
                    real_outer = self.batch._run_batch_operations
                    def outer(*args, **kwargs):
                        real_outer(*args, **kwargs)
                        raise KeyboardInterrupt("outer complete return lost")
                    self.stack.enter_context(patch.object(self.batch, "_run_batch_operations", side_effect=outer))
                self.install()
                with self.assertRaises((OSError, KeyboardInterrupt, self.batch.JournalError, self.batch.SafetyError)):
                    self.invoke()
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(len(self.owner._registry), 0)
                self.assert_denied()
                raw_files = list(self.output.rglob("raw_outcome.json"))
                self.assertEqual(len(raw_files), 1)
                raw = raw_files[0].read_bytes()
                self.assertFalse(self.batch.retry_authorized({}, self.stock, {"authenticated": True}, execution_mode="synthetic"))
                if mode.startswith("rollback"):
                    self.assertEqual(sequence, ["final_checkpoint_parent_sync", "rollback_replace"])
                    checkpoint = json.loads((raw_files[0].parent.parent / "recovery.json").read_bytes())
                    self.assertEqual(checkpoint["terminal_state"], "finished")
                    self.assertTrue((raw_files[0].parent / "terminal_finished.json").is_file())
                evidence = os.environ.get("SMR_EVIDENCE_DIRECTORY")
                if evidence:
                    retained = Path(evidence) / "synthetic-fault-snapshots"
                    retained.mkdir(exist_ok=True)
                    files = {str(p.relative_to(self.output)): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                             "bytes_hex": p.read_bytes().hex()} for p in self.output.rglob("*.json")}
                    (retained / (mode + ".json")).write_text(json.dumps(dict(mode=mode,
                        sequence=sequence, constructors=len(self.calls), next_job_constructors=0,
                        owner_errors=self.owner._errors, files=files), indent=2) + "\n")
                with self.assertRaises((self.batch.SafetyError, self.batch.JournalError)):
                    self.batch.recover_session(self.app, raw_files[0].parent.parent)
                self.assertEqual(raw_files[0].read_bytes(), raw)

    def test_actual_file_shortwrite_and_close_failure(self):
        for fault in ("shortwrite", "close"):
            with self.subTest(fault=fault):
                self.install()
                real_fdopen = os.fdopen
                class Stream:
                    def __init__(self, inner): self.inner = inner
                    def write(self, data):
                        if fault == "shortwrite": return self.inner.write(data[:-1])
                        return self.inner.write(data)
                    def flush(self): return self.inner.flush()
                    def fileno(self): return self.inner.fileno()
                    def close(self):
                        self.inner.close()
                        if fault == "close": raise OSError("actual descriptor close return lost")
                with patch.object(os, "fdopen", side_effect=lambda *a, **k: Stream(real_fdopen(*a, **k))):
                    with self.assertRaises((live.AuthorityError, self.batch.JournalError, OSError)):
                        self.invoke()
                self.assertEqual(len(self.calls), 1)
                self.assert_denied()
                self.owner.close(); self.owner._supervisor.close(); self.stack.close()
                self.setUp()

    def test_bad_external_settings_and_pending_removal_are_zero_job_guards(self):
        preferences = self.app.installation.support_root / "Preferences Data"
        before = preferences.read_bytes()
        preferences.unlink(); preferences.symlink_to(self.app.profiles.live / "Settings.ini")
        with self.assertRaises(self.batch.SafetyError): self.install()
        self.assertEqual(self.calls, [])
        preferences.unlink(); preferences.write_bytes(before)
        removal = self.app.library / "removal.json"
        removal.write_bytes(b'{"pending":"unrelated"}')
        with self.assertRaises(self.batch.SafetyError): self.install()
        self.assertEqual(self.calls, [])
        self.assertEqual(removal.read_bytes(), b'{"pending":"unrelated"}')

    def test_both_product_file_roles_refuse_directories_and_links_before_constructors(self):
        for path in self.batch.external_settings_locations(self.app):
            content=path.read_bytes();path.unlink();path.mkdir()
            retained=path/'retained';retained.write_bytes(content)
            with self.subTest(role=path.name,kind='directory'):
                with self.assertRaises(self.batch.SafetyError):self.install()
                self.assertEqual(self.calls,[])
                self.assertIsNone(self.batch._installed_owner)
                self.assertEqual(retained.read_bytes(),content)
            retained.unlink();path.rmdir();path.symlink_to(self.app.profiles.live/'Settings.ini')
            with self.subTest(role=path.name,kind='symlink'):
                with self.assertRaises(self.batch.SafetyError):self.install()
                self.assertEqual(self.calls,[])
                self.assertIsNone(self.batch._installed_owner)
                self.assertTrue(path.is_symlink())
            path.unlink();path.write_bytes(content)
        self.assertEqual(self.batch.manifest(self.app.profiles.live),self.fixture.original)

    def test_absent_product_settings_retain_installed_three_job_positive(self):
        for path in self.batch.external_settings_locations(self.app):path.unlink()
        self.install()
        self.assertEqual(self.invoke(),0)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(len(self.owner._registry),3)
        for issued in self.owner._registry.values():self.assertTrue(self.owner.decide(issued.token).completion)
        self.assertTrue(all(not p.exists() for p in self.batch.external_settings_locations(self.app)))
        self.assertEqual(self.batch.manifest(self.app.profiles.live),self.fixture.original)

    def test_input_mutated_after_genuine_outer_preflight_denies_before_constructor(self):
        pin=self.fixture.root/'frozen-input';pin.write_bytes(b'independent frozen input')
        stopped=self.batch._known_stopped;sequence=[]
        def mutate_after_return(*args,**kwargs):
            result=stopped(*args,**kwargs)
            if self.owner is not None and live._observing.get() is self.owner and not sequence:
                pin.write_bytes(b'changed between admission and constructor')
                sequence.append('actual_outer_preflight_return_then_frozen_input_mutation')
            return result
        with patch.object(self.batch,'_known_stopped',side_effect=mutate_after_return):
            self.install(freeze_paths=[pin])
            with self.assertRaisesRegex(live.AuthorityError,'freeze changed'):self.invoke()
        self.assertEqual(sequence,['actual_outer_preflight_return_then_frozen_input_mutation'])
        self.assertEqual(self.calls,[])
        self.assertFalse(self.owner._registry)
        self.assert_denied()

    def test_guard_return_mutation_is_fenced_inside_constructor_admission(self):
        pin=self.fixture.root/'guard-input';pin.write_bytes(b'frozen')
        self.install(freeze_paths=[pin])
        stopped=self.batch._known_stopped;sequence=[]
        def mutate_on_guard_return(*args,**kwargs):
            result=stopped(*args,**kwargs)
            if live._observing.get() is self.owner:
                sequence.append('stopped_return')
                # First return is the actual outer preflight, second is the
                # installed owner's guard at the constructor admission.
                if len(sequence)==2:pin.write_bytes(b'changed in admission guard')
            return result
        with patch.object(self.batch,'_known_stopped',side_effect=mutate_on_guard_return):
            with self.assertRaisesRegex(live.AuthorityError,'changed during admission'):self.invoke()
        self.assertEqual(sequence,['stopped_return','stopped_return'])
        self.assertEqual(self.calls,[])
        self.assert_denied()

    def test_deadline_context_code_and_revocation_denials(self):
        self.install()
        self.owner._supervisor.expired.set()
        with self.assertRaises(live.AuthorityError): self.invoke()
        self.assertEqual(self.calls, [])
        self.owner._revoked = False
        self.assert_denied()

    def test_settings_exact_restore_conflict_and_absence_retention(self):
        path = self.app.installation.support_root / "Preferences Data"
        preimage = live.SettingsPreimage.capture(path)
        path.write_bytes(b"observed game settings change")
        current = live.SettingsPreimage.capture(path)
        preimage.restore(lambda: True, current)
        self.assertEqual(live.SettingsPreimage.capture(path), preimage)
        path.write_bytes(b"outside conflict")
        with self.assertRaises(live.AuthorityError): preimage.restore(lambda: True, current)
        self.assertEqual(path.read_bytes(), b"outside conflict")
        absent_path = path.parent / "absent-setting"
        absent = live.SettingsPreimage.capture(absent_path)
        absent_path.write_bytes(b"new state retained")
        with self.assertRaises(live.AuthorityError): absent.restore(lambda: True, live.SettingsPreimage.capture(absent_path))
        self.assertEqual(absent_path.read_bytes(), b"new state retained")

    def test_runtime_install_arguments_cannot_supply_exact_script_root_or_stock_namespace(self):
        with self.assertRaisesRegex(self.batch.SafetyError, "Runtime"):
            self.batch._trusted_install(self.app, self.stock, self.targets, ["unrun"], self.output,
                                        90, 6 * 1024**3, execution_mode="runtime")
        self.assertEqual(self.calls, [])

    def test_same_bytes_inode_and_mutation_fence_cannot_revive(self):
        self.install()
        self.assertEqual(self.invoke(), 0)
        issued = tuple(self.owner._registry.values())[0]
        token = issued.token
        path = Path(issued.snapshots[0][0])
        data = path.read_bytes(); metadata = path.stat()
        replacement = path.with_name(path.name + ".replacement")
        replacement.write_bytes(data)
        os.utime(replacement, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        replacement.replace(path)
        self.assert_denied(token)
        self.owner._revoked = False
        self.assert_denied(token)
        before = len(self.calls)
        with self.assertRaises((self.batch.SafetyError, live.AuthorityError)): self.invoke()
        self.assertEqual(len(self.calls), before)

    def test_code_context_foreign_case_and_role_bindings(self):
        pin = self.fixture.root / "frozen-provider-source.txt"
        pin.write_bytes(b"original complete fixture provider")
        self.install(freeze_paths=(pin,))
        self.assertEqual(self.invoke(), 0)
        issued = tuple(self.owner._registry.values())
        token = issued[0].token
        self.assertEqual(self.owner.decide(token, case="unknown"), live.INSPECTION)
        changed = dict(self.stock, binding={"unknown": True})
        self.assertEqual(self.owner.decide(token, context=changed), live.INSPECTION)
        self.assertFalse(self.owner.decide(issued[1].token).calibration)
        before = pin.read_bytes()
        pin.write_bytes(b"changed complete fixture provider")
        self.assert_denied(token)
        pin.write_bytes(before)
        self.owner._revoked = False
        self.assert_denied(token)
        before_calls = len(self.calls)
        with self.assertRaises((self.batch.SafetyError, live.AuthorityError)): self.invoke()
        self.assertEqual(len(self.calls), before_calls)

    def test_real_application_context_close_loss_denies_all_purposes(self):
        real_call = live.ObservedIO.call
        def call(adapter, name, function, *args, **kwargs):
            result = real_call(adapter, name, function, *args, **kwargs)
            if name == "application.lock_close":
                raise OSError("closed descriptor; application context return lost")
            return result
        self.stack.enter_context(patch.object(live.ObservedIO, "call", call))
        self.install()
        with self.assertRaises(OSError): self.invoke()
        self.assertEqual(len(self.calls), 1)
        self.assert_denied()

    def test_actual_new_saves_and_external_settings_are_preserved(self):
        original = live.SettingsPreimage.capture(self.app.profiles.live)
        preferences = self.app.installation.support_root / "Preferences Data"
        before = live.SettingsPreimage.capture(preferences)
        def watch(*args, **kwargs):
            (self.app.profiles.live / "Saves/future.sav").write_bytes(b"new future map save")
            preferences.write_bytes(b"observed game preference changes")
            return dict(status="loaded", cause="observed_fixture_load")
        self.stack.enter_context(patch.object(self.batch, "watch", side_effect=watch))
        self.install()
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(live.SettingsPreimage.capture(self.app.profiles.live), original)
        self.assertEqual(live.SettingsPreimage.capture(preferences), before)
        preserved = list(self.output.rglob("preserved-diagnostic-profile/Saves/future.sav"))
        self.assertEqual(len(preserved), 3)
        self.assertTrue(all(p.read_bytes() == b"new future map save" for p in preserved))
        sources = list(self.app.profiles.profiles.glob("map-*/Saves/future.sav"))
        self.assertEqual(len(sources), 3)
        self.assertTrue(all(p.read_bytes() == b"new future map save" for p in sources))

    def test_fully_observed_failed_disposition_only_grants_administrative_restore(self):
        self.stack.enter_context(patch.object(self.batch, "watch", return_value=dict(
            status="automation_failed", cause="fixture_automation_failed")))
        self.install()
        with self.assertRaises(self.batch.SafetyError): self.invoke()
        self.assertEqual(len(self.calls), 1)
        issued = tuple(self.owner._registry.values())
        self.assertEqual(len(issued), 1)
        decision = self.owner.decide(issued[0].token)
        self.assertEqual(decision.classification, "live_failed")
        self.assertFalse(decision.completion)
        self.assertFalse(decision.calibration)
        self.assertFalse(decision.retry)
        self.assertFalse(decision.next_job_allowed)
        self.assertTrue(decision.already_restored)
        for purpose in live.CONSUMERS:
            self.assertEqual(self.batch.authority_decision(issued[0].token, purpose=purpose), decision)


if __name__ == "__main__": unittest.main()


class RuntimeEngineeringTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DurabilityIntegrationTests('test_raw_selected_inputs_and_watchdog_cause_are_durable_before_stop_and_restore')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups);self.fixture.setup_application()
        self.batch=fixtures.batch;self.root=self.fixture.root;self.app=self.fixture.app

    def test_actual_stock_source_has_distinct_identity_and_preserves_original_saves(self):
        before=live.SettingsPreimage.capture(self.app.profiles.live)
        job=self.batch.stock_job_for(self.app,{'fixture':'driver'},self.root/'stock-prepare',90,6*1024**3,'Southwest U.S.',None)
        source=Path(job['prepared_root'])
        self.assertEqual(job['resource_namespace'],'stock-selection-v1')
        self.assertIsNone(job['original_scenario_identity'])
        self.assertIsNone(job['binding']['scenario_relative_path'])
        self.assertEqual(list((source/'UserMaps').iterdir()),[])
        self.assertEqual(list((source/'CustomAssets').iterdir()),[])
        self.assertEqual((source/'Saves/stock.sav').read_bytes(),(self.app.profiles.live/'Saves/stock.sav').read_bytes())
        self.assertEqual(live.SettingsPreimage.capture(self.app.profiles.live),before)
        with self.assertRaises(self.batch.SafetyError):
            self.batch.stock_job_for(self.app,{},self.root/'bad-stock',90,1,'',None)
        self.assertFalse((self.root/'bad-stock').exists())

    def test_runtime_freeze_detects_actual_code_inode_environment_module_and_binding_changes(self):
        code=self.root/'code.py';code.write_bytes(b'original code')
        tree=self.root/'inputs';tree.mkdir();(tree/'input').write_bytes(b'input')
        environment=[('fixture','original')];module=type('Module',(),{'__file__':str(code)})()
        callbacks=[lambda: True]
        freeze=live.RuntimeFreeze([code],[tree],lambda:callbacks,
            modules=lambda:[('fixture',module)],environment=lambda:tuple(environment))
        self.assertTrue(freeze.check())
        environment[0]=('fixture','changed')
        with self.assertRaises(live.AuthorityError):freeze.check()
        environment[0]=('fixture','original');callbacks[0]=lambda:True
        with self.assertRaises(live.AuthorityError):freeze.check()
        freeze=live.RuntimeFreeze([code],[tree],lambda:callbacks,modules=lambda:[('fixture',module)],environment=lambda:tuple(environment))
        replacement=self.root/'replacement';replacement.write_bytes(code.read_bytes());os.replace(replacement,code)
        with self.assertRaises(live.AuthorityError):freeze.check()
        freeze=live.RuntimeFreeze([code],[tree],lambda:callbacks,modules=lambda:[('fixture',module)],environment=lambda:tuple(environment))
        (tree/'new-input').write_bytes(b'new')
        with self.assertRaises(live.AuthorityError):freeze.check()
        with self.assertRaises(live.AuthorityError):live.RuntimeFreeze([code],[self.root/'absent'],callbacks)

    def provider_fixture(self, dependency='/usr/lib/libSystem.B.dylib'):
        from types import SimpleNamespace
        distributions=[]
        for name in ('orca','ocr'):
            root=self.root/name;root.mkdir();exe=root/name;exe.write_bytes(b'\xcf\xfa\xed\xfe'+b'fixture-native');distributions.append((root,exe))
        (orca_root,orca),(ocr_root,ocr)=distributions
        compiler=ocr_root/'compiler';compiler.write_bytes(b'fixture compiler')
        descriptor={'schema':'root-provider-selection-v1',
            'orca':{'distribution':str(orca_root),'executable':str(orca),'external_roots':[]},
            'ocr':{'distribution':str(ocr_root),'executable':str(ocr),'external_roots':[]},
            'toolchain':{'executable':str(compiler),'options':['reviewed fixture option']}}
        status=self.root/'status.json';status.write_text(json.dumps({'ok':True,'result':{'target':{'kind':'local'},
            'app':{'pid':101},'runtime':{'runtimeId':'fixture-runtime','appVersion':'fixture-build'}},'_meta':{'runtimeId':'fixture-runtime'}}))
        process=self.batch.Process(101,202,str(orca),1)
        provider=SimpleNamespace(inspect=lambda pid:process)
        commands=[]
        def observed_metadata(argv,timeout):
            commands.append(tuple(argv))
            if argv[0]==str(orca):return status.read_text()
            if argv[0]=='/usr/bin/otool':return argv[-1]+':\n\t'+dependency+' (compatibility version 1.0.0, current version 1.0.0)\n'
            if argv[0]=='/usr/sbin/lsof':return 'p101\nn'+str(orca)+'\n'
            if argv[0]=='/usr/bin/codesign':return '' if '--verify' in argv else 'fixture signed metadata'
            if argv[0]=='/usr/bin/sw_vers':return 'fixture OS/system build'
            if argv[0]==str(compiler):return 'fixture compiler version'
            raise AssertionError('Unexpected trusted fixture metadata request')
        collector=self.batch.ProviderCollector(descriptor,provider,observed_metadata,
            platform_stat=lambda path:SimpleNamespace(st_dev=77))
        return collector,descriptor,status,commands

    def test_actual_provider_artifact_collector_and_incarnation_fences(self):
        collector,descriptor,status,commands=self.provider_fixture();self.assertIs(collector.collect(),collector)
        self.assertTrue(collector.check())
        self.assertTrue(any('--verify' in c for c in commands))
        self.assertTrue(collector.records['orca']['static_declared_dependencies'])
        self.assertEqual(collector.records['platform']['system_volume_observed'],77)
        freeze=live.RuntimeFreeze(collector.paths,collector.roots,(),modules=lambda:(),environment=lambda:())
        self.assertTrue(freeze.check())
        value=json.loads(status.read_text());value['result']['runtime']['runtimeId']='new';status.write_text(json.dumps(value))
        with self.assertRaises(self.batch.SafetyError):collector.check()
        (Path(descriptor['orca']['distribution'])/'new-helper').write_bytes(b'new helper')
        with self.assertRaises(live.AuthorityError):freeze.check()
        with self.assertRaises(self.batch.SafetyError):self.batch.ProviderCollector({'complete':True},None)

    def test_unknown_nonplatform_provider_dependency_and_unobserved_collection_deny(self):
        external=self.root/'unknown-library';external.write_bytes(b'fixture unknown dependency')
        collector,descriptor,status,commands=self.provider_fixture(str(external))
        with self.assertRaisesRegex(self.batch.SafetyError,'Unresolved non-platform'):collector.collect()
        self.assertIsNone(collector.runtime)
        with self.assertRaisesRegex(self.batch.SafetyError,'never completed'):collector.check()
        with patch.object(self.batch.LauncherApplication,'discover',side_effect=AssertionError('zero discovery')) as discover:
            with self.assertRaises(self.batch.SafetyError):self.batch._script_entry(['--calibrate-and-batch','--run','--allow-ui-control'])
            self.assertEqual(discover.call_count,0)

    def supervisor_fixture(self,reused=False,stop_failure=False):
        from types import SimpleNamespace
        events=[]
        # A fresh actual production module retains one genuine installed owner
        # for each revoked fault case. The raw Journal below is diagnostic only;
        # complete authority is proved separately by the real stock/A/B runner.
        import importlib.util
        candidate=importlib.util.module_from_spec(fixtures.spec)
        exec(fixtures.BATCH_CODE,candidate.__dict__)
        owner=candidate._trusted_install(self.app,self.fixture.job,
            [dict(self.fixture.job,input_identity='e'*64)],['trusted-fixture'],
            self.root/('supervisor-'+str(reused)+'-'+str(stop_failure)),90,1000,
            execution_mode='synthetic')
        self.addCleanup(owner._supervisor.close);self.addCleanup(owner.close)
        actual_invalidate=owner._invalidate
        def invalidate(boundary,error=None):
            actual_invalidate(boundary,error);events.append('revoked')
        owner._invalidate=invalidate
        original=self.batch.Process(77,88,str(self.root/'game'),1)
        current=[self.batch.Process(77,99,original.executable,1) if reused else original]
        def stop(process):
            events.append('game_stop')
            if stop_failure:raise OSError('actual fixture stop failure')
            current[0]=None
        provider=SimpleNamespace(inspect=lambda pid:current[0],games=lambda:[current[0]] if current[0] else [],stop=stop)
        raw=self.root/'supervisor-raw.json'
        class Journal:
            def read(self,name):return {'observed':True} if raw.exists() else None
            def outcome(self,value):live.write_file(raw,live.encoded(value),exclusive=True);events.append('raw_return')
        driver=SimpleNamespace(pid=66,returncode=None)
        def stop_driver(value):events.append('driver_stop');value.returncode=0
        return owner,provider,original,driver,Journal(),stop_driver,events

    def test_independent_supervisor_deadline_reuses_exact_identity_and_loss_denies(self):
        for reused,stop_failure,guard_bad in ((False,False,False),(True,False,False),(False,True,False),(False,False,True)):
            with self.subTest(reused=reused,stop_failure=stop_failure,guard_bad=guard_bad):
                raw=self.root/'supervisor-raw.json'
                if raw.exists():raw.unlink()
                owner,provider,process,driver,journal,stop_driver,events=self.supervisor_fixture(reused,stop_failure)
                with owner.lock:
                    cookie=live._observing.set(owner)
                    try:
                        supervisor=live.IdentitySupervisor(owner,provider,process,driver,journal,
                            time.monotonic()+.05,100,lambda:not guard_bad,stop_driver,interval=.01)
                        self.assertTrue(supervisor.failed.wait(1))
                        with self.assertRaises(live.AuthorityError):supervisor.close()
                    finally:live._observing.reset(cookie)
                self.assertEqual(events[:3],['revoked','raw_return','driver_stop'])
                self.assertEqual('game_stop' in events,not reused)
                self.assertTrue(raw.exists())
                if stop_failure:self.assertTrue(any('game stop' in e for e in supervisor.errors))

    def test_default_application_lock_still_uses_supported_removal_owner(self):
        removal=self.app._locked.__wrapped__.__globals__['removal']
        with patch.object(removal,'resume',wraps=removal.resume) as resume:
            with self.app._locked():self.assertTrue(self.app.library.exists())
            self.assertEqual(resume.call_count,1)

    def test_exact_installed_routing_change_is_a_zero_constructor_negative(self):
        with patch.object(self.batch,'MacProcesses',side_effect=AssertionError('zero constructors')) as constructors:
            owner=self.batch._trusted_install(self.app,self.fixture.job,
                [dict(self.fixture.job,input_identity='d'*64)],['fixture'],self.root/'routing',90,1000,
                execution_mode='synthetic')
            self.addCleanup(owner._supervisor.close);self.addCleanup(owner.close)
            self.batch._installed_entrypoint=object()
            with self.assertRaises(live.AuthorityError):owner._preflight()
            self.assertEqual(constructors.call_count,0)
            owner._revoked=False
            with self.assertRaises(live.AuthorityError):owner._preflight()

    def test_genuine_installed_stock_owner_and_runtime_supervisor_actual_io_positive(self):
        case=LiveRunnerTests('test_installed_stock_A_B_actual_io_and_lock')
        case.setUp();self.addCleanup(case.doCleanups)
        case.stock=case.batch.stock_job_for(case.app,{},case.fixture.root/'real-stock',90,
            6*1024**3,'Southwest U.S.',None)
        owner=case.install()
        pin=case.fixture.root/'runtime-code-pin';pin.write_bytes(b'owned runtime code')
        owner.runtime_freeze=live.RuntimeFreeze([pin],[Path(case.stock['prepared_root'])],(),
            modules=lambda:(),environment=lambda:())
        inspection=case.fixture.processes.inspect
        seen=[]
        def inspect(pid):seen.append(pid);return inspection(pid)
        def watch(*args,**kwargs):
            # The real supervisor must inspect during the locked actual executor.
            time.sleep(.24);return dict(status='loaded',cause='trusted_synthetic_UI')
        with patch.object(case.fixture.processes,'inspect',side_effect=inspect),patch.object(case.batch,'watch',side_effect=watch):
            self.assertEqual(case.invoke(),0)
        self.assertEqual(len(case.calls),3)
        self.assertGreater(len(seen),0)
        self.assertTrue(owner.decide(owner.latest_calibration).calibration)
        stock=next(v for v in owner._registry.values() if v.role=='calibration')
        run=json.loads(stock.row)['run_directory']
        selected=case.batch.RunJournal(Path(run)).read('selected',True)['payload']
        self.assertEqual(selected['observed_engine_inputs']['namespace'],'stock-selection-v1')
        self.assertIsNone(selected['observed_engine_inputs']['original_scenario_identity'])
        self.assertNotIn('scenario_sha256',selected['observed_engine_inputs'])
        self.assertTrue(any(name=='file.write_count' for name,value in stock.events))

    def test_fake_or_outside_context_supervisor_is_denied_before_providers(self):
        with self.assertRaises(live.AuthorityError):
            live.IdentitySupervisor(object(),None,None,None,None,time.monotonic()+1,100,None,None)
        owner=object.__new__(live.InvocationOwner)
        with self.assertRaises(live.AuthorityError):
            live.IdentitySupervisor(owner,None,None,None,None,time.monotonic()+1,100,None,None)

    def test_final_supervisor_close_loss_revokes_before_outer_return_all_purposes(self):
        case=LiveRunnerTests('test_installed_stock_A_B_actual_io_and_lock')
        case.setUp();self.addCleanup(case.doCleanups);owner=case.install()
        close=owner._supervisor.close;issued=[]
        def lost_return():
            issued.extend(v.token for v in owner._registry.values())
            close();raise KeyboardInterrupt('final supervisor close lost after effect')
        with patch.object(owner._supervisor,'close',side_effect=lost_return):
            with self.assertRaises(KeyboardInterrupt):case.invoke()
        self.assertEqual(len(case.calls),3)
        self.assertEqual(len(issued),3)
        self.assertTrue(owner._revoked)
        for token in issued:
            for purpose in live.CONSUMERS:
                self.assertEqual(case.batch.authority_decision(token,purpose=purpose),live.INSPECTION)
        self.assertTrue(list(case.output.rglob('raw_outcome.json')))

    def test_legacy_single_cannot_admit_unused_target_after_failed_or_closed_workflow(self):
        case=LiveRunnerTests('test_installed_stock_A_B_actual_io_and_lock')
        case.setUp();self.addCleanup(case.doCleanups);owner=case.install()
        outcomes=iter((dict(status='loaded',cause='fixture_stock'),dict(status='automation_failed',reason='fixture failure')))
        with patch.object(case.batch,'watch',side_effect=lambda *a,**k:next(outcomes)):
            with self.assertRaises(case.batch.SafetyError):case.invoke()
        self.assertEqual(len(case.calls),2)
        self.assertTrue(owner._queue_stopped)
        self.assertTrue(owner._supervisor._stop.is_set())
        constructor=case.batch.MacProcesses
        before=constructor.call_count
        original=constructor.side_effect
        constructor.side_effect=AssertionError('zero next-job sentinel')
        try:
            with self.assertRaises(case.batch.SafetyError):
                case.batch.run_batch(case.app,[case.targets[1]],[os.sys.executable],case.output,
                    90,6*1024**3,execution_mode='synthetic')
            self.assertEqual(constructor.call_count,before)
            # Ordinary runtime calls do not choose installed runtime authority,
            # even when they hold the otherwise exact application/context.
            owner.execution_mode='runtime'
            with self.assertRaises(case.batch.SafetyError):
                case.batch.run_batch(case.app,[case.targets[1]],[os.sys.executable],case.output,
                    90,6*1024**3,execution_mode='runtime')
            self.assertEqual(constructor.call_count,before)
        finally:
            owner.execution_mode='synthetic';constructor.side_effect=original
        failed=next(v for v in owner._registry.values() if json.loads(v.row)['status']=='automation_failed')
        self.assertTrue(owner.decide(failed.token).already_restored)
        self.assertFalse(owner.decide(failed.token).completion)
        self.assertFalse(owner.decide(failed.token).next_job_allowed)
