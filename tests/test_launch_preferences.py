"""Synthetic Settings.ini preparation checks for custom-map launch."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher.launch_preferences import LaunchPreferencesError, prepare_settings


SCENARIO = "UserMaps/Example/RRT_Scenario_User_Example.xml"
BASE = ("[User Settings]\n"
        "PlayerName = Craig\n"
        "LastScenarioName = RRT_Scenario_User_Old.xml\n"
        "SkipOpeningMovies = 0\n"
        "Quickstart = 1\n")


def encoded(text, kind):
    if kind == "utf-8":
        return text.encode("utf-8")
    if kind == "utf-8-bom":
        return b"\xef\xbb\xbf" + text.encode("utf-8")
    if kind == "utf-16-le":
        return b"\xff\xfe" + text.encode("utf-16-le")
    if kind == "utf-16-be":
        return b"\xfe\xff" + text.encode("utf-16-be")
    raise AssertionError(kind)


def decoded(data, kind):
    if kind == "utf-8":
        return data.decode("utf-8")
    if kind == "utf-8-bom":
        return data[3:].decode("utf-8")
    if kind == "utf-16-le":
        return data[2:].decode("utf-16-le")
    if kind == "utf-16-be":
        return data[2:].decode("utf-16-be")
    raise AssertionError(kind)


class LaunchPreferencesTests(unittest.TestCase):
    def test_supported_encodings_and_boms_are_preserved(self):
        boms = {"utf-8": b"", "utf-8-bom": b"\xef\xbb\xbf",
                "utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}
        for kind, bom in boms.items():
            with self.subTest(encoding=kind):
                source = encoded(BASE, kind)
                result = prepare_settings(source, (SCENARIO,))
                self.assertTrue(result.startswith(bom))
                text = decoded(result, kind)
                self.assertIn("LastScenarioName = rrt_scenario_user_example.xml", text)
                self.assertIn("SkipOpeningMovies = 1", text)
                self.assertIn("Quickstart = 0", text)
                self.assertIn("PlayerName = Craig", text)

    def test_crlf_and_unrelated_settings_are_preserved(self):
        text = ("; keep this comment\r\n"
                "[User Settings]\r\n"
                "PlayerName = Craig\r\n"
                "LastScenarioName = RRT_Scenario_User_Old.xml\r\n"
                "SkipOpeningMovies = 0\r\n"
                "Quickstart = 1\r\n"
                "[Graphics]\r\n"
                "Quality = High\r\n")

        result = prepare_settings(text.encode("utf-8"), (SCENARIO,)).decode("utf-8")

        self.assertIn("; keep this comment\r\n", result)
        self.assertIn("PlayerName = Craig\r\n", result)
        self.assertIn("[Graphics]\r\nQuality = High\r\n", result)
        self.assertNotIn("\n", result.replace("\r\n", ""))
        self.assertEqual(result.count("\r\n"), text.count("\r\n"))

    def test_preparation_is_idempotent(self):
        source = encoded(BASE.replace("\n", "\r\n"), "utf-16-be")
        once = prepare_settings(source, (SCENARIO,))
        twice = prepare_settings(once, (SCENARIO,))
        self.assertEqual(twice, once)

    def test_existing_scenario_is_retained_for_multi_scenario_package(self):
        scenarios = ("UserMaps/Example/A/RRT_Scenario_User_Alpha.xml",
                     "UserMaps/Example/B/RRT_Scenario_User_Beta.xml")
        text = BASE.replace("RRT_Scenario_User_Old.xml", "RRT_Scenario_User_BETA.XML")

        result = decoded(prepare_settings(text.encode("utf-8"), scenarios), "utf-8")

        self.assertIn("LastScenarioName = rrt_scenario_user_beta.xml", result)

    def test_duplicate_basenames_are_deduplicated_for_basename_selector(self):
        scenarios = ("UserMaps/Example/RRT_Scenario_User_A.xml",
                     "CustomAssets/Overlay/RRT_Scenario_User_A.XML")
        text = BASE.replace("RRT_Scenario_User_Old.xml", "RRT_Scenario_User_A.xml")

        result = decoded(prepare_settings(text.encode("utf-8"), scenarios), "utf-8")

        self.assertIn("LastScenarioName = rrt_scenario_user_a.xml", result)

    def test_duplicate_setting_keys_are_rejected_without_changing_input(self):
        source = (BASE + "Quickstart = 1\n").encode("utf-8")
        before = bytes(source)
        with self.assertRaises(LaunchPreferencesError):
            prepare_settings(source, (SCENARIO,))
        self.assertEqual(source, before)

    def test_invalid_sections_and_encodings_are_rejected(self):
        invalid = (
            b"[Other]\nLastScenarioName=x\n",
            b"[User Settings]\n[User Settings]\n",
            b"\xff\xfe\x00",
        )
        for source in invalid:
            with self.subTest(source=source):
                with self.assertRaises(LaunchPreferencesError):
                    prepare_settings(source, (SCENARIO,))

    def test_empty_and_unsafe_scenario_inputs_are_rejected(self):
        invalid_sets = (
            (),
            ("/UserMaps/RRT_Scenario.xml",),
            ("UserMaps/../../RRT_Scenario.xml",),
            ("Other/RRT_Scenario.xml",),
            ("UserMaps/RRT_Scenario.txt",),
            (r"UserMaps\RRT_Scenario.xml",),
            ("UserMaps/RRT_\nScenario.xml",),
            ("UserMaps/\0RRT_Scenario.xml",),
        )
        for scenarios in invalid_sets:
            with self.subTest(scenarios=scenarios):
                with self.assertRaises(LaunchPreferencesError):
                    prepare_settings(BASE.encode("utf-8"), scenarios)


if __name__ == "__main__":
    unittest.main()
