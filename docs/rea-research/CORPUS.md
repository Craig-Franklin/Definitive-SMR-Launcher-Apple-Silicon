# Private native corpus: commands and limits

`scripts/research/corpus.py` uses Python's standard library, Ghidra 12.1.4 and
JDK 21. The existing original `ExportEngine.java` performs the export. Nothing
in this repository contains the generated game corpus. Use an absolute,
nonhidden private directory outside Git. Symlink roots/components and `..`
escapes are refused. The directory must remain under the user's control;
these checks are not isolation against a hostile local user racing parent
directory replacement.

## Create and export

```sh
REA_PRIVATE_ROOT="$HOME/Library/Application Support/SMR Research/corpus-v1"
python3 scripts/research/corpus.py inventory --root "$REA_PRIVATE_ROOT" \
  --app "$HOME/Applications/Sid Meier's Railroads!.app" \
  --app "$HOME/Library/Application Support/Steam/steamapps/common/Sid Meier's Railroads/Sid Meiers Railroads.app"
python3 scripts/research/corpus.py dependencies --root "$REA_PRIVATE_ROOT"
python3 scripts/research/corpus.py run --root "$REA_PRIVATE_ROOT" --seconds 3600
python3 scripts/research/corpus.py verify --root "$REA_PRIVATE_ROOT"
python3 scripts/research/corpus.py index --root "$REA_PRIVATE_ROOT"
python3 scripts/research/knowledge.py bind --root "$REA_PRIVATE_ROOT"
```

Inventory hashes regular files without following app symlinks, identifies native
Mach-O magic, copies/thins each unique slice, and records architecture and UUID.
Missing UUIDs stay null. Resources are hashed, not unpacked. Symlinks retain
inside-app/external classification. Dependency inspection records `otool -L`
references; runtime search paths can remain unresolved. External system code
is not imported. The wrapper check identifies script/Steam-URL candidates;
it does not execute or certify a shortcut.

The runner uses two Ghidra CPUs, a 2 GiB JVM heap setting, a 20-second timeout
per function and a 60–14,400-second whole-module limit. These are not a total
RSS limit or an operating-system security sandbox. Inputs are independent
copies. A timeout terminates only the runner's owned process group and records
failure. Logs stay private. Concurrent runners on one root are refused.

Each module keeps a project, export and receipt. Re-running `run` verifies and
reuses complete outputs; it does not start their provider again. An interrupted
project resumes analysis, not an assumed `-noanalysis` success. The exporter can
reuse successful functions with matching body/signature/output digests. Failed
functions remain eligible on resume. The module completes when all discovered
functions were attempted, even if some failed. A pre-existing completion marker
is archived before a fresh attempt, so an old marker cannot prove that attempt
succeeded. Use `--module N` to target a module; invalid/empty selections fail.

`verify` rehashes copied inputs, all installed resources and exported function
files. It validates row/status counts and canonical JSONL digests. A modified
tool profile or input fails. Canonical export equality excludes per-function
elapsed time and reuse flags; summary timestamps are not compared. Function
names, signatures, errors, bodies and static reference/string content remain
part of comparison. This is stronger than comparing counts, but not proof of
correct decompilation or identical native runtime behavior.

## Retrieve locally

```sh
python3 scripts/research/corpus.py lookup --root "$REA_PRIVATE_ROOT" \
  --module 0 --kind address --query 0x100001000 --limit 5 --output lookup-example.json
python3 scripts/research/knowledge.py catalog
python3 scripts/research/check_industry_registry.py --installed-library \
  --corpus-root "$REA_PRIVATE_ROOT" > "$REA_PRIVATE_ROOT/industry-audit.json" 2> "$REA_PRIVATE_ROOT/industry-audit.stderr"
python3 scripts/research/audit_map_library.py --installed-library \
  --corpus-root "$REA_PRIVATE_ROOT" > "$REA_PRIVATE_ROOT/map-audit.json" 2> "$REA_PRIVATE_ROOT/map-audit.stderr"
```

The address above is illustrative, not a game finding. Choose a module from the
private manifest; module numbers are inventory-relative. Lookup supports
`function` (entry/name substring), `address` (containing static body range),
`references` (exact source/target) and `string` (SQLite FTS5). It accepts at most
256 query characters and 1–100 rows, default 20. It writes only a contained
private relative output with exclusive creation. Existing files are preserved.
`--include-code` is valid for function/address lookups, with 1 MiB per function
and 2 MiB total. Never return that file to a cloud model.

The index requires complete verified exports and binds their digests/profile.
Lookup refuses changed indexes or changed receipt bindings. Code retrieval
also checks the selected function's digest. Lookup does not rehash every raw
export on each query; run `verify` before a new investigation or after any local
modification. SQLite FTS5 availability is required. JSONL input is streamed,
limited to 512 MiB per file and 2 MiB per record; larger corpora need an explicit
reviewed limit change. Address containment supports plain hexadecimal ranges
below 2^63; external/non-RAM ranges are not indexed. Indirect calls and
undiscovered code can remain absent.

`knowledge.py bind` requires every industry table entry to match independently
imported Ghidra data. Scanner consultation checks catalog/build/slice/UUID and
export digests. Unknown rules and stale catalogs fail. Broader scanner models
without independent evidence retain partial status. The raw scanner JSON still
contains local paths, extracted identities and map data; redirect both streams
to private files. The corpus CLI's stdout is only closed operational metadata.

## Publication and replay

```sh
python3 scripts/research/corpus.py report --root "$REA_PRIVATE_ROOT" --output publication-candidate.json
python3 scripts/research/corpus.py compare --left "$REA_PRIVATE_ROOT" --right "$REA_REPLAY_ROOT"
```

`report` emits only schema/report constants, module count, the eight validated
integer coverage counters, input-preservation flag, static evidence label,
human-review-required marker and zero runtime checks. It never copies unknown
fields, raw text, paths, names, pseudocode or diagnostics into that projection.
The candidate file is still private until reviewed. An approved original case
summary is a separate authored document; raw evidence is never promoted into
one by renaming a file. Human content review remains explicit. Neither a
scanner nor the review marker is legal clearance or authenticated approval.

For a cold replay, use a separate clean checkout and a fresh private root.
Repeat inventory, dependency inspection, export, verification, indexing and
binding. Compare canonical exports, then compare stable scanner payloads and
bounded lookup results. Copying a database or reusing the original export is a
reuse test, not a cold replay. `compare` refreshes both verifications and refuses
incomplete, different-input or different-profile comparisons. A replay on this
Mac is first-machine reproducibility only, even in another checkout/directory.

Synthetic verification, with no game or private corpus in CI:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_research*' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*registry*' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p test_map_library_audit.py -v
```
