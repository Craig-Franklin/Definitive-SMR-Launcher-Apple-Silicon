# Mac launcher code map

The original Windows launcher is in `Definitive-SMR-Launcher/`. The independently
implemented Mac app lives in `src/smr_launcher/`; changes here should preserve its
Python compatibility and dependency-free runtime.
This fork maintains the Mac app only. Inherited Windows files support provenance
and upstream comparison, not an active Windows development target.

| Area | Starting points | Responsibility |
| --- | --- | --- |
| Entry points | `gui.py`, `cli.py`, `__main__.py`; `scripts/launcher_entry.py` | Tk interface, command-line interface and packaged startup |
| Application operations | `application.py`, `ui_work.py`, `activity.py` | Coordinate services and background work; keep blocking work out of UI callbacks |
| Catalogue and packages | `collection.py`, `packages.py`, `extraction.py`, `map_metadata.py`, `cache_files.py` | Discover/download/import packages, inspect metadata and share safe cache-file mechanics |
| Prepared variants and rules | `variants.py`, `rules.py`, `compatibility_recipes.py`, `recipe_service.py` | Prepare reproducible copies; preserve originals and recipe provenance |
| Resources and validation | `resource_context.py`, `resource_identity.py`, `import_checks.py`, `model_checks.py`, `industry_registry.py` | Resolve exact resources and validate supported data contracts |
| Activation and saves | `activation.py`, `removal.py`, `steam.py`, `live_run_authority.py` | Journalled profile switching, preservation, process checks and explicit runtime authority |
| Verification and history | `verification.py`, `scenario_evidence.py`, `play_history.py`, `history_ui.py` | Bind evidence/history to the correct variant and distinguish levels of verification |
| Crash reporting | `crash_monitor.py`, `crash_reports.py`, `crash_reporting.py`, `crash_details.py` | Collect private evidence and produce sanitized public reports |
| Updates and release | `updates.py`, `update_helper.py`; `scripts/release_policy.py`, `scripts/release_macos.py` | App updates, release selection and signed publication |
| Build and automation | `scripts/build_macos.sh`, `scripts/build_dmg.sh`, `.github/workflows/` | Package and verify the native app, run checks and publish releases |
| Offline research | `scripts/research/` | Bounded research tools with their own documented authority; not automatic game admission |

Tests live in `tests/` and generally follow the service names. Read the relevant tests
and callers before changing an interface. `gui.py` and `application.py` already carry
several responsibilities: extract a cohesive service when it helps the current change,
and track larger restructuring separately. Avoid creating parallel services for an
operation that already exists.

## Invariants worth reviewing

- Imports preserve original package bytes and reject unsafe extraction paths.
- Prepared variants and their verification depend on exact inputs and rule identities.
- Activation is transactional, game-running checks are conservative, and current/future
  saves remain associated with their exact profile. Recovery must not replay stale state.
- Private diagnostics remain private; public reports are sanitized.
- Release artifacts correspond to their exact source revision and retain signing,
  notarization, verification and provenance safeguards.
- Synthetic checks do not establish gameplay or save/reload acceptance.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the working process,
[Mac setup/status](../MACOS_README.md) for the product, and
[compatibility rules](COMPATIBILITY_RULES.md) for rule contracts.
[Engine notes](ENGINE_MAP.md) and historical audit plans provide background; current
issues and applicable private authority govern execution.
