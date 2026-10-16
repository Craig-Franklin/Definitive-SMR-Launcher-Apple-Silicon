# Working in this repository

This personal project prioritizes quick iteration, readable code and proportionate
verification. These rules apply to every AI session. Read relevant sections of
[CONTRIBUTING.md](CONTRIBUTING.md) as needed, rather than every historical document.
Development targets the Mac app. Preserve inherited Windows material for upstream
provenance and comparison; this fork does not develop or release Windows software.

## Code-change workflow

1. Read the request, inspect `git status --short --branch`, and run
   `python3 scripts/check_session.py`. Preserve other work. Read the relevant issue,
   existing implementation and callers before editing.
2. Define the intended result, risk and verification. Plan substantial work briefly.
   Features, substantive bugs, sensitive work and work spanning sessions need an
   issue; tiny edits can use their commit record.
3. Make one coherent change. Reuse existing helpers and standard-library facilities.
   Improve touched code when useful; create an issue for larger cleanup.
4. Run focused checks first. Expand them for a concrete remaining risk. **Build only
   when needed.** Follow the verification matrix in CONTRIBUTING. Use the Python 3.12
   development environment for tests and CI/release helpers; macOS `python3` may be 3.9.
5. Review the final diff. Get independent AI review before publishing changes to
   saves, activation/restoration, deletion, crash-report privacy, release/publication
   safeguards, substantial architecture, or **any new dependency**, including tools.
   Resolve findings; record the reviewed revision, scope and outcome.
6. Complete authorized publication. Routine direct pushes are allowed after local
   checks and self-review; PRs are optional. CI checks the pushed revision and gates
   releases. Preserve signing and repository safeguards. Ask only for a missing
   material decision or an action beyond the agreed authority.
   Keep one commit per coherent change; squash PRs and working fixups. Keep new
   `main` history linear. Never rewrite published `main` or bypass its history rule.
7. Report changes, commands/results, published state and remaining work. Update the
   existing issue and acceptance evidence. Leave a compact handoff for unfinished
   work; avoid duplicate active checklists.

## Code and evidence

- Use descriptive names, cohesive functions and clear responsibility boundaries.
  Add abstractions for a demonstrated need. Avoid duplicate implementations,
  speculative extension points and wrappers without a useful responsibility.
- Preserve validation and meaningful error handling. Fix root causes; do not swallow
  failures or weaken a test to make a change pass.
- Add useful regression coverage for outcomes and failure cases. Use the existing
  unittest setup and Python compatibility contract.
- Run `python scripts/check_quality.py`. Ruff is a pinned development tool;
  formatting is adopted incrementally. Keep unrelated formatting out of a change.
- Write saved issues, PRs, commits and docs in concise plain English. State outcomes
  and observable acceptance criteria; link detailed evidence.
- Distinguish source checks, packaged-app checks and actual game behavior. Unit tests
  and successful loads do not prove gameplay, save/reload or full compatibility.

## Use the game knowledge base

For authorized AI game testing, use the repository skill
[smr-game-driver](.agents/skills/smr-game-driver/SKILL.md) and its
[command guide](docs/AI_GAME_TESTING.md). They use the maintained launcher service
and finite visual driver. Verify current private admission and game availability;
loading the skill does not authorize taking over a user-owned game.

For the engine-dependent tasks below, load the repository skill
[smr-game-knowledge](.agents/skills/smr-game-knowledge/SKILL.md). Codex discovers it
from `.agents/skills`; agents without skill discovery should read that file directly.

For game behavior, compatibility, resource resolution, map validation/preparation, crash analysis or an engine-dependent code change, first read [knowledge use and verification](docs/rea-research/KNOWLEDGE_USE.md) and the current-scope section of [VALIDATION.md](docs/rea-research/VALIDATION.md). Use the relevant original knowledge notes and matching private evidence before making a behavior claim. The corpus covers the installed native slices with explicit failures and discovery limits; never imply missing coverage was checked.

Match executable hash, module UUID and architecture/slice; match map archive/prepared/dependency identities where relevant. Reuse existing analysis after digest/coverage checks. Start with the original `knowledge-catalog.json`, `FINDINGS.md` and the developer guide in `docs/rea-research`; use `corpus.py` for bounded private lookup. Pass `--corpus-root` to research auditors to record matching evidence. Keep unknown builds, untranslated Rosetta frames and unresolved resource precedence explicit. Do not transfer an inferred rule to every build or map.

