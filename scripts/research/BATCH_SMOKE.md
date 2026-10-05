# Sequential map load smoke tests

`batch_smoke.py` is an experimental orchestration script. Its default action only
prints a plan. It requires a separate GUI driver to establish positive evidence
that the selected scenario reached gameplay. It does not mark maps Verified and
does not test saves, autosaves, trains or campaign reliability.

The supplied `orca_load_driver.py` uses the visible desktop. It is **not a
background/headless test**, and it has not yet completed an end-to-end calibration
through this runner. A runtime invocation requires `--run --allow-ui-control`.
Do not use the keyboard/mouse for other work during a foreground test.

## Plan first

Use the project virtual environment. Compile `read_screen_text.swift` into a
private output directory before using the reference driver. Substitute the actual
absolute paths and an exact imported variant ID below:

```sh
.venv/bin/python scripts/research/batch_smoke.py \
  --driver '/absolute/checkout/.venv/bin/python /absolute/checkout/scripts/research/orca_load_driver.py --ocr /absolute/private/read-screen-text' \
  --output /absolute/private/smoke-results \
  --variant EXACT_VARIANT_ID
```

This reads the catalogue and prepared files, fingerprints inputs and prints the
selected scenarios. It does not create an output directory, switch profiles or
launch the game. Omit `--variant` to inspect the full plan. Each scenario in a
package is a separate job; a single successful scenario does not cover its siblings.

If the scenario XML contains an unresolved localization key, a single-scenario
test may use `--expected-title 'Exact displayed English title'`. The GUI driver
must positively match that title before loading. It must fail rather than choosing
a different map when the expected title is absent.
For a package containing several scenarios, add
`--scenario UserMaps/Map/RRT_Scenario_User_Map.xml` with the exact relative path
shown by the plan to select one calibration scenario.

## Run one calibration

Add `--run --allow-ui-control` to a plan containing exactly one scenario. The
runner prepares an independent diagnostic copy, starts the game, invokes the
driver and stops immediately after successful load evidence. Default bounds are
90 seconds and 6 GiB physical footprint, sampled approximately every 150 ms while
the driver operates. `--timeout` and `--memory-gib` change those limits.

The executable path, PID and microsecond process birth time must match before
signals are sent. An additional or unidentified Railroads instance causes a safety
stop. This is a watchdog rather than an OS allocation limit; memory can grow
between observations.

The first test must produce a `loaded` result and verified restoration. Only then
may a matching runner/driver/game use `--run --allow-ui-control --batch` for several
scenarios. Execution remains sequential. A successful shell exit or a process
that merely stays alive is never sufficient to pass.

## What is isolated and preserved

- The entire original **WinDeveloper profile** is independently copied and hashed
  before testing, including map assets, settings and saved games.
- Each diagnostic generation uses a unique identity, copies one prepared map and
  starts with an empty `Saves` directory.
- Only that diagnostic copy changes `SkipOpeningMovies=1`, `LastNumAIPlayers=0`,
  `LastScenarioName`, `FullScreen=0` and `Quickstart=0`.
- After every test the original profile is restored and its full file/directory
  manifest must match. A changed original is preserved for inspection rather than
  overwritten from backup.
- Diagnostic saved games are independently retained before duplicate diagnostic
  assets are cleaned up. Source archives, prepared maps and gameplay verification
  records are not changed.
- Feral can still update logs, caches or preferences **outside WinDeveloper** during
  a normal launch. This runner does not claim to restore the entire support root or
  override macOS preference services.

The application lock serializes launcher mutations for the batch. Direct launches
from Steam/Finder do not participate in that lock; do not start another game while
the runner is active. Only a stopped and identified process allows restoration.

## Driver contract

The runner executes a supplied command directly, without a shell, appending:

```text
--request /absolute/private/run/driver-request.json
--result /absolute/private/run/driver-result.json
```

Request schema 1 contains:

| Field | Meaning |
| --- | --- |
| `pid`, `birth_us`, `executable` | Exact process selected by the runner |
| `input_identity` | Exact test-input fingerprint |
| `scenario` | Scenario path relative to the prepared profile |
| `scenario_name` | Scenario XML basename |
| `scenario_title`, `expected_title` | Expected displayed title; may require localization resolution |
| `mode` | `custom` for imported-map jobs |
| `output_directory` | Private directory for this attempt |
| `deadline` | Absolute Unix time in seconds |

The driver owns GUI interaction only. It must not launch or terminate games,
switch profiles, modify map files or load personal saves. Its result must echo
`schema`, `pid`, `birth_us`, `input_identity` and `scenario_name`.

A successful result also requires `status: "loaded"`, `loaded_scene: true`, a
nonempty `assertion`, and an absolute `screenshot` PNG path directly inside this
attempt's directory with matching `screenshot_sha256`. The game must still be the
same live process when the runner accepts the result. The driver should explain
which independent visual signals establish the loaded scene. Failure is
`status: "automation_failed"` with a reason. The watchdog independently decides
process exit, memory limit and timeout outcomes.

Driver commands run in a separate process group. On abort, the runner kills the
group before reaping its leader and verifies that the group has disappeared, so
outstanding OCR/GUI subprocesses cannot continue into restoration. If the leader
was already reaped but a group remains, it refuses restoration rather than risk
signalling a reused group identity.

## Results and resume

`results.jsonl` records exact inputs, scenario, outcome, evidence location, peak
physical footprint and restoration status. `started_at` is Unix time in seconds;
`elapsed_seconds` measures preparation, launch, monitoring and restoration together
(including diagnostic cleanup). Progress output includes elapsed seconds. Outcomes are:

- `loaded`: positive driver evidence accepted while the game was alive.
- `crash_or_exit`: the process disappeared before proof; LaunchServices does not
  provide this runner with the game's exit status, so a crash cannot be inferred
  solely from disappearance.
- `memory_limit`, `timeout`: a configured bound was reached.
- `automation_failed`: the driver could not establish the required evidence.
- `safety_stop`: an identity, integrity or orchestration condition failed; the
  batch stops.

Only terminal results with verified restoration are skipped on resume. The key
includes the full prepared manifest, archive/variant/game identity, scenario,
runner source hash, driver command and file hashes, title, options and limits.
`--retry` retests completed jobs. Changing those inputs produces new jobs. A torn
JSONL record requires inspection; the script will not silently truncate evidence.

Each session retains `original-profile/` and `recovery.json`. If the process or
machine stops during a test, an unfinished recovery record prevents another batch
from taking over that output directory. Once Railroads is closed, inspect a
recovery plan, then explicitly apply it:

```sh
.venv/bin/python scripts/research/batch_smoke.py \
  --recover-session /absolute/private/smoke-results/session-IDENTITY

.venv/bin/python scripts/research/batch_smoke.py --run \
  --recover-session /absolute/private/smoke-results/session-IDENTITY
```

Recovery checks the library/game binding, retained snapshot, original profile
manifest and current profile identity. It refuses to override an unrelated active
profile or overwrite a changed original. Retain recovery evidence for inspection
if any check fails.

If driver cleanup fails, the runner still attempts to stop the exact test game,
but keeps the original profile inactive and records the driver process group in
the recovery journal. Explicit recovery also requires that group to be absent.
It never signals a persisted group identifier, which could have been reused.

## Validation status

Synthetic tests cover positive-evidence requirements, hash/path rejection,
process exit, PID reuse, memory/time limits, no-proof driver exits, result caching,
diagnostic settings and restoring original settings/saves after failure. A
read-only macOS ABI test reads the test runner's own libproc birth/footprint fields.
These tests do not establish that the GUI driver can reliably load a real map.
