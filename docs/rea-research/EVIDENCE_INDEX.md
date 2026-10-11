# Private evidence index and publication projection

The original Python standard-library helper `scripts/research/rea/evidence.py` implements the REA-04 synthetic slice. It does not analyze executables or establish rights. Only the built-in `synthetic-bounded-index` case is supported. Its target identity is the SHA-256 of our fixture source; its tool identity is the SHA-256 of `toolchain.json`. These are source/configuration identities, **not a compiled executable identity**. A game case requires a separate design and REA-03 resolution.

Use an existing absolute private directory outside every Git worktree. Put original synthetic query outputs there. Never pass proprietary evidence to this workflow under the present authorization. Example commands (replace the private root):

```sh
python3 scripts/research/rea/evidence.py --private-root /absolute/private/directory index query.json --kind synthetic-query
python3 scripts/research/rea/evidence.py --private-root /absolute/private/directory retrieve DIGEST --offset 0 --length 4096 --output /absolute/private/directory/excerpt.txt
python3 scripts/research/rea/evidence.py --private-root /absolute/private/directory report --approval approval.json --human-reviewed --output /absolute/review/report.json
python3 -m unittest discover -s scripts/research/rea -p test_evidence.py -v
```

Indexing creates immutable digest-named copies and `index.json`. Existing outputs are never overwritten. Start a new private directory to create a revised index. Retrieval requires an indexed digest and rechecks every indexed object's hash and length before returning a slice. It writes only inside the private root; no raw output is printed. The index contains only schema version, case/source/tool identity, and entries with digest, byte length and an enumerated kind. Unknown fields, duplicate JSON keys, identity changes, and corruption are rejected.

Publication requires a separate local approval object with **exactly** these fields:

```json
{"schema":1,"case":"synthetic-bounded-index","decision":"approved-synthetic-summary","finding":"cache-unproven"}
```

`finding` may alternatively be `tooling-validated`. Review the evidence and proposed finding before supplying `--human-reviewed`. The output contains only schema, case, decision, finding, fixed `original-synthetic-only` scope, and `legal_clearance: false`. It excludes evidence bytes, digests, paths, arbitrary prose, function text and unknown fields. An approved summary is distinct from raw evidence. This intentionally small vocabulary cannot express a diagnosis or arbitrary investigation prose. Do not encode free text in identifiers or extend the schema to carry raw material.

Limits: 128 objects, 1 MiB per object, 64 KiB index, 4 KiB approval and 4 KiB per retrieval. No recursive discovery, network access, subprocess analysis, provider execution or automatic upload. Index creation can leave harmless digest objects if a later validation fails; raw inputs and existing outputs remain intact. The CLI reports generic refusal without echoing sensitive arguments or content. The library raises exceptions for local callers, which must keep diagnostic details private.

Symlinks and parent escapes are refused; final opens use `O_NOFOLLOW`, outputs use exclusive creation and mode 0600. This is a trusted single-user local filesystem tool, not a sandbox against concurrent hostile directory replacement. It does not enforce directory permissions, prove supplied bytes came from the declared synthetic case, authenticate approval provenance, or provide legal clearance. Human review and the research boundary remain necessary. Raw evidence can contain anything; the closed publication projection is the leakage control, not heuristic scanning.

The regression suite covers valid bounded retrieval; wrong Git/relative roots; symlinks and path escapes; hash/source/tool mismatch; malformed, duplicate and unknown fields; disguised text and JSON in approval slots; size bounds; explicit review; and preservation of existing output. Tests use original sentinels and temporary private roots. No game evidence or raw receipts belong in CI.
