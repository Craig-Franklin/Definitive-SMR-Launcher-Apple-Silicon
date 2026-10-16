"""In-memory map-tool provider only; no launcher services, files or network."""

import json
import sys
import time

configuration, counts, last = {}, {}, {}
for line in sys.stdin:
    request = json.loads(line)
    method, params = request["method"], request["params"]
    counts[method] = counts.get(method, 0) + 1
    last[method] = params
    if configuration.get("delay") and method.startswith("maptools."):
        time.sleep(0.15)
    ok, value = True, {}
    if method == "fixture.configure":
        configuration = params
    elif method == "fixture.counts":
        value = counts
    elif method == "fixture.last":
        value = last
    elif method == "maptools.original_context":
        value = {"game_executable_sha256": "c" * 64}
    elif method == "maptools.play_preflight":
        value = {
            "target": params,
            "ticket": "synthetic-ticket",
            "fingerprint": "synthetic-fingerprint",
            "warning": configuration.get("warning", ""),
            "preparation": (
                {"title": "Synthetic compatibility edition"}
                if configuration.get("preparation") and params.get("prepare_compatibility", True)
                else None
            ),
        }
        if configuration.get("preparation"):
            value["warning"] = (
                "Separate compatibility edition; original saves stay intact"
                if params.get("prepare_compatibility", True)
                else ""
            )
    elif method == "maptools.play_commit":
        ok = not configuration.get("reject_commit")
        value = (
            "Native launch outcome unknown; synthetic launch error"
            if not ok
            else {"target": params, "message": "Synthetic launch accepted"}
        )
    elif method == "maptools.inspect":
        ok = not configuration.get("reject_inspect")
        value = (
            "Synthetic ledger unavailable"
            if not ok
            else {"context": params, "issues": [], "history": [], "scope_text": "Synthetic scope"}
        )
    elif method.startswith("maptools."):
        if configuration.get("disconnect"):
            sys.stdout.write('{"id":')
            sys.stdout.flush()
            break
        ok = not configuration.get("reject")
        value = (
            configuration.get("error", "Synthetic validation rejection")
            if not ok
            else {"message": "Synthetic fact recorded"}
        )
    elif method == "details":
        value = {
            "profile_id": params["profile_id"],
            "context": {
                "archive_sha256": "a" * 64,
                "variant_id": "b" * 64,
                "game_executable_sha256": "c" * 64,
            },
            "summary": {"label": "Known issue", "light": "amber"},
        }
    elif method == "shutdown":
        break
    print(json.dumps({"id": request["id"], "ok": ok, "data" if ok else "error": value}), flush=True)
