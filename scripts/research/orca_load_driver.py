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


def normalized(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def loaded_hud(words):
    # Both signatures must be in the top-right gameplay HUD, not scenario prose.
    top = [w["text"] for w in words if w["y"] < .13 and w["x"] > .60]
    money = any(re.search(r"[$£€]\s*[\d.,]+", t) for t in top)
    date = any(re.search(r"[A-Za-z]{3,}[^\n]*\b(?:18|19|20)\d{2}\b", t) for t in top)
    return money and date


class Driver:
    def __init__(self, request, ocr, orca):
        self.request = request
        self.ocr = ocr
        self.orca = orca
        self.pid = request["pid"]
        self.deadline = request["deadline"]
        self.out = Path(request["output_directory"]).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.events = self.out / "ui-events.jsonl"

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
            raise RuntimeError(f"Orca {action}: {code}")
        return data["result"]

    def observe(self):
        data = self.command("get-app-state")
        if not data:
            return None, []
        shot = data.get("screenshot")
        if not shot or not shot.get("path"):
            return data, []
        raw = subprocess.run([self.ocr, shot["path"]], capture_output=True, text=True,
                             check=True, timeout=min(30, max(1, self.deadline-time.time())))
        words = json.loads(raw.stdout)
        self.event(window=data["snapshot"]["window"]["id"], text=[w["text"] for w in words])
        return data, words

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
        stage = "startup"
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
            play = re.search(r"^\s*(\d+) button Play\s*$", tree, re.M)
            if stage == "startup" and play:
                self.command("click", "--window-id", data["snapshot"]["window"]["id"],
                             "--element-index", play.group(1))
                self.event(action="pregame_play")
                time.sleep(.3)
                continue
            texts = {normalized(w["text"]): w for w in words}
            if stage == "startup" and "singleplayer" in texts and "loadgame" in texts:
                self.click(data, texts["singleplayer"])
                stage = "setup"
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
            intro = any(key in ("firaxis", "firaxisgames", "2k", "2kgames") for key in texts)
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
    result = {key: request[key] for key in ("pid", "birth_us", "input_identity", "scenario_name")}
    result["schema"] = 1
    try:
        result.update(Driver(request, args.ocr, args.orca).run())
    except Exception as exc:
        result.update(status="automation_failed", loaded_scene=False, reason=str(exc))
    args.result.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
