"""Synthetic tools transport; no launcher imports, network or real library."""

import json
import sys

counts = {}
job = {}
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    counts[method] = counts.get(method, 0) + 1
    if method == "fixture.counts":
        data = counts
    elif method == "tools.start":
        if request["params"].get("action") == "disconnect":
            sys.stdout.write('{"id":')
            sys.stdout.flush()
            break
        job = dict(
            job_id="synthetic-job",
            state="unknown",
            message="Result needs inspection; do not repeat automatically.",
        )
        data = dict(job_id="synthetic-job")
    elif method == "tools.status":
        data = dict(job=job)
    else:
        data = {}
    print(json.dumps(dict(id=request["id"], ok=True, data=data)), flush=True)