Record knowledge consulted, applied/rejected rule or finding IDs, identity match, coverage gaps and verification evidence in the private case or analysis receipt. Summaries/PRs give a reviewed original explanation of that consultation and its limits. For a new engine-dependent rule, add an independently specified synthetic positive/negative regression and preserve provenance. A static risk flag, a passed synthetic test and runtime load/save/reopen evidence are separate results. No scan flag does not establish compatibility.

Unrelated UI/docs/build-packaging tasks need no full corpus review; state knowledge use as not applicable when reporting such changes. Do not repeat completed smoke checks or read the whole corpus for each task.

## Local extraction and publication

Craig has authorized local decompilation. Do not reopen that decision or add a license-resolution prerequisite. Raw binaries/assets, pseudocode/disassembly, extracted strings/tables, databases, snapshots and diagnostics stay outside Git and cloud tool results. Cloud sessions use reviewed original notes and closed operational projections. Publish reviewed original tools/tests/analysis only; consult [the boundary](docs/rea-research/RIGHTS_AND_PUBLICATION.md). No cloud raw-corpus MCP, paid service, additional worker, game execution/mutation or access-control bypass follows from this authorization.

## Sessions and tools

Use the installed `caveman` skill for chat by default, honoring requests for normal
prose. If unavailable, keep chat brief and clear. Saved code, docs, issues, PRs and
commit messages use plain prose; never sacrifice technical facts or necessary steps.

Use one capable agent by default. Delegate bounded independent work when it saves
elapsed time or supplies required review. Use cheaper models for precise mechanical
work and stronger reasoning for uncertainty or consequential review. Honor explicit
model restrictions. Avoid reviewer swarms and retries without a changed hypothesis.
Load optional skills only when useful; no plugin is required for these standards.

**In Orca**, verify the caller/workspace with the startup check, load the version-matched
`orca-cli` skill, and use Orca for managed worktrees, terminals, handoffs, workers and
workspace status. Use its `orchestration` skill for supervised coordination. Ordinary
file edits, tests and Git commands use shell tools. Hold an Orca-dependent operation
when its identity or availability cannot be verified; do not create unmanaged
replacement state. Outside Orca, use the host's normal supported tools.

## Authorized project continuation

When Craig explicitly asks for continuous work until a project is complete, keep
that project objective active across slices, merges, CI runs, reviews and worker
waves. Those are progress checkpoints, not project stopping points. Use the host's
persistent goal control when available and requested; do not mark the whole goal
complete while required project acceptance remains open.

After each checkpoint, reconcile the live issues and current implementation, then
execute the next useful authorized task. If one lane is blocked, continue independent
work and attempt legitimate blocker resolution or a bounded stronger-reasoning review.
Do not require another routine "continue" message. Do not create busywork, duplicate
accepted audits or add tests solely to keep an agent occupied.

Before stopping project work, check the complete authorized scope, not only the
last assigned slice: either required project acceptance is complete, the user requested
a pause, a host limit requires suspension, or concrete evidence shows every remaining
authorized path is blocked. Record the next task or exact blocker in the existing
issue/handoff. Follow the host's blocked-goal rules; a single blocked gameplay task
must not stop ready source, evidence, integration or publication work.
An individual host turn may end with a progress report while the persistent goal
remains active; that boundary does not pause or complete the project.

Continuation never overrides preservation, privacy, signing, runtime admission,
closed circuits or retry limits. If signing needs Craig, retain tested/reviewed local
changes and continue independent work. Keep the original failure and lineage; never
bypass a safeguard, invent acceptance or reopen a closed method to stay active.

## Preservation and publication

Keep original packages, game files and current/future saves intact. Work on prepared
copies, keep activation transactional, and refuse switching during game use. Never
publish `.handoff`, credentials, local inventories, raw private diagnostics, game/map
assets or saves. Public tests use synthetic or licensed redistributable data.

GitHub mutations target only `Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon`.
Verify the exact repository/remotes before every mutation as described in CONTRIBUTING.
Use configured commit signing and verify GitHub's Verified result after publication.

Private operational context starts at `.handoff/START_HERE.md` when present. Read it
before compatibility, runtime, profile, preservation or private-authority work.
Missing private authority blocks those operations, not ordinary source/docs work.
A new session does not resume a paused programme or reset a closed circuit.
