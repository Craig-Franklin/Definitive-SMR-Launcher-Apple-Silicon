# Documentation map

This is a Mac development repository with preserved upstream Windows material.
Use the guide for your task; historical reports are evidence, not active work queues.

## Current guides and contracts

| Need | Start here |
| --- | --- |
| Install and use the Mac app | [Mac user guide](../MACOS_README.md) |
| Change code, run checks, review or publish | [CONTRIBUTING](../CONTRIBUTING.md); AI rules in [AGENTS](../AGENTS.md) |
| Find the responsible module | [Architecture](ARCHITECTURE.md) |
| Build or configure signed releases | [Build and release operations](RELEASING.md) |
| Understand UI behavior | [Interface](UI_DESIGN.md), [loading feedback](LOADING_FEEDBACK.md), [play-history decisions](MAP_CARD_HISTORY_DESIGN.md) |
| Compare Mac features with the inherited launcher | [Feature comparison](FEATURE_PARITY.md) |
| Work with prepared editions | [Compatibility rule contracts](COMPATIBILITY_RULES.md) |
| Verify publication access | [Repository access](REPOSITORY_ACCESS.md) |
| Check license and asset provenance | [Mac license](../APPLE_SILICON_LICENSE.md), [upstream assets](UPSTREAM_ASSETS.md) |

## Research and execution boundaries

Engine-dependent work starts with [knowledge use and verification](rea-research/KNOWLEDGE_USE.md)
and the current-scope section of [research validation](rea-research/VALIDATION.md).
The [research index](rea-research/README.md) routes to the corpus, findings,
developer guide, evidence index and local setup. [Research tools](../scripts/research/README.md)
describe individual commands and their limits.

The [engine map](ENGINE_MAP.md), [compatibility pipeline](COMPATIBILITY_PIPELINE.md)
and [compatibility audit](COMPATIBILITY_AUDIT_PLAN.md) preserve observations and
design checkpoints for their recorded inputs. Match evidence identities before
reuse. Public [issue #1](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/1)
and assigned issues carry current compatibility scope/status. Private operational
authority is additionally required for runtime work. The programme is paused.

The native-capture and individual-stop bundles preserve reviewed source bytes.
Other source-bound exceptions are listed in `.ruff-format-baseline` and explained
in CONTRIBUTING. Formatting or a passing synthetic check does not renew admission.

## Dated reports

- [Performance analysis](PERFORMANCE_ANALYSIS.md): original baseline and proposal.
- [Performance results](PERFORMANCE_RESULTS.md): delivered changes and measured limits.
- [AI workflow verification](WORKFLOW_VERIFICATION.md): the standards rollout and CI trials.
- [Play-history design](MAP_CARD_HISTORY_DESIGN.md): agreed decisions and original validation scope.

Keep the measurements, failed approaches and provenance in these records. Update
the relevant issue for new work instead of creating another active plan here.
