# AI workflow verification — 11 October 2026

This is a dated verification record for [#210](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/210)
and its final process trial, [#207](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/207).
The issues carry completion/status; this record explains the verified scope.

## Delivered changes

- `171c058`: shared instructions, developer workflow, architecture links, concise
  templates, pinned development-only Ruff and Orca-aware startup checks.
- `ff19488`: shared file-impact rules, read-only release planning, conditional CI
  jobs and a final check that rejects failed or incomplete verification.
- GitHub allows only squash PR merges. Separate rule `24885866` requires linear
  `main` history without bypass actors. Existing owner-only access rule `24883525`
  remains in place. Coherent signed direct pushes remain allowed.

## Verification

The [implementation run](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/actions/runs/38159235772)
checks exact commit `ff194881d2f0b847cbe83db338b34f33231ac67d`.
Both delivered commits are GitHub Verified.

GitHub ran **918 main-suite tests** and **10 research evidence helper tests**, all
successfully. Planning, quality, tests and the final `checks` job passed. Both app
`build` and `release` jobs were skipped. The planner reported Python quality and
full tests required, app build and release not required.

Locally, 48 focused tests passed: 15 startup/baseline cases, 9 file-impact and final-gate
cases, and 24 release-policy/publication cases. Ruff, adopted formatting, documentation
links and workflow YAML parsing passed. A public export worked without the private
handoff and without an Orca session.

Synthetic Git histories cover internal-only changes with misleading commit labels,
app changes, a docs-only HEAD after an unreleased app fix, packaged documentation,
renames/deletions, tool-only versus app TOML changes, and invalid history. Job-result
fixtures cover failures, cancellation, missing results, unintended skips and inconsistent
plans. The existing tag/version, checksum and source-identity safeguards remain tested.

Independent reviews approved the Ruff dependency, private preservation, startup
corrections and merge settings, and the CI/release changes after a focused correction.
Reviews found and resolved baseline-history validation, stale/malformed Orca identity,
and inconsistent CI-plan gaps. The trial also made Python 3.12 setup explicit because
macOS `python3` can resolve to 3.9.

Publishing this documentation is the docs-only follow-up trial. Its completed run and
job outcomes are recorded in #207, avoiding a self-referential evidence commit loop.

## Enforcement boundaries

GitHub enforces PR merge methods and linear history. CI enforces relevant checks,
Ruff/format adoption, and release gates. A direct push happens before its CI result;
CI failure blocks release and must be resolved before further delivery.

Independent AI review, coherent change boundaries, code reuse, model selection and
later Orca tool usage remain agent obligations. The startup helper validates its
caller/workspace; it cannot police every subsequent command. Linear history does not
remove the owner's existing ability to rewrite a linear history through the separate
access-rule bypass. The instructions forbid such rewrites without explicit direction.

No app build or dummy release was needed for this rollout. Signing/notarization and
publication shell steps were retained; a new production app release was not exercised.
Unit/source checks do not establish gameplay or save/reload behavior. Compatibility
execution remained paused. Private originals and operational evidence remain private.
