# Engine research and map diagnostics

For the optional pinned REA toolchain, original-fixture smoke test, research roadmap
and private-data boundary, start with [Local game knowledge base](../../docs/rea-research/README.md).

Use [the corpus commands](../../docs/rea-research/CORPUS.md) for private inventory,
bulk export, index, lookup and replay. Read [the developer guide](../../docs/rea-research/GAME_DEVELOPER_GUIDE.md)
and [original findings](../../docs/rea-research/FINDINGS.md) for current understanding.
Both auditors accept `--corpus-root /absolute/private/corpus` and attach catalog,
rule and evidence status. Without it they report private knowledge as unavailable.
Their raw JSON still stays private; broader inherited models retain partial status.

## Broader per-scenario audit

`audit_map_library.py` extends the narrow identity screen with reconstructed
factory merge/pruning rules and typed goods/car/annex checks. It also reports
industry alias capacity, the fixed bridge registry, unresolved XML, duplicate
definitions and bridge-range review leads. It never launches or repairs maps.

```sh
.venv/bin/python scripts/research/audit_map_library.py --installed-library \
  > /absolute/private/compatibility-library-audit.json
.venv/bin/python -m unittest discover -s tests -p test_map_library_audit.py -v
```

The report contains local paths and per-map findings; keep it private. Engine
resource precedence and packed assets remain unresolved, so reports describe
candidate effective definitions rather than claiming full runtime simulation.
See the [audit and implementation plan](../../docs/COMPATIBILITY_AUDIT_PLAN.md)
for measured coverage, failure mechanisms and the gates for production use.

## Background industry identity audit

`check_industry_registry.py` inspects map XML without launching the game, moving
the mouse, switching profiles or changing maps. It reads the industry ordering
table from the user's own installed executable. Its offsets are gated by the
exact supported executable SHA-256; another build is refused. No game identity
table or assets are embedded in this script.

Audit all original imports in a launcher library:

```sh
.venv/bin/python scripts/research/check_industry_registry.py --installed-library \
  > /absolute/private/industry-audit.json
```

This reuses launcher discovery for the existing Steam installation and library;
it does not initialize or alter them. Explicit paths remain available:

```sh
python3 scripts/research/check_industry_registry.py \
  --game-binary '/absolute/path/Game.app/Contents/MacOS/game-executable' \
  --library '/absolute/path/launcher-library' \
  --stock-xml '/absolute/path/SMRailroadsData/assets/xml' \
  > /absolute/private/industry-audit.json
```

Use `--library-source prepared` to inspect prepared outputs, or replace
`--library` with `--map-root /absolute/path/one-prepared-map` for a single map.
The optional stock XML directory permits loose stock-file fallback inspection.
The JSON report distinguishes identities outside the fixed table, their explicit
map placements, duplicate definitions, ambiguous filenames and unresolved XML.
Library summaries include both variant count and unique source-directory count.

These are **static risk flags**. An unrecognized identity can be unused or lack
the animation data needed to reach the observed failure path. A recognized
identity does not establish valid assets, successful loading, playable gameplay
or working saves. The script does not inspect FPK contents or claim to reproduce
the entire engine's resource search order. No verification badge is changed.

Run the synthetic checks without a game installation:

```sh
python3 -m unittest discover -s tests -p test_industry_registry_audit.py -v
```

## Ghidra export

`ExportEngine.java` is an original Ghidra postscript for a privately owned, independently copied executable. It exports the current analyzed program without patching its code or changing its function definitions. Decompiled output is reconstructed pseudocode; it is not recovered original source.

Keep executable copies, Ghidra projects, logs, generated indexes and pseudocode outside every Git worktree in the private local research directory. The exporter script itself contains no game bytes. The REA project now authorizes bulk local extraction of all distinct installed game native modules/slices; raw output must not be returned to cloud model tools. Use a bounded operational projection and private logs. Review original analysis separately for publication.

## Run

Install Ghidra and the JDK required by that Ghidra release. Run its `support/analyzeHeadless` against an independent executable copy, with this directory on the script path:

