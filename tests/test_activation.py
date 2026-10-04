"""Synthetic filesystem checks; no game, downloaded maps, or saves are used."""
from pathlib import Path
import hashlib
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.activation import ActivationError, FilesystemProfiles, ORIGINAL, RecoveryError, _macos_exchange


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.live = self.root / "live"
        self.store = self.root / "store"
        self.live.mkdir()
        for name in ("CustomAssets", "UserMaps", "Saves"):
            (self.live / name).mkdir()
        (self.live / "Saves/original.sav").write_bytes(b"original save")
        self.running = False
        self.manager = self.make_manager()
        self.manager.enroll_original()

    def make_manager(self, fault=None):
        return FilesystemProfiles(
            self.live,
            self.store,
            ("CustomAssets", "UserMaps"),
            game_running=lambda: self.running,
            fault_hook=fault,
        )

    def prepared(self, name):
        root = self.root / ("prepared-" + name)
        root.mkdir()
        for part in ("CustomAssets", "UserMaps", "Saves"):
            (root / part).mkdir()
        (root / "CustomAssets/shared.xml").write_bytes((name + " asset").encode())
        (root / "UserMaps/map.xml").write_bytes((name + " map").encode())
        (root / "Saves/initial.sav").write_bytes((name + " initial").encode())
        variant_id = hashlib.sha256(name.encode()).hexdigest()
        profile_id = self.manager.register_variant(variant_id, root)
        return root, profile_id

    def test_round_trip_preserves_exact_assets_and_future_saves(self):
        original, a = self.prepared("A")
        _, b = self.prepared("B")
        stored_a = self.store / "profiles" / a / "CustomAssets/shared.xml"
        self.assertNotEqual(original.joinpath("CustomAssets/shared.xml").stat().st_ino, stored_a.stat().st_ino)
        self.manager.switch(a)
        (self.live / "Saves/autosave.sav").write_bytes(b"A future autosave")
        (self.live / "Saves/stray.txt").write_bytes(b"A unrelated future file")
        self.manager.switch(b)
        self.assertEqual((self.live / "CustomAssets/shared.xml").read_bytes(), b"B asset")
        self.assertFalse((self.live / "Saves/autosave.sav").exists())
        (self.live / "Saves/manual.sav").write_bytes(b"B future manual save")
        self.manager.switch(a)
        self.assertEqual((self.live / "CustomAssets/shared.xml").read_bytes(), b"A asset")
        self.assertEqual((self.live / "Saves/autosave.sav").read_bytes(), b"A future autosave")
        self.assertEqual((self.live / "Saves/stray.txt").read_bytes(), b"A unrelated future file")
        self.manager.switch(ORIGINAL)
        self.assertEqual((self.live / "Saves/original.sav").read_bytes(), b"original save")
        self.assertEqual(list((self.live / "CustomAssets").iterdir()), [])
        self.manager.switch(b)
        self.assertEqual((self.live / "Saves/manual.sav").read_bytes(), b"B future manual save")
        self.assertEqual((original / "CustomAssets/shared.xml").read_bytes(), b"A asset")

    def test_recovery_at_each_transaction_boundary(self):
        for stop_at in ("after_journal", "after_exchange", "after_outgoing_saved", "after_state"):
            with self.subTest(stop_at=stop_at):
                with tempfile.TemporaryDirectory() as base:
                    live = Path(base) / "live"
                    store = Path(base) / "store"
                    prepared = Path(base) / "prepared"
                    for tree in (live, prepared):
                        for part in ("CustomAssets", "UserMaps", "Saves"):
                            (tree / part).mkdir(parents=True, exist_ok=True)
                    (live / "Saves/original.sav").write_bytes(b"O")
                    (prepared / "CustomAssets/a.xml").write_bytes(b"A")
                    (prepared / "Saves/a.sav").write_bytes(b"A save")
                    def fault(event):
                        if event == stop_at:
                            raise RuntimeError("simulated interruption")
                    manager = FilesystemProfiles(live, store, ("CustomAssets", "UserMaps"), lambda: False, fault_hook=fault)
                    manager.enroll_original()
                    profile = manager.register_variant(hashlib.sha256(b"A").hexdigest(), prepared)
                    with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                        manager.switch(profile)
                    resumed = FilesystemProfiles(live, store, ("CustomAssets", "UserMaps"), lambda: False)
                    expected = ORIGINAL if stop_at == "after_journal" else profile
                    self.assertEqual(resumed.recover(), expected)
                    self.assertEqual(resumed.recover(), expected)
                    if expected == ORIGINAL:
                        self.assertEqual((live / "Saves/original.sav").read_bytes(), b"O")
                        resumed.switch(profile)
                    self.assertEqual((live / "Saves/a.sav").read_bytes(), b"A save")
                    resumed.switch(ORIGINAL)
                    self.assertEqual((live / "Saves/original.sav").read_bytes(), b"O")
                    self.assertFalse((store / "journal.json").exists())

    def test_running_or_unknown_game_blocks_switch(self):
        _, profile = self.prepared("A")
        for value in (True, None):
            self.running = value
            with self.assertRaises(ActivationError):
                self.manager.switch(profile)
            self.assertFalse((self.store / "journal.json").exists())
            self.assertEqual((self.live / "Saves/original.sav").read_bytes(), b"original save")

    def test_game_started_during_exchange_defers_commit_until_recovery(self):
        _, profile = self.prepared("A")
        def start_game(event):
            if event == "after_exchange":
                self.running = True
        manager = self.make_manager(fault=start_game)
        with self.assertRaises(ActivationError):
            manager.switch(profile)
        self.assertTrue((self.store / "journal.json").exists())
        self.assertEqual((self.live / "CustomAssets/shared.xml").read_bytes(), b"A asset")
        with self.assertRaises(ActivationError):
            self.manager.recover()
        self.running = False
        self.assertEqual(self.manager.recover(), profile)
        self.manager.switch(ORIGINAL)
        self.assertEqual((self.live / "Saves/original.sav").read_bytes(), b"original save")

    def test_coordinated_play_launches_after_committing_target(self):
        _, profile = self.prepared("A")
        observed = []
        def launch():
            observed.append((self.live / "CustomAssets/shared.xml").read_bytes())
            self.running = True
        self.assertEqual(self.manager.play(profile, launch, startup_seconds=1), profile)
        self.assertEqual(observed, [b"A asset"])
        self.running = False
        self.manager.switch(ORIGINAL)
        self.assertEqual((self.live / "Saves/original.sav").read_bytes(), b"original save")

    def test_external_start_at_exchange_keeps_journal_for_recovery(self):
        _, profile = self.prepared("A")
        def race(first, second):
            self.running = True
            _macos_exchange(first, second)
        manager = FilesystemProfiles(self.live, self.store, ("CustomAssets", "UserMaps"), lambda: self.running, exchange=race)
        with self.assertRaises(ActivationError):
            manager.switch(profile)
        self.assertTrue((self.store / "journal.json").exists())
        self.running = False
        self.assertEqual(self.manager.recover(), profile)

    def test_substituted_inactive_profile_link_is_rejected(self):
        _, profile = self.prepared("A")
        slot = self.store / "profiles" / profile
        parked = self.root / "parked"
        slot.rename(parked)
        slot.symlink_to(parked, target_is_directory=True)
        with self.assertRaises(ActivationError):
            self.manager.switch(profile)
        self.assertEqual(self.manager.active_profile(), ORIGINAL)
        self.assertEqual((parked / "CustomAssets/shared.xml").read_bytes(), b"A asset")

    def test_modified_assets_are_preserved_and_switch_is_blocked(self):
        _, profile = self.prepared("A")
        self.manager.switch(profile)
        (self.live / "CustomAssets/shared.xml").write_bytes(b"external edit")
        with self.assertRaises(RecoveryError):
            self.manager.switch(ORIGINAL)
        self.assertEqual((self.live / "CustomAssets/shared.xml").read_bytes(), b"external edit")
        self.assertFalse((self.store / "journal.json").exists())

    def test_linked_prepared_source_is_rejected(self):
        source = self.root / "unsafe"
        for part in ("CustomAssets", "UserMaps"):
            (source / part).mkdir(parents=True, exist_ok=True)
        (source / "CustomAssets/elsewhere").symlink_to(self.live / "Saves")
        with self.assertRaises(ActivationError):
            self.manager.register_variant(hashlib.sha256(b"unsafe").hexdigest(), source)
        self.assertEqual(self.manager.active_profile(), ORIGINAL)

    def test_linked_saves_are_rejected_before_a_switch(self):
        _, profile = self.prepared("A")
        original_saves = self.live / "Saves"
        parked = self.root / "parked-saves"
        original_saves.rename(parked)
        original_saves.symlink_to(parked, target_is_directory=True)
        with self.assertRaises(ActivationError):
            self.manager.switch(profile)
        self.assertFalse((self.store / "journal.json").exists())
        self.assertEqual((parked / "original.sav").read_bytes(), b"original save")

    def test_readonly_prepared_tree_becomes_independent_writable_generation(self):
        source = self.root / "readonly"
        for part in ("CustomAssets", "UserMaps", "Saves"):
            (source / part).mkdir(parents=True, exist_ok=True)
        (source / "CustomAssets/a.xml").write_bytes(b"asset")
        (source / "Saves/old.sav").write_bytes(b"old")
        for part in (source / "CustomAssets", source / "UserMaps", source / "Saves", source):
            part.chmod(0o555)
        profile = self.manager.register_variant(hashlib.sha256(b"readonly").hexdigest(), source)
        stored = self.store / "profiles" / profile
        (stored / "Saves/new.sav").write_bytes(b"new")
        self.assertEqual((stored / "Saves/new.sav").read_bytes(), b"new")
        self.assertEqual(stat.S_IMODE((source / "Saves").stat().st_mode), 0o555)
        self.assertNotEqual((source / "Saves/old.sav").stat().st_ino, (stored / "Saves/old.sav").stat().st_ino)

    def test_another_store_cannot_enroll_or_switch_the_same_live_root(self):
        rival = FilesystemProfiles(self.live, self.root / "rival-store", ("CustomAssets", "UserMaps"), lambda: False)
        with self.assertRaises(RecoveryError):
            rival.enroll_original()
        _, profile = self.prepared("A")
        with self.assertRaises(RecoveryError):
            rival.switch(profile)
        self.assertEqual(self.manager.active_profile(), ORIGINAL)

    def test_case_alias_of_live_root_uses_the_same_binding_and_lock(self):
        alias = self.live.with_name("LiVe")
        if not alias.samefile(self.live):
            self.skipTest("Test volume is case sensitive")
        rival = FilesystemProfiles(alias, self.root / "rival-store", ("CustomAssets", "UserMaps"), lambda: False)
        self.assertEqual(rival.live, self.manager.live)
        self.assertEqual(rival.lock_file, self.manager.lock_file)
        with self.assertRaises(RecoveryError):
            rival.enroll_original()

    def test_failed_enrollment_does_not_claim_live_root(self):
        fresh = self.root / "fresh-live"
        for name in ("CustomAssets", "UserMaps", "Saves"):
            (fresh / name).mkdir(parents=True, exist_ok=True)
        denied = FilesystemProfiles(fresh, self.root / "fresh-store", ("CustomAssets", "UserMaps"), lambda: True)
        with self.assertRaises(ActivationError):
            denied.enroll_original()
        self.assertFalse(denied.binding_file.exists())
        self.assertFalse(denied.state_file.exists())

    def test_unreadable_asset_directory_blocks_switch_before_journal(self):
        _, profile = self.prepared("A")
        asset = self.live / "CustomAssets"
        asset.chmod(0)
        self.addCleanup(lambda: asset.chmod(0o755))
        with self.assertRaises(OSError):
            self.manager.switch(profile)
        self.assertFalse((self.store / "journal.json").exists())


if __name__ == "__main__":
    unittest.main()
