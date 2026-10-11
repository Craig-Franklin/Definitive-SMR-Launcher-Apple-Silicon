# Validation and current status

Updated 11 October 2026. Craig authorized full local extraction, original tools/findings through a reviewed PR, merge and a separate main-checkout replay. Raw game evidence stays outside Git and cloud tool results. Compatibility automation remains paused. No game execution, map/profile/save mutation or release is authorized or performed by this slice.

## Current accepted research baseline

| Evidence | Observed result |
| --- | --- |
| Installed inventory | 539 regular files; six distinct native containers; eight slices: six x86_64 and two i386 |
| UUID/symlink coverage | Two i386 slices have no UUID; three inside-app symlinks recorded without traversal |
| Dependencies | 77 references: seven inventoried app files, 68 system references, two unresolved loader references; system code not imported |
| Wrapper | One script/Steam-URL candidate identified without execution; not a second native build |
| Bulk provider | Ghidra 12.1.4 / JDK 21, unchanged original ExportEngine.java; default analysis, two CPUs, 2 GiB JVM setting, 20 seconds/function, 3600 seconds/module |
| Analysis/export | All eight analysis logs reported success; eight completed exports; total bulk wall time 2006.759 seconds on this Mac |
| Function coverage | 81,808 discovered functions attempted: 79,340 decompiled, 2,241 external, 227 failed |
| Failure categories | 199 unreadable-body errors, five timeout messages, 23 other decompiler failures; message-based categories, not proof that each discovered body is valid code |
| Static index | 2,596,292 references; 439,755 call edges; 171,083 identified strings; 107,146 string references; all 81,808 function records indexed |
| Digest/preservation | Function outputs and canonical JSONL verified; independent copied inputs and all 539 installed resources unchanged |
| Targeted lookup | Address/function/reference/string queries returned 1/1/1/5 matches; bounded code retrieval digest checked; raw results private |
| Industry binding | Every one of the 36 byte-reader entries matched independently imported Ghidra references/strings |
| Narrow map screen | 236 variants, 221 directories; 131 with static membership risk, 120 with explicit-placement risk, 10 unresolved; no broken-map/crash count implied |
| Broader map screen | 221 imports, 227 scenarios, 2,579 XML files; catalog/rule references attached; inherited models retain partial knowledge status |
| Map preservation | Before/after hashes matched for 2,716 XML/catalogue inputs; no repair or runtime checks |
| Synthetic source checks | Integrated checkpoint passed 838 tests in 53.111 seconds under Python 3.12; 55 focused research tests and 10 separate synthetic evidence-helper tests passed under Python 3.9 after final build/provenance/CLI hardening; exact latest-main integration checked by PR CI |

The closed publication candidate contains only fixed labels, integer counts, preservation and human-review-required markers. Original code/tests, the finding catalog and authored explanations were reviewed separately. No raw code, game tables/strings, private source paths, snapshots, databases, diagnostics or map names are publication inputs. A scanner is not legal clearance and a review marker does not authenticate human approval.

The existing legacy export's function/output digests also verified for the exact main executable and Ghidra version. Its canonical export differs from the new baseline; it reports 151 main-function failures versus 154 now. Its full original tool-profile history is unavailable. Preserve it as a reference, not a cold replay or silent replacement for this baseline.

The initial bulk pass ran while the original runner was being hardened. Its initial receipts retained the input/export digests, attempt and private log, but did not retain a complete runner-source hash/argv after completion. The fixed exporter hash/profile and provider logs are preserved. The final runner now retains source hash, argv, timing, profile and provider PID. This is a precise first-run provenance limit; the final validators checked the baseline bytes and coverage. The main replay uses final committed source and fresh private projects.

The human-readable GAME_DEVELOPER_GUIDE.md targets a mid-level developer and links supported claims to original findings/limits. Caveman's Codex skill was installed from JuliusBrussee/caveman at 2e08b9177c07bb7249a8a2d1a6758e5db281d002 and applied to the explanation pass. No proxy, model service or recurring automation was installed.

## PR and main replay

PR #205 contains original tools, synthetic tests, continuity instructions and reviewed original findings only. Main's current performance/access-policy commits were merged into the research branch without conflicts. Latest release v0.12.5 already includes that performance change; research commits use chore/docs subjects and do not request a release. Exact-head PR checks, merge and fresh main replay/comparison are pending at this checkpoint. The existing main checkout remains untouched.

A new directory/checkout on this Mac is not second-machine reproducibility. REA-02 remains first-machine accepted. This corpus covers discovered static code, not all possible machine code, original source recovery, every engine subsystem or runtime compatibility. Packed assets, indirect behavior, failed bodies, resource precedence, translated crash-frame mapping and gameplay/save semantics remain explicit limits.

Fresh matching-cwd session receipt: gpt-6.1-sol, effort max, approval never, sandbox danger-full-access. Requested low effort is not effective in the current receipt; no supported in-session effort switch is exposed. Routine work uses deterministic scripts; no extra worker, paid API or cloud MCP was used. Token usage and peak RSS are unavailable. Earlier low-effort reporting below belongs to the earlier synthetic checkpoint.

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
