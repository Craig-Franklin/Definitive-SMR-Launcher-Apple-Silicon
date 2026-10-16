"""Synthetic subprocess for the Swift pipe check; never imports launcher services."""

import json
import sys
import time

counts = {}
configuration = {}
recorded = False
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    counts[method] = counts.get(method, 0) + 1
    if method == "fixture.configure":
        configuration = request["params"]
        print(json.dumps({"id": request["id"], "ok": True, "data": {}}), flush=True)
        continue
    if method == "fixture.counts":
        print(json.dumps({"id": request["id"], "ok": True, "data": counts}), flush=True)
        continue
    if method == "briefing.translate":
        if configuration.get("slow_translation"):
            time.sleep(0.15)
        params = request["params"]
        value = {"id": request["id"], "ok": not configuration.get("reject_translation")}
        value["error" if not value["ok"] else "data"] = (
            "synthetic translation unavailable"
            if not value["ok"]
            else {
                **params,
                "variant_id": "different-edition"
                if configuration.get("changed_translation_edition")
                else params["variant_id"],
                "text": "Présentation fictive traduite.",
            }
        )
        print(json.dumps(value), flush=True)
        continue
    if method == "artwork":
        if configuration.get("slow_artwork"):
            time.sleep(0.15)
        params = request["params"]
        print(
            json.dumps(
                {
                    "id": request["id"],
                    "ok": True,
                    "data": {
                        **params,
                        "variant_id": "wrong"
                        if configuration.get("wrong_artwork")
                        else params["variant_id"],
                        "art": configuration.get("art", ""),
                        "image_png": "",
                        "artwork_state": "ready" if configuration.get("art") else "missing",
                    },
                }
            ),
            flush=True,
        )
        continue
    if method == "annotations.save":
        if configuration.get("slow_annotations"):
            time.sleep(0.15)
        params = request["params"]
        failed = configuration.get("reject_annotations")
        value = {"id": request["id"], "ok": not failed}
        value["error" if failed else "data"] = (
            "synthetic annotations failure"
            if failed
            else {
                **params,
                "saved": True,
                "variant_id": "wrong"
                if configuration.get("changed_annotation_edition")
                else params["variant_id"],
                "annotation": {
                    "title": params["title"],
                    "notes": params["notes"],
                    "image_png": params.get("image_png", ""),
                },
                "art": "",
            }
        )
        print(json.dumps(value), flush=True)
        continue
    if method in ("details", "history.record"):
        if method == "details" and configuration.get("slow_details"):
            time.sleep(0.15)
        failed = configuration.get(
            "reject_record" if method == "history.record" else "reject_details"
        )
        if method == "history.record" and not failed:
            recorded = True
        value = {"id": request["id"], "ok": not failed}
        value["error" if failed else "data"] = (
            "synthetic " + method + " failure"
            if failed
            else {
                "profile_id": request["params"].get("profile_id", ""),
                "context": {
                    "archive_sha256": "a" * 64,
                    "variant_id": ("d" if configuration.get("changed_edition") else "b") * 64,
                    "game_executable_sha256": ("f" if configuration.get("changed_build") else "c")
                    * 64,
                },
                "summary": {
                    "label": "Wrong edition"
                    if configuration.get("changed_edition")
                    else "Played"
                    if recorded
                    else "Never opened",
                    "light": "grey",
                    "opens": 1 if recorded else 0,
                },
                "verification": configuration.get("verification", "Not verified"),
                "history": [],
                "recorded": 1,
            }
        )
        print(json.dumps(value), flush=True)
        continue
    if method == "disconnect":
        # A truncated response cannot establish the outcome of a mutation.
        sys.stdout.write('{"id":')
        sys.stdout.flush()
        break
    if method == "echo":
        print(json.dumps({"event": "fixture", "data": {"synthetic": True}}), flush=True)
    if method == "snapshot":
        if configuration.pop("library_changed_during_snapshot", False):
            print(json.dumps({"event": "library.changed", "data": {}}), flush=True)
        if configuration.get("slow_snapshot"):
            time.sleep(0.15)
        if configuration.get("reject_snapshot"):
            print(
                json.dumps(
                    {"id": request["id"], "ok": False, "error": "synthetic snapshot failure"}
                ),
                flush=True,
            )
            continue
        # A large, entirely fictional library exercises fragmented framing and
        # background presentation preparation without importing real services.
        rows = [
            {
                "id": f"large-{index}",
                "name": f"Synthetic {index}",
                "archive_sha256": "a" * 64,
                "variant_id": "b" * 64,
                "game_executable_sha256": "c" * 64,
                "metadata": {"description": "x" * (16 * 1024)},
                "artwork_state": "pending" if configuration.get("artwork_snapshot") else "ready",
            }
            for index in range(1000)
        ]
        encoded = (
            json.dumps({"id": request["id"], "ok": True, "data": {"maps": rows}}) + "\n"
        ).encode()
        for offset in range(0, len(encoded), 8192):
            sys.stdout.buffer.write(encoded[offset : offset + 8192])
            sys.stdout.buffer.flush()
        continue
    value = {"id": request["id"], "ok": method != "reject"}
    value["data" if method != "reject" else "error"] = (
        request["params"] if method != "reject" else "synthetic rejection"
    )
    encoded = json.dumps(value, ensure_ascii=False) + "\n"
    sys.stdout.write(encoded[:12])
    sys.stdout.flush()
    time.sleep(0.01)
    sys.stdout.write(encoded[12:])
    sys.stdout.flush()
