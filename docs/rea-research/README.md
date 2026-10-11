# Local game knowledge base for Railroads

## Objective and settled decisions

Decompile and index the installed game's native code locally, then preserve reusable knowledge for future building, debugging and crash analysis. Build the corpus once per distinct executable identity and reuse it across AI sessions. The first deliverable is a useful, coverage-accounted local corpus, rather than a single crash diagnosis.

Craig explicitly authorized local game decompilation on 11 October 2026 and asked to remove license resolution as a project dependency. Local extraction may proceed under that instruction. Raw code/assets stay local; original analysis may be published after content review. [Storage and publication boundary](RIGHTS_AND_PUBLICATION.md) records the distinction without reopening the settled decision.

Use the existing Ghidra 12.1.4 installation and resumable `scripts/research/ExportEngine.java` for bulk export. Retain pinned REA 6.4.0 for targeted questions the index cannot answer. No paid tools, new workers, cloud MCP registration or recurring automation are needed.

## Scope of everything

Inventory both known app locations automatically. Identify native executables, embedded native libraries/frameworks, architecture slices and linked dependencies by hash/UUID. Reuse identical module bytes across locations. The initial metadata-only inventory found six distinct native module hashes in the Steam app and no native modules in the small Applications bundle; confirm its launcher/shortcut relationship locally before treating it as a second build. Private symlink/dependency targets must be classified explicitly, never followed by an unrestricted filesystem crawl.

Import and export every discovered function in every distinct in-scope native module/slice. Keep strings, static references, call graphs, per-function failures, analysis options and provenance locally. Inventory scripts and non-code resources alongside that code so future investigations can bind a resource to a build. Textures, audio, archives and other assets are resources to index; they are not recoverable source code. Do not indiscriminately unpack all assets when their identities and relationships suffice. An explicitly needed format can receive its own local index.

External system libraries are dependency references, not a request to decompile macOS. Protected or unreadable inputs get a recorded limitation; access-control bypass is outside scope. No game execution, binary patching, profile/map/save mutation or resumption of the separate compatibility project follows from extraction.

## Working location and start here

- Personal repository only: `Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon`.
- Research branch: `Craig-Franklin/rea-stability-research`; [draft PR #205](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/pull/205).
- [Project #4](https://github.com/users/Craig-Franklin/projects/4) tracks the work. Existing REA IDs are retained; several item scopes changed with this revision.
- Private corpus and receipts live outside every Git worktree. The public branch contains original tools/tests/docs and reviewed original findings only.

At session start read this file, [AI_PLAYBOOK.md](AI_PLAYBOOK.md), the current section of [VALIDATION.md](VALIDATION.md), and the active Project item. Read other files only as needed. Preserve unrelated dirty work and the paused shared compatibility handoff.

## Execution order and acceptance

| ID | Work item | Completion evidence / current state |
| --- | --- | --- |
| REA-01 | Choose the local corpus method | Existing exporter for bulk, REA for targeted gaps; assessment complete |
| REA-02 | Keep the pinned local toolchain usable | First-machine smoke and document-bound cache replay passed; accepted for this Mac; second-machine replay is a portability follow-up |
| REA-03 | Record local authorization and disclosure boundary | Settled by Craig's instruction; local extraction authorized, raw output private, reviewed original analysis publishable; no license-resolution gate |
| REA-04 | Identify builds and make the private corpus resumable/queryable | Metadata inventory started; extend synthetic index to real module/slice identities, digest/coverage checks, bounded retrieval and original-analysis handoffs |
| REA-05 | Complete bulk local decompilation | Reuse verified existing exports first; persist one Ghidra analysis per unique module/slice; export every discovered function; enumerate failures and unresolved code |
| REA-06 | Build reusable knowledge and AI session handoffs | Local function/resource lookup; original subsystem notes with evidence IDs, confidence and unknowns; compact reviewed handoff for future sessions |
| REA-07 | Use the corpus for building, debugging and crashes | One request at a time; matching build/UUID, targeted evidence, falsifying checks and original diagnostics; runtime work stays separately scoped |
| REA-08 | Publish original tools and reviewed analysis as useful | Explicit candidate-file review, synthetic tests for code changes, signed exact-fork push/readback; no raw corpus in GitHub/CI |

## Next ready work

Finish REA-04 and begin REA-05 together: reconcile existing private export identities and completion records without returning raw content to cloud tools; complete the module/slice/dependency inventory; harden a small bulk runner around the existing exporter; checkpoint each module. Do not wait for the synthetic PR to merge, a second machine, a broad dependency audit, exhaustive provider tests or a rights-holder reply.

The synthetic helper in [EVIDENCE_INDEX.md](EVIDENCE_INDEX.md) is implemented and tested, but does not yet accept game evidence. Generalize it only as needed for the corpus, retaining private-root/digest and publication protections. Do not mistake the plan or metadata inventory for completed game decompilation.

## Completion means coverage and reuse

A bulk pass finishing is not proof that all machine code was discovered or every function decompiled. Report module/slice coverage, function totals, successful/failed/external functions, unresolved calls, resource-index coverage and input hashes before/after. Retain failures as work items instead of repeating the whole import. Useful success is that a future session finds a matching build and relevant evidence without reimporting the game or reading a whole dump.

Static findings, synthetic regressions, game load, saved file, reopened save and continued play remain separate evidence. The corpus improves diagnosis; it does not itself establish compatibility or a repaired crash.
