# Mac launcher code map

The original Windows launcher is in `Definitive-SMR-Launcher/`. The independently
implemented Mac app uses `src/native/` for SwiftUI/AppKit presentation and
`src/smr_launcher/` for Python services. Preserve Python compatibility and the
dependency-free Python runtime.
This fork maintains the Mac app only. Inherited Windows files support provenance
and upstream comparison, not an active Windows development target.

| Area | Starting points | Responsibility |
| --- | --- | --- |
| Native presentation | `src/native/SMRLauncher.swift`, `LauncherView.swift`, `LauncherModel.swift`, `Backend.swift` | Native window, retained browsing/drafts and private subprocess transport |
| Entry points and bridge | `native_backend.py`, `gui.py`, `cli.py`, `__main__.py`; `scripts/launcher_entry.py` | Packaged Swift entry, hidden Tk lifecycle/advanced tools and Python developer/CLI entry |
| Personal display details | `annotations.py` | Identity-keyed private title/notes/image overrides, separate from package facts |
| Application operations | `application.py`, `ui_work.py`, `activity.py` | Coordinate services and background work; keep blocking work out of UI callbacks |
| Catalogue and packages | `collection.py`, `packages.py`, `extraction.py`, `map_metadata.py`, `cache_files.py` | Discover/download/import packages, inspect metadata and share safe cache-file mechanics |
| Prepared variants and rules | `variants.py`, `rules.py`, `compatibility_recipes.py`, `recipe_service.py` | Prepare reproducible copies; preserve originals and recipe provenance |
| Resources and validation | `resource_context.py`, `resource_identity.py`, `import_checks.py`, `model_checks.py`, `industry_registry.py` | Resolve exact resources and validate supported data contracts |
| Activation and saves | `activation.py`, `removal.py`, `steam.py`, `live_run_authority.py` | Journalled profile switching, preservation, process checks and explicit runtime authority |
| Verification and history | `verification.py`, `scenario_evidence.py`, `play_history.py`, `history_ui.py` | Bind evidence/history to the correct variant and distinguish levels of verification |
| Crash reporting | `crash_monitor.py`, `crash_reports.py`, `crash_reporting.py`, `crash_details.py`, `crash_evidence.py` | Collect private evidence and produce sanitized public reports with coverage and candidate families |
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

See the [native implementation boundary and verification](ui-workflow/IMPLEMENTATION.md)
for the subprocess protocol, update-safe lifecycle and staged advanced-tool migration.


## Command launch and finite research owners

The existing command bridge provides JSON status and catalogue-profile launch:

```sh
PYTHONPATH=src python -m smr_launcher.cli status
PYTHONPATH=src python -m smr_launcher.cli play PROFILE_ID
```

CLI `play` and both app interfaces call `LauncherApplication.play`. A successful
command reports observed process startup; it does not establish a ready game
menu, gameplay, or save/reopen acceptance. The CLI does not own a research
admission, observer, restoration deadline, or subsequent input session.

A finite research owner uses that same application service in process, with an
explicit launch callback and mutation guard. It may supply a distinct registered
`profile_copy` only when it is absent from the catalogue and its assets match
both the authorized catalogue profile and that map's prepared assets. Launch
settings and saves belong to the copy;
the source catalogue record is not replaced or fabricated. Guarded play refuses
pending removal/activation recovery and takes application/profile locks without
waiting. The guard covers profile exchange and launch-settings writes; the launch
callback retains the owner's final cancellation fence, bounded child tracking,
and cleanup. It runs outside the mutation guard, under the application/profile
locks, so a blocking child wait does not hold or reacquire the owner's state lock.
These hooks do not grant runtime admission or substitute for fresh ownership and
preservation checks. Ordinary CLI/UI calls retain catalogue-only behavior.
The private agent entry also pins the reviewed calibration source and maintained
application module path before queue or owner construction. Re-pinning an older
low-level launch composition cannot select it for a successor.

See [AI game testing](AI_GAME_TESTING.md) for the finite visual execution and
exact-save/reopen procedure, including its current empirical verification limits.

## Offline recipe batch planning

`LauncherApplication.plan_recipe_batch(requests)` accepts 1–32 explicit dictionaries
containing only `profile_id` and `recipe_id`. The command bridge exposes the same API:

```sh
PYTHONPATH=src python -m smr_launcher.cli plan-recipes \
  --request PROFILE_ID RECIPE_ID --request OTHER_PROFILE_ID OTHER_RECIPE_ID
```

The JSON envelope stays unchanged. Its data contains ordered `supported`/`refused`
jobs and `all_supported`; a refused job is never a planned success. Later duplicate
profile/recipe pairs are refused with the first index in `duplicate_of`. Invalid
batch shapes fail before locking. The planner resolves current originals through
the existing bundled resolver under one application lock. Pending removal refuses
without recovery. It does not create editions, recover/switch profiles, activate,
probe game processes, or write saves, registrations or evidence. Lock coordination
may create the library directory and `.application.lock`.

Supported reports contain exact original/archive/build, recipe/compiler/source/stock
bindings, proposed relative paths, patch digests and donor member identities without
XML or asset bytes. Donor archive identity remains the existing resolver's catalogue
claim (`archive_bytes_verified: false`); member bytes are verified. This does not
strengthen donor selection or conditional engine assumptions. Reports are snapshots,
not execution tickets: later preparation must re-resolve every existing guard.
Preparation support establishes no loading, feature, repair, gameplay or save acceptance.

## Explicit offline recipe preparation

`LauncherApplication.prepare_recipe_batch(requests)` and `prepare-recipes` use the
same 1–32 explicit requests as the planner. Repeat `--request PROFILE_ID RECIPE_ID`
for each job. Any unsupported or duplicate job prevents all execution; malformed
request shapes raise before locking. Supported jobs then resolve their current
original, installation and recipe context again under the application lock.
Planning reports are snapshots and cannot be submitted as execution tickets.

The batch disables removal recovery and asks the activation owner to refuse pending
activation journals under its own lock, including dangling journal links. Existing
single-edition APIs retain their default recovery behavior. Each successful job
publishes its independent edition through the existing preparation machinery and
validates its receipt, registered output context and prepared output before reporting
`prepared`. Results include the actual edition record, receipt and binding, plus
whether an intact edition was reused. Initial planning fields are labelled `preflight`;
successful jobs contain the freshly bound receipt context. A final audit rechecks all
successful publications under the application lock, including their unique current
original and source identity. It marks stale outcomes `failed` with `audit_failed`
while retaining the actual edition identity. A failed execution reports `failed` and stops;
remaining jobs are `not_attempted`. Preflight refusals remain `refused`.

`all_prepared` and `partial` describe batch completion. Earlier successful editions
and saves remain present after a later failure. A failing single-job transaction may
retain output for inspection; no batch rollback, retry, activation, launch or recovery
is performed. Preparation proves storage outcomes only, never Verified status,
engine compatibility or gameplay. The JSON command envelope reports transport/API
errors separately from per-job outcomes; callers must inspect `all_prepared`.
