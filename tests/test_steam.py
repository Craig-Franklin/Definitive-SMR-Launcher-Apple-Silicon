"""Steam binding fixtures use only synthetic files and manifests."""
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.steam import BindingError, SteamInstallation


class SteamDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.steamapps = self.root / "other-drive/steamapps"
        self.steamapps.mkdir(parents=True)
        self.game = self.steamapps / "common/Sid Meier's Railroads/Sid Meiers Railroads.app"
        executable = self.game / "Contents/MacOS/Sid Meiers Railroads"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"synthetic Mac game executable")
        (self.game / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "com.feralinteractive.railroads",
            "CFBundleVersion": "synthetic-version",
        }))
        profile = self.home / "Library/Application Support/Feral Interactive/Sid Meier's Railroads!/VFS/User/AppData/Roaming/My Games/WinDeveloper"
        profile.mkdir(parents=True)

    def manifest(self, beta: str = "", state: str = "4") -> None:
        self.steamapps.joinpath("appmanifest_7600.acf").write_text(
            '"AppState"\n{\n"appid" "7600"\n"StateFlags" "' + state + '"\n"buildid" "synthetic-build"\n'
            + (f'"BetaKey" "{beta}"\n' if beta else "") + '}\n',
            encoding="utf-8",
        )

    def test_custom_library_and_default_branch_without_beta_key(self):
        self.manifest()
        installation = SteamInstallation.discover(self.home, self.steamapps)
        self.assertEqual(installation.steamapps_root, self.steamapps)
        self.assertEqual(installation.home, self.home)
        self.assertEqual(installation.beta_keys, ())
        self.assertEqual(installation.steam_buildid, "synthetic-build")

    def test_legacy_beta_is_rejected(self):
        self.manifest("mac_retail_111")
        with self.assertRaises(BindingError):
            SteamInstallation.discover(self.home, self.steamapps)

    def test_incomplete_and_changing_states_are_rejected(self):
        for state in ("0", "1", "6", "36", "68", "132", "260", "4194308", "-1", "invalid"):
            with self.subTest(state=state):
                self.manifest(state=state)
                with self.assertRaises(BindingError):
                    SteamInstallation.discover(self.home, self.steamapps)

    def test_enrolled_installation_not_ready_after_manifest_changes(self):
        self.manifest()
        installation = SteamInstallation.discover(self.home, self.steamapps)
        installation.check_current()
        self.manifest(state="260")
        with self.assertRaises(BindingError):
            installation.check_current()

    def test_running_flag_can_be_observed_without_approving_a_switch(self):
        self.manifest()
        installation = SteamInstallation.discover(self.home, self.steamapps)
        self.manifest(state="68")
        observed = SteamInstallation.discover(self.home, self.steamapps, allow_running_state=True)
        self.assertEqual(observed, installation)
        with self.assertRaises(BindingError):
            installation.check_current()
        self.manifest(state="260")
        with self.assertRaises(BindingError):
            SteamInstallation.discover(self.home, self.steamapps, allow_running_state=True)


if __name__ == "__main__":
    unittest.main()
