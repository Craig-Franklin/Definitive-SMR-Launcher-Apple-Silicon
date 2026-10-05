#!/usr/bin/env python3
"""Visible English UI driver for the bounded map smoke-test runner.

Requires Orca Computer Use and the compiled read_screen_text.swift helper.
The parent runner owns the process watchdog, profiles and termination.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

MAX_EVIDENCE_TOKENS = 80
MAX_EVIDENCE_TEXT = 120
PREGAME_PLAY_REFRESH_LIMIT = 2
CRASH_REPORT_CANCEL_LIMIT = 2
FAILURE_EVIDENCE_MARGIN_SECONDS = 5.0


class OrcaCommandError(RuntimeError):
    def __init__(self, action, code):
        self.action = action
        self.code = code
        super().__init__(f"Orca {action}: {code}")


def normalized(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def loaded_hud(words):
    # Both signatures must be in the top-right gameplay HUD, not scenario prose.
    top = [w["text"] for w in words if w["y"] < .13 and w["x"] > .60]
    money = {i for i, t in enumerate(top) if re.fullmatch(r"\s*[$£€]\s*[\d.,]+\s*", t)}
    # One retained HUD screenshot showed Vision reading the full January label
    # as "Januars". Accept that one-character end-glyph confusion only within
    # the same narrow top-right HUD bounds as every other date candidate.
    months = "Januar(?:y|s)|February|March|April|May|June|July|August|September|October|November|December"
    # Vision has rendered 1945 as 19+5. Accept a single observed glyph in the
    # final two positions without inferring the actual year from it.
    year = r"(?:18|19|20)(?:\d{2}|\+\d|\d\+)"
    # The speed buttons immediately follow the date HUD. Vision sometimes
    # joins one or two of their tiny glyphs to the date token (for example,
    # "January. 1850 D u e"). Allow only a very short OCR tail: one or two
    # contiguous alphanumeric glyphs, or two or three individually spaced
    # glyphs. Longer words and prose must not count as a date field.
    adjacent_glyphs = r"(?:\s+(?:[A-Za-z0-9]{1,2}|[A-Za-z0-9](?:\s+[A-Za-z0-9]){1,2}))?"
    # Some scenarios enable a daily calendar: observed HUD "January 1, 1950".
    # Keep a bounded day and require a comma/period before the four-digit year.
    date_separator = r"(?:\s+(?:[1-9]|[12]\d|3[01])[.,]\s*|[.,\s]+)"
    date = {i for i, t in enumerate(top) if re.fullmatch(
        rf"\s*(?:{months}){date_separator}{year}{adjacent_glyphs}\s*", t, re.I)}
    return any(m != d for m in money for d in date)


class Driver:
    def __init__(self, request, ocr, orca):
        self.request = request
        self.ocr = ocr
        self.orca = orca
        self.pid = request["pid"]
        # Finish with enough time to copy the last screenshot and write a
        # bounded failure result before the parent watchdog stops this driver.
        self.parent_deadline = request["deadline"]
        self.deadline = self.parent_deadline - FAILURE_EVIDENCE_MARGIN_SECONDS
        self.out = Path(request["output_directory"]).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.events = self.out / "ui-events.jsonl"
        self.stage = "startup"
        self.expected_title = ""
        self.observation_count = 0
        self.last_frame = None
        self.pregame_play_submitted = False
        self.pregame_play_stale_attempts = 0
        self.crash_report_cancel_attempts = 0

    def event(self, **data):
        with self.events.open("a") as f:
            f.write(json.dumps(dict(time=time.time(), **data)) + "\n")

    def command(self, action, *args):
        remaining = self.deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("UI driver deadline reached")
        proc = subprocess.run([self.orca, "computer", action, "--app", f"pid:{self.pid}",
                               *map(str, args), "--json"], capture_output=True, text=True,
                              timeout=min(12, remaining))
        data = json.loads(proc.stdout)
        if not data.get("ok"):
            code = data.get("error", {}).get("code")
            if code in ("window_not_found", "window_stale", "screenshot_failed"):
                return None
            raise OrcaCommandError(action, code or "unknown_error")
        return data["result"]

    def observe(self):
        self.observation_count += 1
        data = self.command("get-app-state")
        if not data:
            return None, []
        shot = data.get("screenshot")
        if not shot or not shot.get("path"):
            return data, []
        self.last_frame = {
            "path": str(shot["path"]),
            "stage": self.stage,
            "observation": self.observation_count,
            "tokens": [],
            "token_count": 0,
            "tokens_truncated": False,
        }
        words = self._ocr(shot["path"])
        setup_enlarged = False
        if self.stage == "setup" and self._cancel_visible(words) and not self._expected_title_visible(words):
            # Small decorative title text can be misread (Canyon -> Canvon).
            # Re-read the same frame at 3x; never relax the exact title predicate.
            words.extend(self._ocr(shot["path"], setup_supersampled=True))
            setup_enlarged = True
        if self.stage == "setup" and self._expected_title_visible(words) and not self._cancel_visible(words):
            # Small lower-screen controls can vanish at the captured window's
            # native scale. Keep the exact title check on the full-frame pass,
            # then re-read only the bottom controls region before clicking.
            words.extend(self._ocr(shot["path"], bottom_controls=True))
            if not self._cancel_visible(words) and not setup_enlarged:
                # German Empire's small Cancel/OK controls were merged by both
                # 1x passes. A 3x read separates them; require exact Cancel text.
                words.extend(self._ocr(shot["path"], setup_supersampled=True))
        elif self.stage == "loading" and not loaded_hud(words):
            # The full-screen pass often merges the date field with adjacent
            # speed-button glyphs. Re-read only the top-right HUD strip, ending
            # before those controls, and keep the strict full-month/year and
            # separate-currency predicates below.
            words.extend(self._ocr(shot["path"], hud_strip=True))
            if not loaded_hud(words):
                # At small captured window sizes, 1x Vision can misread the year
                # prefix or a month terminal glyph. Retry the same bounded HUD
                # boxes at 3x only after both existing passes fail the signature.
                words.extend(self._ocr(shot["path"], hud_strip_supersampled=True))
        self.last_frame.update(self._bounded_tokens(words))
        self.event(window=data["snapshot"]["window"]["id"], text=[w["text"] for w in words])
        return data, words

    def _ocr(self, screenshot, bottom_controls=False, hud_strip=False,
             hud_strip_supersampled=False, setup_supersampled=False):
        command = [self.ocr]
        if sum((bottom_controls, hud_strip, hud_strip_supersampled, setup_supersampled)) > 1:
            raise ValueError("Choose only one OCR region")
        if bottom_controls:
            command.append("--bottom-controls")
        elif hud_strip:
            command.append("--hud-strip")
        elif hud_strip_supersampled:
            command.append("--hud-strip-3x")
        elif setup_supersampled:
            command.append("--setup-3x")
        command.append(screenshot)
        raw = subprocess.run(command, capture_output=True, text=True, check=True,
                             timeout=min(30, max(1, self.deadline-time.time())))
        return json.loads(raw.stdout)

    def _expected_title_visible(self, words):
        return bool(self.expected_title) and any(
            normalized(w["text"]) == self.expected_title
            and .12 < w["y"] < .30 and w["x"] < .55 for w in words)

    @staticmethod
    def _cancel_visible(words):
        return any(normalized(w["text"]) == "cancel" and w["y"] > .75 for w in words)

    @staticmethod
    def _crash_report_cancel(data, words):
        """Recognize only Feral's observed reporter, returning Cancel alone.

        The surrounding title and report wording are required even for AX.
        Never use a default-button/Return action: that could submit the report.
        """
        snapshot = data["snapshot"]
        tree = snapshot.get("treeText", "")
        visible = normalized(" ".join(w["text"] for w in words))
        context = normalized(tree + " " + str(snapshot["window"].get("title", "")))
        title = "sidmeiersrailroadsunexpectedlyquit"
        if not any(title in text and "anonymousreport" in text for text in (visible, context)):
            return None
        cancel = re.findall(r"^\s*(\d+) button Cancel\s*$", tree, re.M)
        send = re.findall(r"^\s*(\d+) button Send\s*$", tree, re.M)
        if len(cancel) == len(send) == 1 and cancel[0] != send[0]:
            return ("element", cancel[0])
        # Observed dialog has Cancel immediately left of Send at the bottom.
        cancels = [w for w in words if normalized(w["text"]) == "cancel"
                   and .55 < w["x"] < .82 and .84 < w["y"] < .97
                   and 0 < w["width"] < .2 and 0 < w["height"] < .08]
        sends = [w for w in words if normalized(w["text"]) == "send"
                 and .80 < w["x"] < .98 and .84 < w["y"] < .97]
        if len(cancels) == len(sends) == 1 and cancels[0]["x"] + cancels[0]["width"] < sends[0]["x"]:
            return ("word", cancels[0])
        raise RuntimeError("Feral crash reporter recognized but Cancel is not unambiguous")

    @staticmethod
    def _single_player_control(words):
        texts = {normalized(w["text"]): w for w in words}
        single = texts.get("singleplayer") or texts.get("singleplaver")
        if not single:
            return None
        if "loadgame" in texts and "tutorial" in texts:
            return single
        # The hovered Single Player tooltip can obscure Load Game. Require
        # four other exact menu controls in the observed left-hand menu region.
        context = [single] + [texts.get(name) for name in
                              ("tutorial", "multiplayer", "options", "exit")]
        if all(w and 0 <= w["x"] < .25 and .35 < w["y"] < .85 for w in context):
            return single
        return None

    @staticmethod
    def _bounded_tokens(words):
        tokens = []
        for word in words[:MAX_EVIDENCE_TOKENS]:
            try:
                text = str(word.get("text", ""))
                token = {"text": text[:MAX_EVIDENCE_TEXT]}
                for key in ("x", "y", "width", "height"):
                    number = float(word[key])
                    if not (number == number and abs(number) != float("inf")):
                        raise ValueError("Non-finite OCR geometry")
                    token[key] = round(number, 5)
                tokens.append(token)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        return {"tokens": tokens, "token_count": len(words),
                "tokens_truncated": len(words) > MAX_EVIDENCE_TOKENS}

    def capture_failure_evidence(self):
        """Copy and hash only the last screenshot, keeping observation work bounded."""
        evidence = {"stage": self.stage, "observation_count": self.observation_count,
                    "screenshot": None, "screenshot_sha256": None, "tokens": [],
                    "token_count": 0, "tokens_truncated": False}
        frame = self.last_frame
        if not frame:
            return evidence
        evidence.update({key: frame[key] for key in
                         ("stage", "observation", "tokens", "token_count", "tokens_truncated")})
        source = Path(frame["path"])
        target = self.out / "last-frame.png"
        try:
            if source.is_symlink() or not source.is_file():
                raise OSError("Last screenshot is not a regular file")
            if target.is_symlink():
                target.unlink()
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            digest = hashlib.sha256()
            with target.open("rb") as screenshot:
                for block in iter(lambda: screenshot.read(1024 * 1024), b""):
                    digest.update(block)
            evidence["screenshot"] = str(target)
            evidence["screenshot_sha256"] = digest.hexdigest()
        except OSError as exc:
            evidence["capture_error"] = str(exc)
        return evidence

    def click(self, data, word=None, point=None):
        shot = data["screenshot"]
        x, y = point if point else (word["x"] + word["width"]/2,
                                    word["y"] + word["height"]/2)
        return self.command("click", "--window-id", data["snapshot"]["window"]["id"],
                            "--x", round(x*shot["width"]/shot["scale"]),
                            "--y", round(y*shot["height"]/shot["scale"]))

    def run(self):
        expected = normalized(self.request.get("expected_title") or self.request["scenario_title"])
        if not expected or expected.startswith("tag"):
            raise ValueError("A resolved English scenario title is required")
        self.expected_title = expected
        stage = "startup"
        self.stage = stage
        stage_deadline = min(self.deadline-1, time.time()+30)
        consecutive = 0
        last_action = 0.0
        while time.time() < self.deadline:
            if stage != "loading" and time.time() >= stage_deadline:
                raise TimeoutError(f"Could not establish {stage} UI before its deadline")
            data, words = self.observe()
            if not data:
                time.sleep(.3)
                continue
            tree = data["snapshot"].get("treeText", "")
            dismiss = self._crash_report_cancel(data, words) if stage == "startup" else None
            if dismiss:
                if self.crash_report_cancel_attempts >= CRASH_REPORT_CANCEL_LIMIT:
                    raise RuntimeError("Feral crash reporter remained after bounded Cancel attempts")
                self.crash_report_cancel_attempts += 1
                self.event(action="crash_report_cancel_attempt", attempt=self.crash_report_cancel_attempts)
                try:
                    if dismiss[0] == "element":
                        self.command("click", "--window-id", data["snapshot"]["window"]["id"],
                                     "--element-index", dismiss[1])
                    else:
                        self.click(data, dismiss[1])
                except OrcaCommandError as exc:
                    if exc.action != "click" or exc.code != "element_not_found":
                        raise
                # Always observe again. Keep the original startup/parent limits.
                time.sleep(.3)
                continue
            play = re.search(r"^\s*(\d+) button Play\s*$", tree, re.M)
            if stage == "startup" and play and not self.pregame_play_submitted:
                try:
                    submitted = self.command("click", "--window-id", data["snapshot"]["window"]["id"],
                                             "--element-index", play.group(1))
                except OrcaCommandError as exc:
                    if exc.action != "click" or exc.code != "element_not_found":
                        raise
                    self.pregame_play_stale_attempts += 1
                    self.event(action="pregame_play_refresh", attempt=self.pregame_play_stale_attempts,
                               reason=exc.code)
                    if self.pregame_play_stale_attempts > PREGAME_PLAY_REFRESH_LIMIT:
                        raise RuntimeError("Startup Play control stayed stale after bounded refreshes") from exc
                    continue
                if submitted is None:
                    # The known window-stale statuses are already handled by
                    # command(). Refresh the accessibility snapshot before
                    # deciding whether an action can be submitted.
                    self.event(action="pregame_play_refresh", reason="stale_window")
                    continue
                self.pregame_play_submitted = True
                self.event(action="pregame_play")
                time.sleep(.3)
                continue
            # Vision consistently reads the game's decorative y as v at this
            # resolution. Accept only this observed spelling with menu context.
            single_player = self._single_player_control(words)
            if stage == "startup" and single_player:
                self.click(data, single_player)
                stage = "setup"
                self.stage = stage
                stage_deadline = min(self.deadline-1, time.time()+15)
                self.event(action="single_player")
                continue
            if stage == "setup":
                # Positive title match at the scenario selector, plus setup controls.
                title = next((w for w in words if normalized(w["text"]) == expected
                              and .12 < w["y"] < .30 and w["x"] < .55), None)
                cancel = next((w for w in words if normalized(w["text"]) == "cancel"
                               and w["y"] > .75), None)
                if title and cancel:
                    shutil.copy2(data["screenshot"]["path"], self.out / "scenario-selected.png")
                    # English Feral scenario layout: OK is immediately right of Cancel.
                    # Derived from the currently observed Cancel box, not screen coordinates.
                    self.click(data, point=(cancel["x"]+cancel["width"]+.026,
                                            cancel["y"]+cancel["height"]/2))
                    stage = "loading"
                    self.stage = stage
                    self.event(action="load_scenario", expected_title=expected)
                    continue
            if stage == "loading":
                consecutive = consecutive + 1 if loaded_hud(words) else 0
                if consecutive >= 2:
                    target = self.out / "loaded-scene.png"
                    shutil.copy2(data["screenshot"]["path"], target)
                    return dict(status="loaded", loaded_scene=True,
                                assertion="Exact scenario title observed before loading; currency and year in gameplay HUD on two observations",
                                screenshot=str(target), screenshot_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
            # The profile normally skips movies. Space also skips an unexpected intro;
            # never send it once the menu or scenario setup has been recognized.
            intro = any(normalized(w["text"]) in ("firaxis", "firaxisgames", "2k", "2kgames") for w in words)
            if stage == "startup" and intro and time.time()-last_action > 1:
                self.command("press-key", "--window-id", data["snapshot"]["window"]["id"], "--key", "Space")
                last_action = time.time()
            time.sleep(.25)
        raise TimeoutError(f"No positive loaded scene; last UI stage: {stage}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--ocr", required=True)
    parser.add_argument("--orca", default="orca")
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    result = run_request(request, args.ocr, args.orca)
    args.result.write_text(json.dumps(result, indent=2) + "\n")


def run_request(request, ocr, orca):
    result = {key: request[key] for key in ("pid", "birth_us", "input_identity", "scenario_name")}
    result["schema"] = 1
    subject = None
    try:
        subject = Driver(request, ocr, orca)
        result.update(subject.run())
    except Exception as exc:
        result.update(status="automation_failed", loaded_scene=False, reason=str(exc))
        if subject is not None:
            result["failure_evidence"] = subject.capture_failure_evidence()
    return result


if __name__ == "__main__":
    main()
