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
    if method in ("details", "history.record"):
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
                    "game_executable_sha256": "c" * 64,
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
        # A large, entirely fictional library exercises fragmented framing and
        # background presentation preparation without importing real services.
        rows = [
            {
                "id": f"large-{index}",
                "name": f"Synthetic {index}",
                "archive_sha256": "a" * 64,
                "variant_id": "b" * 64,
                "metadata": {"description": "x" * (16 * 1024)},
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