```sh
analyzeHeadless /absolute/private/projects EngineResearch \
  -import /absolute/private/copies/game-executable \
  -scriptPath /absolute/checkout/scripts/research \
  -postScript ExportEngine.java /absolute/private/export 20 \
  -max-cpu 4
```

Ghidra project paths cannot contain dot-prefixed path components. Use a nonhidden private project directory outside Git for projects and exports; do not write research output into the paused compatibility `.handoff`. Configure Ghidra's JVM memory limit separately when processing a large executable.

To export an already analyzed program or resume an interrupted export:

```sh
analyzeHeadless /absolute/private/projects EngineResearch \
  -process game-executable -noanalysis \
  -scriptPath /absolute/checkout/scripts/research \
  -postScript ExportEngine.java /absolute/private/export 20
```

The optional second script argument is the per-function decompilation timeout in seconds, from 1 to 3600; the default is 20. The timeout bounds each decompiler call, not the preceding Ghidra analysis or the entire export.

## Outputs

| File | Contents |
| --- | --- |
| `identity.json` | Imported executable SHA-256, Ghidra version, language/compiler and image base |
| `functions/*.c` | One pseudocode file per successfully decompiled function; explanatory comments for external declarations |
| `functions/*.json` | Address/image offset, name, signature, body ranges and hash, thunk/external flags, status/error, output file/hash |
| `functions.jsonl` | Consolidated function inventory, including failures and external functions |
| `references.jsonl` | Static references identified by the analyzed program, including code and data sources |
| `callgraph.jsonl` | Static call-reference edges plus explicit unresolved call sites |
| `strings.jsonl` | Defined string data recognized by Ghidra, with addresses and types |
| `string-references.jsonl` | References to the starts of those defined strings |
| `RUNNING.json` | Progress checkpoint, refreshed every 100 functions |
| `summary.json` | Latest attempt's totals and outcome |
| `COMPLETE.json` | Marker that every export stage finished; inspect its failure count |

A completed export may still contain timed-out or failed functions. `COMPLETE.json` means the pass finished, not that all code was discovered or every function decompiled. It does not cover other executable modules or dynamically generated code. A call graph may omit indirect targets; a string index may omit undiscovered strings and references into the middle of a string.

## Resume behavior

The output directory is locked during export. Its identity must match the exact binary, Ghidra version, image base and language/compiler. Successful per-function results are reused only when body hash, signature, name and pseudocode hash match. Failed results are retried on every pass. An interrupted consolidated index can be rebuilt from the per-function checkpoints.

Use a new output directory after changing analysis options, global types or other analysis assumptions. The resume key cannot capture every possible effect of a changed program database. Likewise, use a new directory after changing the export format rather than mixing research generations.

Public documentation should summarize verified mechanisms in original prose or small reconstructed examples, clearly marking inferences. Retain the full generated engine output privately.

## Validation

The script was compiled and exercised with Ghidra 12.1.4 and JDK 21 against a small original x86-64 Mach-O fixture. Validation covered function output, an external thunk/declaration, call edges, a known string, successful-result reuse and retry of a failed checkpoint. These tests validate the export mechanics; they do not establish any game compatibility result.


## Map testing

[Batch smoke testing](BATCH_SMOKE.md) documents a separate opt-in runner and its
visible-UI driver. Compile the original screenshot OCR helper once into a private
directory before planning tests:

```sh
swiftc scripts/research/read_screen_text.swift -o /absolute/private/read-screen-text
```

The helper reads screenshots with Apple's on-device Vision framework. It does
not launch games or control input. The Python driver uses Orca Computer Use to
operate the visible game window; it requires Orca and its macOS permissions.
This is local developer research tooling, not a background feature in the
released launcher. OCR and scene-assertion fixture checks have passed; the
complete new runner/driver still requires one real-game calibration.

Unreadable function bodies are recorded as individual export failures. A
successful headless-process exit code is insufficient to prove export completion:
check `COMPLETE.json`, `summary.json` and their failure counts.
