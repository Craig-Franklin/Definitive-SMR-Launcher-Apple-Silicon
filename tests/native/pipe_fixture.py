"""Synthetic subprocess for the Swift pipe check; never imports launcher services."""

import json
import sys
import time

for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "disconnect":
        # A truncated response cannot establish the outcome of a mutation.
        sys.stdout.write('{"id":')
        sys.stdout.flush()
        break
    if method == "echo":
        print(json.dumps({"event": "fixture", "data": {"synthetic": True}}), flush=True)
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
