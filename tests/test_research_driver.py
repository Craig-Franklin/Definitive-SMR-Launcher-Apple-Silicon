"""Fail-closed scene assertions for the optional foreground research driver."""
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "orca_load_driver", Path(__file__).resolve().parents[1] / "scripts/research/orca_load_driver.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def word(text, x=.75, y=.04):
    return dict(text=text, x=x, y=y, width=.1, height=.02)


class SceneTests(unittest.TestCase):
    def test_hud_requires_currency_and_date_in_gameplay_region(self):
        self.assertTrue(driver.loaded_hud([word("$500,000"), word("January, 1850", .85)]))
        self.assertFalse(driver.loaded_hud([word("$1,850")]))
        self.assertFalse(driver.loaded_hud([word("January, 1850")]))
        self.assertFalse(driver.loaded_hud([word("$500,000", .3, .4), word("January, 1850", .3, .5)]))
        self.assertFalse(driver.loaded_hud([word("Building Up Steam")]))

    def test_positive_title_then_two_hud_frames_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "fixture.png"
            image.write_bytes(b"synthetic screenshot fixture")
            data = dict(snapshot=dict(treeText="", window=dict(id=12)),
                        screenshot=dict(path=str(image), width=800, height=600, scale=1))
            request = dict(pid=123, deadline=time.time()+20, output_directory=temp,
                           expected_title="Example Map")
            subject = driver.Driver(request, "unused", "unused")
            frames = [
                [word("Single Player"), word("Load Game")],
                [word("Example Map", .2, .2), word("Cancel", .77, .86)],
                [word("$500,000"), word("January, 1850", .85)],
                [word("Building Up Steam")],  # must reset positive count
                [word("$500,000"), word("January, 1850", .85)],
                [word("$500,000"), word("January, 1850", .85)],
            ]
            with patch.object(subject, "observe", side_effect=[(data, f) for f in frames]) as obs, \
                 patch.object(subject, "click") as click, patch.object(driver.time, "sleep"):
                result = subject.run()
            self.assertEqual(result["status"], "loaded")
            self.assertEqual(obs.call_count, 6)
            self.assertEqual(click.call_count, 2)
            self.assertEqual(Path(result["screenshot"]).read_bytes(), image.read_bytes())

    def test_wrong_scenario_cannot_be_reported_loaded(self):
        with tempfile.TemporaryDirectory() as temp:
            subject = driver.Driver(dict(pid=123, deadline=time.time()+10,
                                         output_directory=temp, expected_title="Target Map"), "unused", "unused")
            data = dict(snapshot=dict(treeText="", window=dict(id=12)))
            frames = [(data, [word("Single Player"), word("Load Game")]),
                      (data, [word("Wrong Map", .2, .2), word("Cancel", .77, .86)]),
                      (data, [word("$500,000"), word("January, 1850", .85)])]
            with patch.object(subject, "observe", side_effect=frames), \
                 patch.object(subject, "click") as click, patch.object(driver.time, "sleep"):
                with self.assertRaises(StopIteration):
                    subject.run()
            self.assertEqual(click.call_count, 1)


if __name__ == "__main__":
    unittest.main()
