# Reproducible stability research for Railroads

## Purpose and recommendation

Use REA with the existing Ghidra installation to answer specific questions about crashes and custom-map compatibility. Preserve reusable evidence and develop original diagnostics and carefully scoped repairs. Start with one known failure and its matching game build; expand only after the method produces a useful, independently checked result.

**Recommendation: adopt REA as an optional research tool.** Keep the existing Ghidra export/index for inexpensive lookups. Use REA for focused function, reference, and call-path questions. It is not a game repair engine, and it does not replace gameplay or save/reload validation.

**Rights boundary:** the game's Steam-linked EULA restricts reverse engineering and decompilation. Keeping output local prevents publication but does not establish permission. Game decompilation and feeding proprietary results to AI remain conditional on resolving the applicable rights. The tools can be installed and validated now using our own synthetic program. See [rights and publication](RIGHTS_AND_PUBLICATION.md).

## Project and working branch

- [GitHub Project: SMR — Reproducible Stability Research](https://github.com/users/Craig-Franklin/projects/4), private and linked to the personal fork.
- Repository: `Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon`.
- Branch: `Craig-Franklin/rea-stability-research`, based on `465bd9a47a8bf1bd9d5b55609e50cad3e5d0d97c`.
- Work happens in a separate Orca worktree. The paused compatibility run remains paused.
- This project plans research and validates tooling. It does not authorize game launches, profile changes, binary patches, new cloud services, or automatic AI crash processing.

The public repository stores our reproducible method. The local research directory stores proprietary inputs and generated analysis. A private GitHub Project is still remote storage: it must not contain game code or raw diagnostics.

## Read this in order

1. [REA assessment](REA_ASSESSMENT.md): suitability, limitations, and alternatives.
2. [Local setup](LOCAL_SETUP.md): pinned dependencies and a reproducible synthetic smoke test.
3. [Rights and publication](RIGHTS_AND_PUBLICATION.md): what stays local and what may be reviewed for publication.
4. [AI instructions](AI_PLAYBOOK.md): targeted retrieval, evidence contracts, resource limits, and model selection.
5. [Validation record](VALIDATION.md): what was actually tested, distinct from future work.

## Roadmap

| ID | Work item | User-visible outcome | Dependency / completion evidence |
| --- | --- | --- | --- |
| REA-01 | Establish the research boundary and assess REA | A clear decision about what the tool can help with | This assessment, references, risks and alternatives recorded |
| REA-02 | Validate a reproducible local toolchain | Another Mac can repeat a small, safe analysis | Pinned npm lock; Ghidra/JDK versions; original x86-64 fixture; analysis, replay, unchanged-input and cleanup evidence |
| REA-03 | Resolve game analysis and AI disclosure rights | A documented basis for the chosen research method | Exact edition/license identified; permission or qualified assessment; local/cloud scope recorded; otherwise use original-source and permitted behavioral diagnostics |
| REA-04 | Create the private evidence index and publication check | Investigations resume without rereading large dumps | REA-03 for proprietary extraction; stable identifiers, digest checks, bounded queries, explicit publication allowlist, negative leak tests |
| REA-05 | Investigate one representative crash | One bounded diagnosis with evidence and remaining unknowns | REA-03/04; matching binary UUID/hash, crash operation, resource identity, competing explanations and falsifying test |
| REA-06 | Turn a verified mechanism into a diagnostic or repair | A clear warning or preserved-copy repair for recognized inputs | REA-05; independent regression, unknown-build refusal, idempotence, original preservation; no silent feature removal |
| REA-07 | Verify game behavior under existing test controls | Evidence that the candidate helps the affected scenario | Separate runtime resumption/admission; baseline/control, load, interaction, save, reopen and continued play; restoration evidence |
| REA-08 | Publish reusable knowledge and audit efficiency | The next investigation is quicker and cheaper | Sanitized original tools/tests/docs, exact publication review, measured time/tokens/query reuse, reproducibility review |

Only REA-01 and the first-machine portion of REA-02 are addressed by the initial setup. A second clean-machine replay is still required. The Project carries specific status and completion criteria. Planned work must not be labelled implemented or game-verified.

The original synthetic REA-04 index and publication projection are now implemented; see [commands and precise limits](EVIDENCE_INDEX.md) and the updated validation record. Review and broader acceptance remain open.

## First investigation after the rights decision

Begin with the known crash represented by [issue #193](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/193) and the scoped crash-to-repair work in [#190](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/190). Prior research reports a conditional legacy collision-data handle/pointer mismatch; the original fault instruction and triggering resource remain unbound. Treat that as a lead to verify, not a settled cause.

The first question is: **Can we bind one crash to the exact game image, failing operation, and triggering map resource well enough to create a safe diagnostic?** Reconcile the latest private checkpoint and accepted evidence before using older findings. An unknown Rosetta frame must not be assigned to a game function merely because its address resembles one.

Next priorities are resource precedence and dependency resolution, fixed registry limits and identity collisions, and save/load differences. Memory growth deserves a measured baseline and explicit operation sequence. Avoid a whole-engine reconstruction or a broad all-map campaign as the first milestone.

## How success is measured

- Repeatability: another licensed installation can reproduce the same procedure, with build differences detected rather than hidden.
- Diagnostic quality: each accepted finding identifies its evidence, scope, competing explanations, and unknowns.
- Stability: fewer reproducible failures for the tested scenario, with required map features and current/future saves preserved.
- Efficiency: fewer redundant imports and tool calls, lower model use for routine work, and measured usage per accepted finding.
- Publication: useful original code and synthetic regressions are available without distributing proprietary game material.

A static check, successful decompilation, successful load, saved file, reopened save, and extended play are distinct results. No single one establishes full map compatibility.
