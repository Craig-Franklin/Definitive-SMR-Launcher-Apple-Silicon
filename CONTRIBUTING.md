# Development workflow

Favor quick, complete changes with clear verification. [AGENTS.md](AGENTS.md) contains
mandatory AI rules. [Architecture](docs/ARCHITECTURE.md) maps the Mac implementation.
The [documentation index](docs/README.md) separates current guides from historical records.

## Setup and commands

Python 3.12 matches macOS CI. Production code retains Python `>=3.9` compatibility.
The application has no Python runtime dependencies. Development and app-build
requirements are separate.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e . -r requirements-dev.txt
.venv/bin/python scripts/check_session.py
.venv/bin/python scripts/check_quality.py
.venv/bin/python -m unittest discover -s tests -p 'test_activation.py' -v
```

Choose the relevant test file. Use discovery: some legacy files define tests below
their direct-execution entry point.

On macOS, `python3` may still be 3.9. Use the Python 3.12 environment above for
tests and CI/release helpers, which use `tomllib`. Install Homebrew `python@3.12`
and `python-tk@3.12` if the development interpreter or Tk is missing.

The complete suite is:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Build only when needed. [Build and release operations](docs/RELEASING.md) describes
build prerequisites and the existing signed-release configuration.

```sh
./scripts/build_macos.sh
./scripts/build_dmg.sh "dist/Definitive SMR Launcher Apple Silicon.app" "dist/Definitive-SMR-development.dmg"
```

## Request to completion

1. **Inspect:** request/issue, existing code and callers, working tree and applicable
   instructions. Preserve unrelated work and user-owned resources.
2. **Define done:** outcome, acceptance criteria, risk and checks. Plan substantial
   work briefly; use a separate plan document only when complexity warrants it.
3. **Implement:** one coherent change, reusing existing code. Include useful nearby
   cleanup; track larger cleanup separately.
4. **Verify:** focused checks first, then expand for a remaining risk or required gate.
   Reuse evidence only when relevant code, inputs and environment match.
5. **Review:** inspect the actual final diff, tests, docs and accidental private data.
   Apply the independent-review triggers below.
6. **Publish:** signed commits, exact repository guard, then observe CI. Routine direct
   pushes are allowed after local checks; PRs are optional. Resolve failed checks
   before further delivery. CI checks a pushed revision and gates releases; it cannot
   prevent the earlier direct push.
7. **Record:** update current issue status and acceptance evidence. Report changes,
   checks, publication and remaining work. Close only when done is proved.

Ask for missing material product decisions or risky/irreversible actions beyond the
agreed scope. Continue routine authorized work without repeated confirmation.

## Verification matrix

Research evidence helpers have a separate synthetic suite when affected:
`python3 -m unittest discover -s scripts/research/rea -p test_evidence.py -v`.
For engine-dependent work, follow the conditional knowledge/evidence route in
AGENTS.md; unrelated UI, docs and build tooling need no full corpus review.

| Change | Local checks | GitHub automation | App build/release |
| --- | --- | --- | --- |
| Docs, templates, AI instructions | Relevant text/configuration checks | Lightweight checks | Neither for these changes alone |
| Tests only | Affected tests | Full existing suite | Neither for these changes alone |
| App code | Focused tests; expand for risk | Full existing suite | Build when verification or a qualifying release needs it |
| Assets, dependencies, packaging, automation | Check affected behavior | Relevant checks; code changes get the full suite | Build when the app/build process needs verification |

Tests should prove outcomes and meaningful failure cases. Avoid tests that mirror the
implementation or repeat expensive checks without a reason. Static, fake, packaged-app
and real-game results are different evidence.

Release intent and build necessity are separate. Conventional `feat`, `fix`, `perf`
and breaking app changes can release after checks. Internal docs/tests/tooling alone
do not release the app. Evaluate all unreleased changes since the previous release,
including an app fix before a docs-only HEAD. Inspect actual files/build inputs,
including renames/deletions, rather than trusting commit labels alone.

`scripts/change_policy.py` shares actual file-impact rules with release planning.
Packaged licenses, attribution, icons and resources count as app inputs. Ruff-only
`pyproject.toml` edits do not. New unknown inputs get conservative verification.
For a change that needs a package check beyond those rules, include the exact commit
footer `Build-Verification: required` and explain why. It requests verification,
not a release. A release build supplies package verification without a duplicate
development build. CI's final `checks` job rejects failed, cancelled, missing or
unexpectedly skipped required jobs; release planning runs again inside the existing
publication concurrency guard.

## Review and readability

Self-review every final diff. Obtain independent AI review before publishing changes to
saves, activation/restoration, deletion, crash-report privacy, release/publication
safeguards, substantial architecture, or any new dependency (including development tools).

Give one scoped reviewer the goal, exact candidate, relevant invariants and evidence.
Record revision/diff identity, findings, resolutions and outcome in the issue or PR;
keep private evidence private. Re-review consequential corrections, not unchanged work.
AI review is an agent obligation, not a GitHub approving-identity gate. Routine human
approvals and mandatory PRs are not required.

Use descriptive names, cohesive functions and explicit interfaces. Look for existing
helpers before adding another implementation. Introduce abstractions for demonstrated
reuse or separation of responsibilities. Preserve validation, errors and data integrity.
A review suggestion should identify a concrete correctness or maintenance benefit.

Ruff is pinned in `requirements-dev.txt`; correctness and unused-code rules live in
`pyproject.toml`. `scripts/check_quality.py` checks maintained Python, formatting and
local Markdown links. Maintained Mac code, tests and ordinary tooling now follow the
formatter, with the exceptions below. Keep unrelated formatting out of subsequent changes.

Of the 34 entries in `.ruff-format-baseline`, 32 preserve source identities: native-capture
and individual-stop bundles, closed runtime paths, and analyzer/resource modules
whose bytes are bound into research receipts. They are not a general style backlog.
Changes to those files need the applicable authority and evidence review before
adopting a new identity; formatting cannot silently renew their acceptance. Narrow
lint exceptions preserve existing declarations in those sources. Do not add exemptions.
The other two entries are packaging helpers: `backend_entry.py` and `dmg_settings.py`
in `scripts/`. Native frontend packaging adopted `launcher_entry.py` and `verify_dmg.py`. Adopt their formatting with the
next required packaging change, avoiding a package build solely for style changes.
The inherited Windows launcher remains outside the maintained lint/format scope.

## Issues, PRs and continuity

Use plain-English titles naming a problem or outcome. Include enough context,
observable **Done when** criteria, and relevant constraints/reproduction steps. Keep
current status and the next action easy to find. Comments record meaningful progress,
decisions and blockers; link logs instead of repeating them. Templates are starting
points; remove irrelevant sections.

Features, substantive bugs, sensitive work and work spanning sessions need an issue.
Reuse a suitable existing issue. Tiny edits can use their commit record. Optional PRs
state what changed, why, verification and review evidence. GitHub issues/Project carry
public scope, status and acceptance. Historical planning docs are background, not
current execution authority; avoid parallel active checklists.

For unfinished work, leave revision/state, completed work/evidence, remaining work or
blocker, next action and critical constraints. Preserve history; update the current
summary. Private operational context routes through `.handoff/START_HERE.md` if present.

## Continuous implementation

For authorized ongoing work, use the repository
[continuous implementation skill](.agents/skills/smr-continuous-implementation/SKILL.md).
Keep the project objective active through ordinary integration, review and publication
checkpoints. Use the existing issue/Orca queue; do not create another active backlog.
A new session or skill invocation never resets a closed method or resumes a pause.

Select a useful observable outcome and the check that will establish it. Before a
retry, connect a concrete hypothesis to changed evidence or a distinguishing test.
Record failure, proposed cause, attempted remedy, result and next decision together
in the existing Task or compact handoff. Consult that record after a handoff and
before repeating an approach; do not reread unchanged history for every command.
Renaming a task or changing agents does not make an unchanged attempt new.

Stop an approach when it returns to the same failure without new information. Choose
a diagnostic that separates plausible causes, reuse an accepted finding, or work on
an independent ready task. A new hypothesis must change the experiment, not just its
description. If no useful authorized path remains, report the exact blocker under
the host's Goal rules. Do not weaken checks, bypass authority, fabricate acceptance,
or recursively commission reviews to keep the session active. Legitimate repair and
investigation continue while they produce discriminating evidence.

Reuse checks/reviews for unchanged relevant code, inputs and environment. Recheck
changed risks and required integration; avoid duplicate verification for ceremony.
Continue independent work while a genuine signing prompt or local blocker waits.
Preserve permitted owned cleanup when new work stops.

### Evaluate progress

At session start, preserve a baseline of current acceptance, candidate revision,
known blockers and ready work in the existing handoff. At natural outcomes or changed
blockers, append a compact record there or in the existing Task:

- Outcome and exact evidence/revision; distinguish source, packaged and runtime proof.
- New fact or eliminated hypothesis; say when no acceptance advanced.
- Failed approach repeated, with what changed and why another attempt was justified.
- Human intervention and reason; required signing differs from avoidable clarification.
- Elapsed interval and material waits when available, plus the next useful action.

Count accepted outcomes and unjustified repeated approaches across comparable tasks,
not tool calls, commits or documents as proxies for progress. Include failed and held
tasks in the denominator. Review the first useful delivery and first complete
end-to-end pilot against the baseline, then summarize at meaningful milestones.
Improvement means supported acceptance advances or a blocker is demonstrably resolved,
without unchanged retry cycles or unnecessary user intervention. Do not infer a causal
speedup from dissimilar tasks or a single successful run.

These records support later human or requested independent inspection. They do not
require a polling agent, timer or metrics service. Persist results before an authorized
completion/blocker notification; notification delivery is distinct from receipt, and
host failure may prevent it. A skill guides judgment; it cannot mechanically prove
useful progress or guarantee zero loops. Existing technical guards and host limits
remain effective unless explicitly changed through the appropriate implementation.

### Pause and restart handoffs

Keep one current handoff that names the exact missing implementation or acceptance
step, retained worktree/revision, evidence to reuse, and the next complete outcome.
Distinguish user constraints from restrictions introduced by the agent. Record
whether the next action is implementation, diagnostic, assisted execution or full
continuation; a limited request does not reactivate the whole programme.

Before closing a settled session, preserve its native session identity and resume
route, pending work, and cleanup disposition. Preserve worktrees and private
artifacts unless deletion is separately authorized. A restart must reconcile live
ownership and closed methods, not repeat the audit or treat old terminal handles
as current authority. Confirm handoff input acceptance separately from turn start;
if delivery is uncertain, inspect or replay the same receipt instead of sending a
duplicate prompt. Keep low-usage handoffs compact and avoid a monitoring agent.

## Orca, models and enforcement limits

Run `scripts/check_session.py` at startup. Explicit Orca session signals trigger CLI
verification of the caller and workspace. Stale/unavailable advertised sessions fail;
normal non-Orca sessions pass without Orca. Secret values are not inspected or logged.
Load the version-matched Orca skill. Use Orca for managed worktree/terminal lifecycle,
handoffs, supervised workers and workspace status. Use shell tools for files, tests and
Git. Hold an operation whose required Orca identity/capability cannot be verified;
preserve ownership, task/retry lineage and user terminals.

Use one capable agent, cheaper models for precise mechanical work, and stronger reasoning
for uncertainty or consequential review. Honor specific model restrictions. Bound worker
scope and ownership; investigate before retries. Optional skills load on demand. No extra
plugin or paid background AI service is required. Record actual time/token usage when
available and identify partial or unavailable measurements.

The startup helper enforces its own checks. Agent instructions govern subsequent tool
choice. GitHub cannot observe arbitrary local commands; this is not a host tool firewall.

## Upstream inheritance

This fork develops, tests and releases the Mac app only. Preserve the inherited
Windows source/assets and their provenance for comparison with upstream. Windows
features are reference points, not claims of Mac support or a Windows work queue.

For an intentional upstream update, inspect its source revision and changed files,
retain attribution, and identify effects on shared assets and Mac behavior. Port
applicable behavior through the existing Mac services and verification process.
Do not bulk-reformat inherited source or push to upstream. Keep delivery to this
fork consistent with the linear-history and signed-commit rules below.

## Publication safeguards

### Commit and merge strategy

Keep one commit per coherent change. Squash branch fixups when integrating a PR;
use a concise conventional title such as `fix: show loading progress (#208)`.
The title describes the delivered result. Keep useful context in the body or linked
issue instead of copying a list of working commit messages. Split unrelated work.

Only squash merges are enabled for PRs. A separate rule requires linear history on
`main`, including updates from the owner's account. Routine signed direct pushes
remain allowed after the relevant local checks/review. GitHub enforces the graph
shape; the agent must judge whether changes are coherent and adequately verified.

Fetch before publication. If `main` advanced, replay only this task's unpublished
changes onto the current base, resolve conflicts, and recheck the affected result.
Re-sign rewritten local commits through the configured signing integration. Do not
merge `main` into a delivery branch and then push that merge commit to `main`.
Rebasing to update a private branch is compatible with squash delivery; GitHub's
rebase-and-merge delivery option is disabled. Never rewrite published `main`, release
tags or another session's commits. Hold shared-branch rewrites for an ownership
decision. Start subsequent independent work from current `main` after squash delivery.

Do not bypass or disable the history rule to deliver a change. Any exceptional
history strategy needs Craig's explicit direction. See
[repository access](docs/REPOSITORY_ACCESS.md) for verified server settings.

### Target and signing

The only authorized mutation target is `Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon`,
repository ID `1404818508`, owned by personal User `Craig-Franklin`. Upstream is read-only.
Before every GitHub mutation use the existing local guard when available:

```sh
python3 .handoff/verify_release_target.py
```

Without that private helper, inspect `git remote get-url origin`,
`git remote get-url --push origin`, and
`gh api repos/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon`. Both origin URLs must
be `https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon.git`; API
identity, owner type and fork parent ID `950948787` must match. Inspect both fetch/push
URLs of any additional remotes and keep upstream read-only. Use explicit fully qualified
GitHub targets.

Preserve configured 1Password SSH signing. Verify GitHub's Verified status after pushing.
Inspect the exact staged/public diff for private data and licensed content. Use synthetic
or licensed test fixtures. [Repository access](docs/REPOSITORY_ACCESS.md) describes the
server-side ownership controls.

## Verification record

[The rollout verification record](docs/WORKFLOW_VERIFICATION.md) links the exact
implementation run, tested cases and enforcement limits. Current task status lives
in the linked GitHub issues.

## Sources

- [OpenAI practical guidance](https://learn.chatgpt.com/guides/best-practices) and
  [AGENTS.md behavior](https://learn.chatgpt.com/docs/agent-configuration/agents-md).
- [Scoped AI review rules](https://developers.openai.com/blog/custom-code-review-rules-for-codex).
- [GitHub check semantics](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).
- [Ruff configuration](https://docs.astral.sh/ruff/configuration/).
- [GitHub merge strategies](https://docs.github.com/en/pull-requests/reference/pull-request-merges)
  and [linear history](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-linear-history).
