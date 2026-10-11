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
