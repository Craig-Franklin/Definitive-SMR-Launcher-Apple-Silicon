# Validation and current status

Updated 11 October 2026 in the isolated research worktree. The current project scope below supersedes the initial synthetic-only plan. Historical test results remain evidence of what was actually run.

## Current scope and next work

Craig authorized full local game decompilation and asked for a reusable knowledge base for future building, debugging and crash analysis. REA-03 now records that authorization and the raw-local/reviewed-original-publication boundary; license resolution is no longer a project dependency. First-machine toolchain acceptance is sufficient to proceed. Second-machine replay, exhaustive provider/cache testing and a broad dependency audit are follow-ups rather than blockers. No local game corpus is claimed complete.

A fresh metadata-only inventory inspected both known app locations without decompilation or execution. The small Applications bundle contained three regular files and no native modules. The Steam app contained 536 regular files, six distinct native module hashes and three symlinks excluded from traversal. Architecture/UUID metadata tools succeeded for all six modules; observed architecture sets were x86_64 and i386+x86_64. Slice/dependency/symlink classification remains part of REA-04. Private paths, module identifiers and full inventories stay in the local receipt.

The next ready implementation is the private module/slice manifest and a small resumable runner around the existing ExportEngine.java, reusing any matching completed exports. Then build original subsystem notes and compact AI session handoffs. See README.md for acceptance. Keep compatibility automation paused and raw outputs out of cloud tool responses. No new smoke checks were needed for this documentation revision.

Repository continuity now includes root AGENTS.md, a knowledge-consultation workflow, private case consultation fields, default PR prompts and developer-entry links. Scanner/corpus rule-reference enforcement and actual knowledge lookup are still implementation work; no automatic consultation or runtime verification is claimed.

## Historical synthetic validation — before scope expansion

## Observed results

| Check | Result |
| --- | --- |
| Personal fork and base | `Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon`, public; branch from `465bd9a47a8bf1bd9d5b55609e50cad3e5d0d97c` |
| REA source review | Pinned commit in `toolchain.json`; focused docs, entrypoint, Ghidra launch/session-root and snapshot-cache inspection |
| Local package | `rea-agents@6.4.0`; dependencies installed with lifecycle scripts disabled; lockfile retained |
| Provider doctor | Explicit Ghidra scope healthy; Ghidra 12.1.4, arm64 decompiler and full JDK 21.0.12.1 detected |
| Other integrations | Global environment reported unhealthy for unconfigured optional providers/client registrations; those are not needed for this scoped CLI test |
| Compiler | Apple clang 21.0.0, `clang-2100.3.34.2`; original fixture compiled as x86-64 |
| Real provider query | Synthetic function decompiled, with expected conditional and return present; provider identified as Ghidra |
| Input preservation | Synthetic executable SHA-256 unchanged after analysis |
| Repeated query | Succeeded against the same snapshot path |
| Cache inspection | **Zero primitive and zero workflow cache entries** in the first saved snapshot; repeated query has no cache-replay limitation marker. Cache acceleration is not demonstrated |
| Initial timing | Scoped doctor 0.748 s; compile 0.644 s; first query 6.503 s; repeat 5.761 s. One sample only, not a benchmark |
| Cleanup observation | Empty run-specific temporary directory; no matching Ghidra bridge process observed after the first run; stderr empty |
| Game access | No game binary analysis, launch, map/profile mutation or repair in this work |
| AI integration | No global REA MCP/skill registration, on-device model setup or paid API configuration |
| Reproducibility extent | First-machine smoke only; second clean-machine replay pending |

A second run of the final helper also passed: first query 6.191 s, repeat 5.448 s, zero cache entries, input unchanged. Negative checks confirmed refusal of a Git-worktree output path, a relative output path and a dot-prefixed project path. JSON parsing, document-relative links, package integrity matching, Python compilation and diff whitespace checks passed.

## Cache finding and decision

Upstream source/docs describe exact-query caching, but the installed 6.4.0 CLI decompilation test produced a snapshot with no executable cache bindings. A snapshot file's existence therefore cannot be used as evidence of saved import time. The source review and npm build have not been proven equivalent. At this point the cause was not established; the later document-bound investigation below explains the installed CLI limitation.

The later bounded investigation below addressed this acceptance item with entry counts, exact replay and bridge-event evidence. Additional cache-profile testing is not a corpus prerequisite. Do not upgrade or change providers just to obtain a favorable result. Reuse the existing private export/index in the meantime. The optional REA adoption recommendation does not depend on claiming a cache win.

## Earlier limitations and next-work decision (superseded)

The initial plan deferred game extraction and prioritized rights resolution and another-machine replay. The current authorization and roadmap replace that ordering. Technical limitations remain: the evidence helper is synthetic-only, a full dependency audit has not been performed, and the fixture JVM heap limit does not cap total RSS or whole-run time. These observations do not require repeating the passed tests.

## REA-04 synthetic implementation (11 October 2026)

Implemented a standard-library private evidence index, digest verification, bounded retrieval and closed publication projection. Nine focused regression tests pass, including CLI preservation tests. See [commands and limits](EVIDENCE_INDEX.md). The publication vocabulary contains no arbitrary prose or raw evidence fields. Human review remains explicit; the tool neither authenticates that review nor establishes rights. Raw receipts and the exploratory probe stay outside Git.

The bounded synthetic cache investigation used five invocation attempts, including one logger-interface failure before provider contact, one invalid document selector, one document-list query, and a successful document-bound query plus exact replay. No doctor/compile smoke checks were repeated and dependencies were unchanged. This stayed below 12 queries and 20 active minutes.

Installed 6.4.0 `isSnapshotCacheable` requires a string `document` for `procedure_pseudo_code`; the CLI decompile registration supplies only `procedure`. Predicate checks returned false without a document and true with one. Using the provider-returned document identity yielded one primitive entry and zero workflow entries. The first bound query took 5.449 s; exact replay took 0.030 s. First-call logger events were successful bridge ping, procedure query and shutdown; replay had no bridge events. Installed DirectAnalysis checks matching snapshot evidence before opening the provider. Together these support a cache hit for this exact synthetic API query; timing alone is not the proof. No independent OS process tracing was performed. The replay evidence did not add a cache limitation marker, so that marker is not a reliable acceptance test for this path. The synthetic binary hash was unchanged.

The initial CLI zero-entry result remains valid: built-in decompile caching is limited by omitted document binding. No upstream fix or dependency upgrade was made. Workflow caching, other operations/profiles and source/npm equivalence remain unproven. This is first-machine evidence only.

Effective session evidence: gpt-6.1-sol, low effort, approval never, sandbox danger-full-access, from the current session turn_context. Token usage unavailable. No additional workers, paid APIs, cloud registrations or game access.

Earlier resume instruction, superseded by the current scope above: original REA-04 tooling was ready for draft review while rights and portability were pending. The synthetic helper remains useful but needs real module/slice support for the corpus. Private cache receipts are under the existing synthetic receipt directory in a cache-probe-bound-document subdirectory; no raw receipt is published.
