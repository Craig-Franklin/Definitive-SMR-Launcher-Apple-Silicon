# Repository instructions

## Start and preserve scope

Inspect the branch and dirty tree before substantive work. Preserve unrelated work. Personal fork only: `Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon`; pass `--repo` on supported GitHub commands and explicit owner/project IDs elsewhere. Craig authorized completing this corpus slice, merging its reviewed PR and replaying from main on 11 October 2026. No release or resumption of the paused compatibility workers/shared `.handoff` is authorized. Future merges require their own task authorization.

## Use the game knowledge base

For game behavior, compatibility, resource resolution, map validation/preparation, crash analysis or an engine-dependent code change, first read [knowledge use and verification](docs/rea-research/KNOWLEDGE_USE.md) and the current-scope section of [VALIDATION.md](docs/rea-research/VALIDATION.md). Use the relevant original knowledge notes and matching private evidence before making a behavior claim. The corpus covers the installed native slices with explicit failures and discovery limits; never imply missing coverage was checked.

Match executable hash, module UUID and architecture/slice; match map archive/prepared/dependency identities where relevant. Reuse existing analysis after digest/coverage checks. Start with the original `knowledge-catalog.json`, `FINDINGS.md` and the developer guide in `docs/rea-research`; use `corpus.py` for bounded private lookup. Pass `--corpus-root` to research auditors to record matching evidence. Keep unknown builds, untranslated Rosetta frames and unresolved resource precedence explicit. Do not transfer an inferred rule to every build or map.

Record knowledge consulted, applied/rejected rule or finding IDs, identity match, coverage gaps and verification evidence in the private case or analysis receipt. Summaries/PRs give a reviewed original explanation of that consultation and its limits. For a new engine-dependent rule, add an independently specified synthetic positive/negative regression and preserve provenance. A static risk flag, a passed synthetic test and runtime load/save/reopen evidence are separate results. No scan flag does not establish compatibility.

Unrelated UI/docs/build-packaging tasks need no full corpus review; state knowledge use as not applicable when reporting such changes. Do not repeat completed smoke checks or read the whole corpus for each task.

## Local extraction and publication

Craig has authorized local decompilation. Do not reopen that decision or add a license-resolution prerequisite. Raw binaries/assets, pseudocode/disassembly, extracted strings/tables, databases, snapshots and diagnostics stay outside Git and cloud tool results. Cloud sessions use reviewed original notes and closed operational projections. Publish reviewed original tools/tests/analysis only; consult [the boundary](docs/rea-research/RIGHTS_AND_PUBLICATION.md). No cloud raw-corpus MCP, paid service, additional worker, game execution/mutation or access-control bypass follows from this authorization.

## Checks and code review

Use focused synthetic tests for changed behavior; the general suite is `python3 -m unittest discover -s tests -v`. REA helper checks are `python3 -m unittest discover -s scripts/research/rea -p test_evidence.py -v`. Tests use original fixtures, never private game evidence in CI. Reuse accepted results unless changed inputs invalidate them.

Reviewers should flag engine-dependent claims with no matching-build knowledge/evidence record, hypotheses promoted to verified facts, unmapped scanner rules or static scans promoted to gameplay/save verification. Preserve useful original findings with their limits so the next session can reuse them.
