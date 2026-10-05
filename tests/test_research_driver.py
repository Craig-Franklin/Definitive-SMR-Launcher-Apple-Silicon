"""Fail-closed scene assertions for the optional foreground research driver."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "orca_load_driver", Path(__file__).resolve().parents[1] / "scripts/research/orca_load_driver.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def word(text, x=.75, y=.04):
    return dict(text=text, x=x, y=y, width=.1, height=.02)


class SceneTests(unittest.TestCase):
    def test_daily_calendar_hud_requires_bounded_day_and_separate_money(self):
        for text in ("January 1, 1950", "January 1. 1950", "December 31, 1999"):
            self.assertTrue(driver.loaded_hud([word("$500,000"), word(text, .85)]))
            self.assertFalse(driver.loaded_hud([word(text, .85)]))
            self.assertFalse(driver.loaded_hud([word("$500,000"), word(text, .85, .5)]))
        for text in ("January 0, 1950", "January 32, 1950", "January 101, 1950",
                     "January1950 D", "January 1 1950", "January 1, 1950 freight arrived"):
            self.assertFalse(driver.loaded_hud([word("$500,000"), word(text, .85)]), text)

    def test_crash_report_cancel_requires_exact_context_and_safe_control(self):
        data = dict(snapshot=dict(treeText="3 button Cancel\n4 button Send", window=dict(id=12)))
        words = [word("Sid Meier's Railroads! Unexpectedly Quit", .1, .1),
                 word("send an anonymous report", .1, .2),
                 word("Cancel", .69, .9), word("Send", .87, .9)]
        self.assertEqual(driver.Driver._crash_report_cancel(data, words), ("element", "3"))
        pixels = dict(snapshot=dict(treeText="", window=dict(id=12)))
        self.assertEqual(driver.Driver._crash_report_cancel(pixels, words), ("word", words[2]))
        self.assertIsNone(driver.Driver._crash_report_cancel(data, words[1:]))
        self.assertIsNone(driver.Driver._crash_report_cancel(data, [words[0]] + words[2:]))
        for unsafe in (words[:2] + [words[3]], words + [words[2]],
                       words[:2] + [dict(words[2], x=.89), words[3]]):
            with self.assertRaisesRegex(RuntimeError, "Cancel is not unambiguous"):
                driver.Driver._crash_report_cancel(pixels, unsafe)

    def test_crash_report_cancel_reobserves_then_proceeds_and_never_sends(self):
        with tempfile.TemporaryDirectory() as temp:
            subject = driver.Driver(dict(pid=123, deadline=time.time()+60,
                                         output_directory=temp, expected_title="Example Map"),
                                    "unused", "unused")
            report = dict(snapshot=dict(treeText="1 button Cancel\n2 button Send", window=dict(id=12)))
            words = [word("Sid Meier's Railroads! Unexpectedly Quit"), word("anonymous report")]
            play = dict(snapshot=dict(treeText="3 button Play", window=dict(id=13)))
            with patch.object(subject, "observe", side_effect=[(report, words), (play, [])]), \
                 patch.object(subject, "command", return_value={}) as command, \
                 patch.object(driver.time, "sleep"):
                with self.assertRaises(StopIteration):
                    subject.run()
            self.assertEqual(command.call_args_list[0].args,
                             ("click", "--window-id", 12, "--element-index", "1"))
            self.assertEqual(command.call_args_list[1].args,
                             ("click", "--window-id", 13, "--element-index", "3"))
            self.assertEqual(command.call_count, 2)

    def test_repeated_crash_report_cancel_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            subject = driver.Driver(dict(pid=123, deadline=time.time()+60,
                                         output_directory=temp, expected_title="Example Map"),
                                    "unused", "unused")
            report = dict(snapshot=dict(treeText="1 button Cancel\n2 button Send", window=dict(id=12)))
            words = [word("Sid Meier's Railroads! Unexpectedly Quit"), word("anonymous report")]
            with patch.object(subject, "observe", return_value=(report, words)), \
                 patch.object(subject, "command", return_value={}) as command, \
                 patch.object(driver.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "bounded Cancel"):
                    subject.run()
            self.assertEqual(command.call_count, driver.CRASH_REPORT_CANCEL_LIMIT)
            self.assertTrue(all(c.args[-1] == "1" for c in command.call_args_list))

    def test_hover_tooltip_can_obscure_load_game_without_hiding_menu_context(self):
        words = [word(text, .08, y) for text, y in
                 (("Tutorial", .44), ("Single Plaver", .48),
                  ("Load Start a singleplayer game", .52), ("Multiplayer", .56),
                  ("Options", .61), ("Exit", .73))]
        self.assertEqual(driver.Driver._single_player_control(words)["text"], "Single Plaver")
        self.assertIsNone(driver.Driver._single_player_control(words[:-1]))
        self.assertIsNone(driver.Driver._single_player_control(
            [dict(w, x=.6) for w in words]))
        self.assertIsNone(driver.Driver._single_player_control(
            [dict(w, text="Single Maybe") if w["text"] == "Single Plaver" else w for w in words]))

    def test_setup_enlargement_recovers_exact_title_without_accepting_wrong_map(self):
        for recovered, expected in (("Arizona Canyon V2.4 Mac", True), ("Arizona Canyon V2.2", False)):
            with self.subTest(recovered=recovered), tempfile.TemporaryDirectory() as temp:
                subject = driver.Driver(dict(pid=123, deadline=time.time()+30,
                                             output_directory=temp), "private-ocr", "unused")
                subject.stage = "setup"
                subject.expected_title = driver.normalized("Arizona Canyon V2.4 Mac")
                frame = Path(temp) / "frame.png"
                data = dict(snapshot=dict(window=dict(id=12)), screenshot=dict(path=str(frame)))
                original = [word("Arizona Canvon V2.4 Mac", .23, .18), word("Cancel", .77, .86)]
                enlarged = [word(recovered, .23, .18), word("Cancel", .77, .86)]
                with patch.object(subject, "command", return_value=data), \
                     patch.object(driver.subprocess, "run", side_effect=[
                         SimpleNamespace(stdout=json.dumps(original)),
                         SimpleNamespace(stdout=json.dumps(enlarged))]) as recognize:
                    _, words = subject.observe()
                self.assertEqual(recognize.call_count, 2)
                self.assertEqual(recognize.call_args_list[1].args[0],
                                 ["private-ocr", "--setup-3x", str(frame)])
                self.assertEqual(subject._expected_title_visible(words), expected)

    def test_merged_setup_controls_need_exact_cancel_after_enlargement(self):
        for final, accepted in (("Cancel", True), ("Cancel Ok", False), ("CanceI", False)):
            with self.subTest(final=final), tempfile.TemporaryDirectory() as temp:
                subject = driver.Driver(dict(pid=123, deadline=time.time()+30,
                    output_directory=temp, expected_title="German Empire"), "private-ocr", "unused")
                subject.stage = "setup"
                subject.expected_title = driver.normalized("German Empire")
                data = dict(snapshot=dict(window=dict(id=12)),
                            screenshot=dict(path=str(Path(temp)/"frame.png")))
                passes = [[word("German Empire", .23, .18), word("Cancel Ok", .77, .86)],
                          [word("Cancel Ok", .77, .86)], [word(final, .77, .86)]]
                with patch.object(subject, "command", return_value=data), \
                     patch.object(subject, "_ocr", side_effect=passes) as recognize:
                    _, words = subject.observe()
                self.assertEqual(recognize.call_count, 3)
                self.assertEqual(recognize.call_args_list[-1].kwargs, {"setup_supersampled": True})
                self.assertEqual(subject._cancel_visible(words), accepted)
                self.assertTrue(subject._expected_title_visible(words))

    def test_hud_requires_currency_and_date_in_gameplay_region(self):
        self.assertTrue(driver.loaded_hud([word("$500,000"), word("January, 1850", .85)]))
        self.assertFalse(driver.loaded_hud([word("$1,850")]))
        self.assertFalse(driver.loaded_hud([word("January, 1850")]))
        self.assertFalse(driver.loaded_hud([word("$500,000", .3, .4), word("January, 1850", .3, .5)]))
        self.assertFalse(driver.loaded_hud([word("Building Up Steam")]))

    def test_hud_accepts_observed_ocr_confusion_in_year(self):
        # The game HUD rendered January 1945, but OCR read the 4 as '+'.
        words = [word("$500.000"), word("January. 19+5", .85)]
        self.assertTrue(driver.loaded_hud(words))

    def test_date_signature_requires_full_month_and_separate_hud_fields(self):
        self.assertFalse(driver.loaded_hud([word("$500.000"), word("Jan. 1945", .85)]))
        self.assertFalse(driver.loaded_hud([word("$500.000 January 1945")]))

    def test_date_accepts_only_short_adjacent_speed_glyphs(self):
        currency = word("$500.000")
        for date in ("January. 1850 uE", "January. 1850 WE",
                     "January. 1850 D u e", "January. 1850 O 0 G"):
            with self.subTest(date=date):
                self.assertTrue(driver.loaded_hud([currency, word(date, .85)]))

        for date in ("January. 1850 war", "January. 1850 the",
                     "January. 1850 O 0 G E", "January. 1850 speed controls",
                     "Jan. 1850 uE", "January. 1750 uE"):
            with self.subTest(date=date):
                self.assertFalse(driver.loaded_hud([currency, word(date, .85)]))

    def test_date_accepts_only_observed_january_terminal_glyph_confusion(self):
        currency = word("$500.000", .70, .04)
        self.assertTrue(driver.loaded_hud([currency, word("Januars, 1850", .84, .04)]))
        self.assertFalse(driver.loaded_hud([currency, word("Januars 1850 the railroad", .84, .04)]))
        self.assertFalse(driver.loaded_hud([currency, word("Januars, 15+9", .84, .04)]))
        self.assertFalse(driver.loaded_hud([currency, word("Januars, 1850", .2, .3)]))

    def test_hud_strip_ocr_recovers_fields_without_speed_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "frame.png"
            noisy_full_frame = [word("$500.000R"), word("January. 1850 %C m", .80)]
            hud_only = [word("$500.000", .67), word("Januars, 1850", .82)]
            subject = driver.Driver(dict(pid=123, deadline=time.time()+30,
                                         output_directory=temp), "private-ocr", "unused")
            subject.stage = "loading"
            data = dict(snapshot=dict(window=dict(id=12)), screenshot=dict(path=str(image)))
            with patch.object(subject, "command", return_value=data), \
                 patch.object(driver.subprocess, "run", side_effect=[
                     SimpleNamespace(stdout=json.dumps(noisy_full_frame)),
                     SimpleNamespace(stdout=json.dumps(hud_only)),
                 ]) as recognize:
                _, words = subject.observe()

            self.assertEqual(recognize.call_count, 2)
            self.assertEqual(recognize.call_args_list[1].args[0],
                             ["private-ocr", "--hud-strip", str(image)])
            self.assertTrue(driver.loaded_hud(words))

    def test_hud_strip_ocr_is_loading_only_and_keeps_strict_date_match(self):
        cases = (
            ("startup", [word("$500.000R"), word("January. 1850 %C m", .8)],
             [word("$500.000"), word("January 1850 The railroad is open")], 1, False),
            ("loading", [word("$500.000R"), word("January. 1850 %C m", .8)],
             [word("$500.000"), word("January 1850 The railroad is open")], 3, False),
            ("loading", [word("$500.000"), word("January 1850", .82)], [], 1, True),
        )
        for stage, full_words, hud_words, expected_calls, expected_loaded in cases:
            with self.subTest(stage=stage, expected_loaded=expected_loaded), tempfile.TemporaryDirectory() as temp:
                subject = driver.Driver(dict(pid=123, deadline=time.time()+30,
                                             output_directory=temp), "private-ocr", "unused")
                subject.stage = stage
                data = dict(snapshot=dict(window=dict(id=12)),
                            screenshot=dict(path=str(Path(temp) / "frame.png")))
                output = [SimpleNamespace(stdout=json.dumps(full_words))]
                if expected_calls == 2:
                    output.append(SimpleNamespace(stdout=json.dumps(hud_words)))
                elif expected_calls == 3:
                    output.extend((SimpleNamespace(stdout=json.dumps(hud_words)),
                                   SimpleNamespace(stdout=json.dumps(hud_words))))
                with patch.object(subject, "command", return_value=data), \
                     patch.object(driver.subprocess, "run", side_effect=output) as recognize:
                    _, words = subject.observe()
                self.assertEqual(recognize.call_count, expected_calls)
                self.assertEqual(driver.loaded_hud(words), expected_loaded)

    def test_hud_supersample_retry_is_only_after_both_loading_passes_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "frame.png"
            full = [word("$500.000R"), word("January. 15+9", .84)]
            regular_strip = [word("$500.000", .70), word("January. 15+9", .84)]
            scaled_strip = [word("$500.000", .70), word("January. 18+9", .84)]
            subject = driver.Driver(dict(pid=123, deadline=time.time()+30,
                                         output_directory=temp), "private-ocr", "unused")
            subject.stage = "loading"
            data = dict(snapshot=dict(window=dict(id=12)), screenshot=dict(path=str(image)))
            with patch.object(subject, "command", return_value=data), \
                 patch.object(driver.subprocess, "run", side_effect=[
                     SimpleNamespace(stdout=json.dumps(full)),
                     SimpleNamespace(stdout=json.dumps(regular_strip)),
                     SimpleNamespace(stdout=json.dumps(scaled_strip)),
                 ]) as recognize:
                _, words = subject.observe()

            self.assertEqual(recognize.call_count, 3)
            self.assertEqual(recognize.call_args_list[1].args[0],
                             ["private-ocr", "--hud-strip", str(image)])
            self.assertEqual(recognize.call_args_list[2].args[0],
                             ["private-ocr", "--hud-strip-3x", str(image)])
            self.assertTrue(driver.loaded_hud(words))
            # 3x tokens remain normalized to the original image, in the strict
            # top-right HUD bounds used by the predicate.
            self.assertTrue(all(0 <= w["x"] <= 1 and 0 <= w["y"] <= 1 for w in words))

    def test_hud_supersample_does_not_bypass_currency_month_or_year_checks(self):
        cases = (
            ([word("not-money")], [word("January. 18+9", .84)]),
            ([word("$500.000")], [word("Jan. 18+9", .84)]),
            ([word("$500.000")], [word("January. 15+9", .84)]),
        )
        for currency, date in cases:
            with self.subTest(date=date[0]["text"]):
                self.assertFalse(driver.loaded_hud(currency + date))

    def test_driver_reserves_time_before_parent_watchdog_for_failure_evidence(self):
        parent_deadline = time.time() + 30
        with tempfile.TemporaryDirectory() as temp:
            subject = driver.Driver(dict(pid=123, deadline=parent_deadline,
                                         output_directory=temp), "unused", "unused")
        self.assertEqual(subject.parent_deadline, parent_deadline)
        self.assertEqual(subject.deadline,
                         parent_deadline - driver.FAILURE_EVIDENCE_MARGIN_SECONDS)

    def test_last_frame_capture_is_bounded_and_uses_latest_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, last = root / "first.png", root / "last-source.png"
            first.write_bytes(b"first screenshot")
            last.write_bytes(b"latest screenshot")
            request = dict(pid=123, deadline=time.time()+20, output_directory=temp,
                           expected_title="Example Map")
            subject = driver.Driver(request, "unused", "unused")
            subject.stage = "loading"
            old_target = root / "last-frame.png"
            old_target.write_bytes(b"old failure frame")
            data = [
                dict(snapshot=dict(window=dict(id=1)), screenshot=dict(path=str(first))),
                dict(snapshot=dict(window=dict(id=2)), screenshot=dict(path=str(last))),
            ]
            texts = [[word("old")], [word("x" * 200) for _ in range(driver.MAX_EVIDENCE_TOKENS + 5)]]
            with patch.object(subject, "command", side_effect=data), \
                 patch.object(driver, "loaded_hud", return_value=True), \
                 patch.object(driver.subprocess, "run", side_effect=[
                     SimpleNamespace(stdout=json.dumps(items)) for items in texts]):
                subject.observe()
                subject.observe()

            self.assertEqual(old_target.read_bytes(), b"old failure frame")
            evidence = subject.capture_failure_evidence()

            self.assertEqual(evidence["stage"], "loading")
            self.assertEqual(evidence["observation_count"], 2)
            self.assertEqual(evidence["observation"], 2)
            self.assertEqual(Path(evidence["screenshot"]).resolve(), old_target.resolve())
            self.assertEqual(old_target.read_bytes(), b"latest screenshot")
            self.assertEqual(evidence["screenshot_sha256"], hashlib.sha256(b"latest screenshot").hexdigest())
            self.assertEqual(len(evidence["tokens"]), driver.MAX_EVIDENCE_TOKENS)
            self.assertEqual(evidence["token_count"], driver.MAX_EVIDENCE_TOKENS + 5)
            self.assertTrue(evidence["tokens_truncated"])
            self.assertEqual(evidence["tokens"][0]["text"], "x" * driver.MAX_EVIDENCE_TEXT)
            self.assertEqual(set(evidence["tokens"][0]) - {"text"}, {"x", "y", "width", "height"})

    def test_automation_failure_result_includes_private_last_frame_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "observed.png"
            source.write_bytes(b"observed failure scene")
            output = root / "driver-output"
            request = dict(pid=123, birth_us=4, input_identity="test-input", scenario_name="Fixture",
                           deadline=time.time()+20, output_directory=str(output), expected_title="Fixture")

            def fail_after_observation(subject):
                subject.stage = "loading"
                subject.observation_count = 3
                subject.last_frame = dict(path=str(source), stage="loading", observation=3,
                                          tokens=[dict(text="Loading", x=.5, y=.5,
                                                       width=.1, height=.02)],
                                          token_count=1, tokens_truncated=False)
                raise TimeoutError("no positive loaded scene")

            with patch.object(driver.Driver, "run", fail_after_observation):
                result = driver.run_request(request, "unused", "unused")

            evidence = result["failure_evidence"]
            self.assertEqual(result["status"], "automation_failed")
            self.assertFalse(result["loaded_scene"])
            self.assertEqual(evidence["stage"], "loading")
            self.assertEqual(evidence["observation_count"], 3)
            self.assertEqual(Path(evidence["screenshot"]).resolve(), (output / "last-frame.png").resolve())
            self.assertEqual(Path(evidence["screenshot"]).read_bytes(), b"observed failure scene")
            self.assertEqual(evidence["screenshot_sha256"], hashlib.sha256(b"observed failure scene").hexdigest())

    def test_year_in_scenario_copy_does_not_substitute_for_a_hud_date(self):
        words = [word("$500.000"), word("The railroad comes to Nebraska.", .18, .27),
                 word("1945. Railroads come to Nebraska.", .20, .33)]
        self.assertFalse(driver.loaded_hud(words))

    def test_bottom_roi_ocr_requires_exact_selector_title_and_missing_cancel(self):
        cases = (
            ("setup exact title", "setup", [word("Target Map", .2, .2)], 2),
            ("setup wrong title", "setup", [word("Different Map", .2, .2)], 1),
            ("loading exact title", "loading",
             [word("Target Map", .2, .2), word("$500.000"), word("January 1850", .85)], 1),
            ("setup title and cancel", "setup",
             [word("Target Map", .2, .2), word("Cancel", .77, .86)], 1),
        )
        for label, stage, full_words, expected_calls in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                subject = driver.Driver(dict(pid=123, deadline=time.time()+20,
                                             output_directory=temp, expected_title="Target Map"),
                                        "private-ocr", "unused")
                subject.stage = stage
                subject.expected_title = "targetmap"
                data = dict(snapshot=dict(window=dict(id=12)),
                            screenshot=dict(path=str(Path(temp) / "frame.png")))
                ocr_outputs = [SimpleNamespace(stdout=json.dumps(full_words))]
                if expected_calls == 2:
                    ocr_outputs.append(SimpleNamespace(stdout=json.dumps([word("Cancel", .77, .86)])))
                with patch.object(subject, "command", return_value=data), \
                     patch.object(driver.subprocess, "run", side_effect=ocr_outputs) as recognize:
                    _, words = subject.observe()
                self.assertEqual(recognize.call_count, expected_calls)
                self.assertEqual(recognize.call_args_list[0].args[0], ["private-ocr", str(Path(temp) / "frame.png")])
                if expected_calls == 2:
                    self.assertEqual(recognize.call_args_list[1].args[0],
                                     ["private-ocr", "--bottom-controls", str(Path(temp) / "frame.png")])
                    self.assertTrue(any(driver.normalized(w["text"]) == "cancel" and w["y"] > .75
                                        for w in words))

    def test_stale_startup_play_click_refreshes_before_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            subject = driver.Driver(dict(pid=123, deadline=time.time()+20,
                                         output_directory=temp, expected_title="Example Map"),
                                    "unused", "unused")
            startup = dict(snapshot=dict(treeText="7 button Play", window=dict(id=12)),
                           screenshot=dict(path="/tmp/frame.png", width=800, height=600, scale=1))
            menu = dict(snapshot=dict(treeText="7 button Play", window=dict(id=12)),
                        screenshot=dict(path="/tmp/frame.png", width=800, height=600, scale=1))
            frames = [(startup, []), (startup, []),
                      (menu, [word("Single Plaver"), word("Load Game"), word("Tutorial")])]
            commands = []

            def command(action, *args):
                commands.append((action, args))
                if action == "click" and "--element-index" in args:
                    if sum("--element-index" in saved_args for _, saved_args in commands) == 1:
                        raise driver.OrcaCommandError("click", "element_not_found")
                return {}

            with patch.object(subject, "observe", side_effect=frames) as observe, \
                 patch.object(subject, "command", side_effect=command), \
                 patch.object(driver.time, "sleep"):
                with self.assertRaises(StopIteration):
                    subject.run()

            element_clicks = [(action, args) for action, args in commands
                              if action == "click" and "--element-index" in args]
            self.assertEqual(len(element_clicks), 2)
            self.assertEqual(observe.call_count, 4)  # Includes the first setup-stage observation.
            self.assertTrue(subject.pregame_play_submitted)
            self.assertEqual(subject.pregame_play_stale_attempts, 1)
            self.assertTrue(any(action == "click" and "--x" in args for action, args in commands))

    def test_successful_startup_play_is_not_repeated_while_menu_transitions(self):
        with tempfile.TemporaryDirectory() as temp:
            subject = driver.Driver(dict(pid=123, deadline=time.time()+20,
                                         output_directory=temp, expected_title="Example Map"),
                                    "unused", "unused")
            startup = dict(snapshot=dict(treeText="7 button Play", window=dict(id=12)),
                           screenshot=dict(path="/tmp/frame.png", width=800, height=600, scale=1))
            transitioning = dict(snapshot=dict(treeText="7 button Play", window=dict(id=12)),
                                 screenshot=dict(path="/tmp/frame.png", width=800, height=600, scale=1))
            menu = dict(snapshot=dict(treeText="", window=dict(id=12)),
                        screenshot=dict(path="/tmp/frame.png", width=800, height=600, scale=1))
            frames = [(startup, []), (transitioning, []),
                      (menu, [word("Single Plaver"), word("Load Game"), word("Tutorial")])]
            commands = []

            def command(action, *args):
                commands.append((action, args))
                return {}

            with patch.object(subject, "observe", side_effect=frames) as observe, \
                 patch.object(subject, "command", side_effect=command), \
                 patch.object(driver.time, "sleep"):
                with self.assertRaises(StopIteration):
                    subject.run()

            element_clicks = [(action, args) for action, args in commands
                              if action == "click" and "--element-index" in args]
            self.assertEqual(len(element_clicks), 1)
            self.assertEqual(observe.call_count, 4)  # Includes the first setup-stage observation.
            self.assertTrue(subject.pregame_play_submitted)
            self.assertTrue(any(action == "click" and "--x" in args for action, args in commands))

    def test_startup_play_retry_is_limited_to_element_not_found(self):
        with tempfile.TemporaryDirectory() as temp:
            subject = driver.Driver(dict(pid=123, deadline=time.time()+20,
                                         output_directory=temp, expected_title="Example Map"),
                                    "unused", "unused")
            startup = dict(snapshot=dict(treeText="7 button Play", window=dict(id=12)),
                           screenshot=dict(path="/tmp/frame.png", width=800, height=600, scale=1))
            with patch.object(subject, "observe", return_value=(startup, [])) as observe, \
                 patch.object(subject, "command",
                              side_effect=driver.OrcaCommandError("click", "permission_denied")) as command, \
                 patch.object(driver.time, "sleep"):
                with self.assertRaisesRegex(driver.OrcaCommandError, "permission_denied"):
                    subject.run()

            self.assertEqual(command.call_count, 1)
            self.assertEqual(observe.call_count, 1)
            self.assertFalse(subject.pregame_play_submitted)

    def test_repeated_stale_play_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            subject = driver.Driver(dict(pid=123, deadline=time.time()+20,
                                         output_directory=temp, expected_title="Example Map"),
                                    "unused", "unused")
            startup = dict(snapshot=dict(treeText="7 button Play", window=dict(id=12)),
                           screenshot=dict(path="/tmp/frame.png", width=800, height=600, scale=1))
            with patch.object(subject, "observe", return_value=(startup, [])) as observe, \
                 patch.object(subject, "command",
                              side_effect=driver.OrcaCommandError("click", "element_not_found")) as command, \
                 patch.object(driver.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "bounded refreshes"):
                    subject.run()

            self.assertEqual(command.call_count, driver.PREGAME_PLAY_REFRESH_LIMIT + 1)
            self.assertEqual(observe.call_count, driver.PREGAME_PLAY_REFRESH_LIMIT + 1)
            self.assertFalse(subject.pregame_play_submitted)

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
                [word("Single Plaver"), word("Load Game"), word("Tutorial")],
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
            frames = [(data, [word("Single Player"), word("Load Game"), word("Tutorial")]),
                      (data, [word("Wrong Map", .2, .2), word("Cancel", .77, .86)]),
                      (data, [word("$500,000"), word("January, 1850", .85)]),
                      (data, [word("$500,000"), word("January, 1850", .85)])]
            with patch.object(subject, "observe", side_effect=frames), \
                 patch.object(subject, "click") as click, patch.object(driver.time, "sleep"):
                with self.assertRaises(StopIteration):
                    subject.run()
            self.assertEqual(click.call_count, 1)


if __name__ == "__main__":
    unittest.main()
