# REA assessment

Reviewed 11 October 2026. Source snapshot: [`ca11162d027a0d3e3b5a65a54825b00d5689fb80`](https://github.com/morluto/rea/tree/ca11162d027a0d3e3b5a65a54825b00d5689fb80). Installed npm package: `rea-agents@6.4.0`. The source checkout and registry artifact are separate provenance records; matching version labels alone do not prove byte equivalence.

## What REA contributes

REA provides a CLI and MCP interface over analysis providers. For this project the relevant provider is Ghidra, which is already installed and avoids a new commercial-tool purchase. Supported native workflows include metadata, functions, pseudocode, assembly, references and calls. That can help connect a bounded crash location to resource validation and original launcher diagnostics. [CLI reference](https://github.com/morluto/rea/blob/ca11162d027a0d3e3b5a65a54825b00d5689fb80/docs/CLI.md).

The reviewed Ghidra integration accepts 12.1.x and documents verification with 12.1.4/JDK 21 on supported hosts, including macOS arm64. It imports one program into an ephemeral project, uses read-only/delete-project arguments, and does not open the user's existing Ghidra database. Some naming/comment operations change temporary analysis metadata without patching executable bytes. [Provider installation and behavior](https://github.com/morluto/rea/blob/ca11162d027a0d3e3b5a65a54825b00d5689fb80/docs/INSTALLATION.md#ghidra).

This is a focused integration review, not an exhaustive security or correctness audit of every REA tool. The project includes many unrelated application, browser, Android and runtime features; they are unnecessary for the initial SMR workflow.

## Source checks behind the recommendation

| Area inspected at the pinned commit | Finding and consequence |
| --- | --- |
| `package.json`, `scripts/rea.mjs` | Node compatibility, package identity and dispatch between CLI and MCP; published package includes built runtime |
| `src/ghidra/GhidraJavaLaunch.ts` | Explicit Java launch, isolated Java home/temp properties and heap configuration; heap is not total process memory |
| `src/ghidra/GhidraSessionRoot.ts` | Ghidra project roots avoid dot-prefixed path components; use a nonhidden private directory |
| `src/application/binary/AnalysisSnapshotCache.ts` | Target/profile-bound snapshot reuse excludes live and mutable queries; precise parameters matter |
| `docs/CLI.md`, `docs/INSTALLATION.md` | New CLI calls are separate processes; an exact snapshot hit can avoid provider startup; new questions can require reanalysis |
| Existing launcher `scripts/research/ExportEngine.java` and README | An original resumable export already exists; reuse successful local output after identity/completeness checks |

## Best fit for this game

| Problem | Appropriate use | Limit |
| --- | --- | --- |
| A crash at a known matching-image offset | Follow a small function neighborhood and compare it to recorded inputs | Decompiler types and inferred names can be wrong; a crash stack alone does not identify the asset |
| Resource-loading order | Examine specific lookup/selection paths and design a controlled validation experiment | Static code does not prove which loose/packed resource was selected at runtime |
| Identity or registry limits | Establish a bounded condition; express it in original audit code and synthetic fixtures | Correctness remains build-specific; do not publish extracted game tables |
| Save/load failures | Compare relevant control flow and supplied diagnostics | Requires an independently admitted actual save/reopen test |
| Excess memory use | Use static findings to guide bounded observations | A suspicious allocation path does not prove a leak or peak-memory cause |
| macOS/Rosetta failures | Keep x86-64 game, arm64 host, UUID, image base and offset evidence explicit | Linux recorded-crash functionality is not a macOS crash-symbolication guarantee |

## Recommended operating mode

Use the deterministic local CLI and existing private indexes first. Query one function or reference set at a time. Craig has now authorized local extraction. A separately scoped local-only persistent integration can reduce repeated provider imports for a sequence of new questions. Save its exact-query snapshot on close. Verify the active provider and cleanup receipt.

Do not enable the whole MCP surface in a cloud agent just to get convenience. Tool annotations are useful descriptions, not an enforced sandbox. Build the private query/export interface as REA-04 under the current local-extraction authorization. For strict local-only analysis, use manual CLI/Ghidra or a separately evaluated on-device model without external fallback; cloud Codex sees tool results it is given.

The initial installed-package smoke test produced zero snapshot cache entries; see [the validation record](VALIDATION.md). A later document-bound installed-API query demonstrated one primitive cache hit; the built-in decompile CLI still omits that binding. Workflow caching remains unproven. REA snapshots are intended as query caches, not substitutes for persistent Ghidra projects. Retain the existing exporter for large reusable indexes and reserve REA for questions not answered by those indexes. Neither workflow should automatically publish its output.

## Alternatives and adoption decision

- Existing Ghidra/export workflow: lowest integration cost; best for already-indexed functions and deterministic searches. Retain it.
- REA + Ghidra: recommended targeted addition; original synthetic smoke test is the initial acceptance step.
- Hopper or IDA: optional later only if a demonstrated Ghidra limitation blocks an important question and licensing/cost are agreed. No purchase is needed for the initial plan.
- Full local corpus: now the chosen baseline for future building/debugging/crash work, using the existing resumable exporter. This does not mean recovered original source, a replacement engine or an agent swarm.

Reassess adoption after one bounded investigation. Keep REA if it reduces analyst effort or reveals materially better evidence. Record a failure honestly if it only adds import time and tool-output volume.

## Revised decision — 11 October 2026

Create the broad reusable local corpus first, then use targeted REA queries for missing evidence. Preserve build/coverage accounting and reusable original knowledge notes. A single representative crash is a later use case, not a prerequisite. Second-machine replay, additional commercial providers and exhaustive cache tests are optional follow-ups when a concrete need appears.
