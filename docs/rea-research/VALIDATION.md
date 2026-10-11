# Validation and current status

Checked 11 October 2026 in the isolated research worktree. These results apply to tooling and original synthetic code, not the game.

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

Upstream source/docs describe exact-query caching, but the installed 6.4.0 CLI decompilation test produced a snapshot with no executable cache bindings. A snapshot file's existence therefore cannot be used as evidence of saved import time. The source review and npm build have not been proven equivalent. The root cause of the missing cache entries is not established.

Keep this result as a concrete acceptance item in REA-04. Test the pinned CLI/MCP operation and profile before relying on caching; collect entry counts, replay evidence and provider-start observations. Do not upgrade or change providers just to obtain a favorable result. Reuse the existing private export/index in the meantime. The optional REA adoption recommendation does not depend on claiming a cache win.

## Limitations and next work

Game-analysis rights and AI disclosure scope are unresolved. A full dependency security audit and exhaustive review of REA are not complete. The synthetic-only evidence index and closed publication projection are implemented; see EVIDENCE_INDEX.md. Proprietary-case support remains future, rights-dependent work. JVM heap is bounded for the fixture, but total memory and whole-run time are not hard-capped by the helper.

The next useful tasks are REA-03 (rights decision) and a second-machine REA-02 replay. Original-source diagnostics and publication-check development can be scoped independently. Game-specific investigation remains conditional; existing compatibility automation remains paused.

## REA-04 synthetic implementation (11 October 2026)

Implemented a standard-library private evidence index, digest verification, bounded retrieval and closed publication projection. Nine focused regression tests pass, including CLI preservation tests. See [commands and limits](EVIDENCE_INDEX.md). The publication vocabulary contains no arbitrary prose or raw evidence fields. Human review remains explicit; the tool neither authenticates that review nor establishes rights. Raw receipts and the exploratory probe stay outside Git.

The bounded synthetic cache investigation used five invocation attempts, including one logger-interface failure before provider contact, one invalid document selector, one document-list query, and a successful document-bound query plus exact replay. No doctor/compile smoke checks were repeated and dependencies were unchanged. This stayed below 12 queries and 20 active minutes.

Installed 6.4.0 `isSnapshotCacheable` requires a string `document` for `procedure_pseudo_code`; the CLI decompile registration supplies only `procedure`. Predicate checks returned false without a document and true with one. Using the provider-returned document identity yielded one primitive entry and zero workflow entries. The first bound query took 5.449 s; exact replay took 0.030 s. First-call logger events were successful bridge ping, procedure query and shutdown; replay had no bridge events. Installed DirectAnalysis checks matching snapshot evidence before opening the provider. Together these support a cache hit for this exact synthetic API query; timing alone is not the proof. No independent OS process tracing was performed. The replay evidence did not add a cache limitation marker, so that marker is not a reliable acceptance test for this path. The synthetic binary hash was unchanged.

The initial CLI zero-entry result remains valid: built-in decompile caching is limited by omitted document binding. No upstream fix or dependency upgrade was made. Workflow caching, other operations/profiles and source/npm equivalence remain unproven. This is first-machine evidence only.

Effective session evidence: gpt-6.1-sol, low effort, approval never, sandbox danger-full-access, from the current session turn_context. Token usage unavailable. No additional workers, paid APIs, cloud registrations or game access.

Resume: original REA-04 tooling is ready for draft review. Keep Project REA-04 In Progress for review and broader acceptance (including timeout/cleanup fault injection); REA-03 is unresolved and REA-02 still needs another machine. Do not resume compatibility automation. Private cache receipts are under the existing synthetic receipt directory in a cache-probe-bound-document subdirectory; no raw receipt is published.
