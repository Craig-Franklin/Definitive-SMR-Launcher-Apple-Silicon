# Use game knowledge as a reference and verification source

## Entry point and current coverage

Root `AGENTS.md` requires consultation for engine-dependent development, crash analysis and custom-map scans. It points here rather than depending on the previous chat. Codex discovers repository instructions when starting a run/session; it does not refresh every already-running session automatically. A checkout without this file cannot inherit it. See [official instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

Current coverage is recorded in VALIDATION.md: installed module metadata inventory and original synthetic tools exist; the complete game corpus and new reviewed subsystem findings do not yet exist. Until they do, report `partial` or `unavailable` for the missing evidence. Existing original code/docs are useful references, but their presence is not proof that a new result was checked against the engine.

## Minimal consultation record

Use `knowledge_use` in the private `case.template.json` record for engine-dependent tasks. A scanner receipt may carry the same information without creating a second document. Record:

- Status: `matched`, `partial`, `unavailable` or `not_applicable`; a new template starts as `not_checked`.
- Knowledge revision/digest and local corpus identity; matching game/module hash, UUID and architecture/slice.
- Consulted finding/rule IDs; applied and rejected rules; relevant unresolved/contradictory evidence.
- Exact scan/input identity or crash incident identity; tests/observations used to verify the conclusion.

Private records may include local references; publish only reviewed original summaries. `not_applicable` is for changes such as layout, prose or packaging that do not depend on game semantics. It is not a shortcut for a crash or compatibility conclusion. Missing knowledge does not block independent work; it limits the claim and identifies the next useful input.

## Crash report workflow

1. Parse and retain the raw report locally. Reuse the existing original crash parsers; do not paste the report or game function text into cloud prompts. Identify the actual exception/faulting thread and module UUID/architecture. Record the executable hash when the matching image is available; mark a missing hash explicitly.
2. Match the module/slice and map/resource identities before mapping addresses. Keep runtime address, image base, image-relative offset and file offset distinct. A translated/unknown frame remains unknown without a supported mapping.
3. Query the private corpus locally for the fault and a bounded neighborhood; consult reviewed original findings about that subsystem. Record evidence IDs and competing explanations. A crash in loading code does not itself identify the offending map resource.
4. State the diagnosis at its supported level: observed fault, candidate mechanism or verified mechanism. Record one falsifying check or missing input. Add an original regression/diagnostic only when its condition is supported. Runtime reproduction or repair validation is a separate task if needed.

Useful original-code entry points: `src/smr_launcher/crash_reports.py`, `crash_details.py`, `crash_monitor.py` and their focused synthetic tests. Consult their current contracts before changing parsing or automatic reporting. The corpus must not silently expand uploaded crash payloads.

## Custom-map scan workflow

1. Bind the game build and the exact archive/import/prepared variant. Reuse unchanged input digests; record changed dependencies and analyzer revision. Scan local copies read-only.
2. Consult applicable original knowledge for resource precedence, identity/registry behavior, references, merge rules and save/load dependencies. Reconcile the scanner's assumptions with the matched build rather than treating its implementation as the engine specification.
3. Run the applicable existing static auditors locally with stdout/stderr redirected to private files. `scripts/research/README.md` documents `check_industry_registry.py` and `audit_map_library.py`; their JSON can contain raw extracted identities and local paths and is not a cloud/public report.
4. A finding should identify the original rule/finding used, input evidence and certainty: a known invalid condition, a static risk, a heuristic review lead or unresolved input. Missing/ambiguous/packed dependencies must remain visible. Report check coverage and omitted checks alongside findings.
5. A no-finding result means no implemented check matched the inspected inputs. It does not prove the map loads, plays, saves or reopens. Only separate runtime evidence can support those claims.

The existing auditors already report provenance/limitations, but do not yet attach the new corpus revision and finding/rule IDs to every result. Their integration is a concrete REA-04/06/07 acceptance item, not a completed enforcement feature. Do not rerun the entire library solely to decorate an unchanged report; add metadata in the next relevant scanner change or run.

## Building and learning loop

Before an engine-dependent code change, find the relevant original behavior note and its evidence. Use it to specify an original synthetic counterexample and positive/negative cases; do not write a test that merely repeats the new implementation. Test unknown-build/input refusal when build-specific behavior is used. A synthetic pass validates the diagnostic/model under its assumptions, not the game.

When a new finding changes understanding, preserve a compact original note with a stable ID, applicable builds, private evidence IDs, confidence, counterexamples, superseded findings and links to original code/tests. Use that note as the next session's reference. Keep detailed raw evidence local and reviewed original summaries discoverable. Promote a rule only to the evidence level actually achieved.

The next knowledge-index implementation must provide a local locator, a digest/versioned finding catalog and lookup by build/subsystem/rule. Scanner/diagnostic receipts will bind that catalog revision and enumerate applied rules; synthetic checks will reject missing/unknown references and wrong-build use. CI can test the original contracts with synthetic fixtures; it cannot access the private corpus or prove a model actually read it.

## What is enforced now versus next

Implemented now: root instructions, this consultation contract, private case fields, default PR review prompts, and links from developer/research entry points. This makes omission visible to a reviewer without a new approval step.

Next in REA-04/06/07: corpus/finding lookup and machine-checked rule/evidence references in diagnostic/scan receipts. These checks must preserve useful partial results while refusing an engine-verified claim without matching evidence. Instruction following alone is not a mathematical guarantee, and a filled-in receipt is not semantic proof; provenance, useful regressions and explicit review provide separate checks.

These files currently live on the research branch/draft PR. New sessions using that branch can discover them. Other branches/checkouts need the reviewed instruction/doc changes incorporated before claiming the same coverage. Do not copy files into the dirty main checkout or merge the PR without authorization. Existing sessions must explicitly read the new instructions; no automatic rollout is claimed.
