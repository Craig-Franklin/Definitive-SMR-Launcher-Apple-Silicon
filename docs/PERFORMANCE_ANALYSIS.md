# Launcher performance analysis and implementation plan

Analysis date: 11 October 2026. Starting source: `0fdd684c591f23b6a85ccf89af62fd2bfef87c70`, app source version 0.3.0. Scope: [analysis issue #197](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/197), [Performance project #3](https://github.com/users/Craig-Franklin/projects/3). Craig authorized implementation after this analysis through an independently reviewed PR to the personal fork's main. No merge, release or game launch is part of this work.

## Method and evidence limits

Measurements used a shared Apple Silicon host running macOS 27.2. Startup/import/progress and coordinator native Tk measurements used arm64 Homebrew CPython 3.12.15 with Tk 9.1. The library worker's catalogue/metadata/filter/provenance and IA network/parse measurements used arm64 system CPython 3.9.6. Each before/after comparison uses the same interpreter; cross-interpreter timings are not compared as speedups. CPU/power/thermal state was not controlled. All local benchmarks used synthetic temporary libraries or isolated HOME directories; none used game bytes, commercial maps, actual profiles or saves. OS caches were not flushed. Source timings, fake-widget timings, native Tk timings and packaged rendering observations are distinct below. Numbers are milliseconds and min/median/max unless stated otherwise. Raw diagnostics stay local; published benchmark code and final results accompany implementation.

Bytecode-cold imports used a fresh empty private cache for each process (n=5); warm imports used a primed private cache and fresh processes (n=11). These are not disk-cold launches or first-download Gatekeeper measurements. Local metadata fixtures used 25/250 maps with no icons or gameplay evidence, about 44/442 KiB of mapInfo text. Provenance fixtures used distinct opaque 4 KiB archives, either unique sizes or all the same size. These stress algorithmic scaling, not real map throughput. Uninstrumented provenance timings reset the synthetic catalogue outside each timed call, n=3. Already-sourced repeats, metadata and filters used n=7. Progress bursts used a fake Treeview and prefilled queue, n=11, without actual downloads. Native gallery rebuilds used actual Tk widgets, cold metadata per call, no icons/evidence, n=5.

Baseline validation: 746 synthetic tests passed in 48.978 seconds. The baseline arm64 PyInstaller app built and passed local ad hoc signature checks. A packaged welcome window was visually observed after a nominal two-second wait in an empty temporary HOME. That observation proves rendering, not first-paint or readiness latency. Representative packaged cold/warm startup, populated verification stores, large real archive throughput, actual input feel and upstream packaged splash duration remain measurement gaps. Repeat before/after source and synthetic native tests on the same fixtures; inspect a rebuilt isolated package before claiming packaged rendering. A sentinel scheduled every 10 ms will measure event-loop delays during injected work. No performance result establishes game compatibility.

## Startup and browsing paths

`scripts/build_macos.sh` builds a windowed arm64 **onedir** app. `scripts/launcher_entry.py` imports GUI and updater before selecting helper/GUI mode. `gui.LauncherWindow.__init__` creates Tk, loads preferences/community cache, builds layout, synchronously calls `LauncherApplication.discover`, constructs/starts `CrashMonitor`, checks staged updates and builds the initial view before `run()` enters mainloop. `SteamInstallation.discover` validates manifest/bundle/profile paths and hashes the entire executable; that hash remains required. Monitor construction reads up to 4,096 retained sessions; actual scanning/reporting then runs on its monitor thread. Startup does not automatically fetch IA unless its collection workflow is selected. Automatic app-update network work is deferred through `_submit`.

`_maps_view` and `_render_gallery` read catalogue/metadata, inspect verification and decode art on Tk's thread, recreating five widgets and seventeen bindings per map. `_filtered` is already cheap with warm metadata. Gameplay verification may read contextual resources and must remain fresh for details/play; no generic persistent verification cache is proposed. Collection refresh currently runs IA fetch/parse, `match_collection_sources`, then optional community fetch serially before publishing records through `_poll`. Network runs off Tk but delays list availability and holds global busy. Download reuse/import verification remains independent and required.

Upstream is pinned at [`3973e58e1027bb28e2db1718373f0faaab759a3b`](https://github.com/ageekhere/Definitive-SMR-Launcher/tree/3973e58e1027bb28e2db1718373f0faaab759a3b). Its Windows onefile build selects a splash JPEG; `main.py` calls `create_splash`, later closes `pyi_splash` and schedules post-load work before mainloop. This is source comparison. PyInstaller's [versioned splash documentation](https://pyinstaller.org/en/v6.22.3/usage.html#splash-screen-experimental) excludes macOS because the feature uses a secondary thread. The complete pinned upstream tree has no license/licence/copying/notice path, GitHub reports no license, and this fork's MIT grant expressly excludes inherited material. Reuse permission is unverified. Implement a fresh native loading shell; copy no upstream code or art.

## Baselines

| Operation | Workload / samples | Min / median / max ms |
| --- | --- | --- |
| Entry imports | bytecode cold, n=5 | 257.278 / 260.531 / 275.638 |
| Entry imports | bytecode warm, n=11 | 52.979 / 54.077 / 57.123 |
| Steam discovery | synthetic 256 MiB executable, n=11 | 92.084 / 93.983 / 95.863 |
| Monitor constructor only | 4,096 synthetic sessions, n=11 | 252.543 / 253.293 / 260.989 |
| Local catalogue | 250 maps, n=7 | 0.829 / 0.884 / 0.966 |
| Metadata cold application cache | 250 maps, n=7 | 55.925 / 57.870 / 60.480 |
| Warm filtering | 250 maps, n=7 | 1.072 / 1.096 / 1.121 |
| Native gallery `_show('maps')` | 25 maps, n=5 | 78.667 / 88.061 / 121.978 |
| Native gallery `_show('maps')` | 250 maps, n=5 | 671.508 / 722.263 / 736.632 |
| First provenance reconciliation | 25 unique-size archives, n=3 | 16.009 / 16.923 / 17.451 |
| First provenance reconciliation | 25 same-size archives, n=3 | 33.403 / 33.780 / 35.462 |
| First provenance reconciliation | 250 unique-size archives, n=3 | 872.062 / 884.783 / 887.630 |
| First provenance reconciliation | 250 same-size archives, n=3 | 2689.598 / 2714.313 / 2751.651 |
| Already-sourced reconciliation | 250 maps, n=7 (counter run) | 0.984 / 1.162 / 1.440 |
| Progress drain, fake Treeview | 2,000 rows / 10,000 events, n=11 | 299.453 / 302.050 / 307.838 |
| IA metadata HTTP 200 network | 58,618 bytes / 225 files / 221 accepted maps, n=3 | 641.830 / 667.775 / 710.958 |
| IA payload parse | same responses, n=3 | 1.682 / 1.688 / 2.189 |

IA network timings include connect/TLS/headers/body from one location/time, with bounded requests and no archive downloads; they are not a service SLA. Community latency was not measured. Summing phase medians would be an estimate, not measured end-to-end startup/refresh. The instrumented 250-map same-size reconciliation made 31,375 hash attempts and 250 durable catalogue writes; already-sourced repeats made zero of both. Native gallery timings include rebuild/destruction/layout but omit populated verification and icons. Idle `_poll` Python work was under 1 microsecond/call with fakes; changing its cadence is not selected without energy/native evidence.

## Exactly five ranked recommendations

### 1. Show startup feedback and move discovery/bootstrap off Tk

**Problem/evidence:** `gui.LauncherWindow.__init__`, `steam.SteamInstallation.discover`, `crash_monitor.CrashMonitor.__init__` perform required hash/history IO before the loop is responsive. Measured isolated costs are roughly 94 ms for 256 MiB discovery and 253 ms for maximum session history. Confidence high in call placement, medium in representative impact; actual installed workload is unmeasured.

**Approach/files:** independently authored loading view in `gui.py`; enter the loop before background discovery and monitor construction; return plain data, apply on Tk's thread. Keep full identity validation, crash monitoring/publication preferences, safe-close and busy gating. Conditional entry imports may keep helper mode independent as a small adjunct. Loading feedback improves perceived responsiveness; it does not itself reduce total readiness time.

**Effort/risk/dependencies:** medium; one GUI editor; monitor ownership/late completion are the main risks. No game controls become available before validated app/monitor completion. Depends on gallery slice for large-library readiness.

**Acceptance:** injected 500 ms discovery/monitor delay leaves event-loop sentinel max gap <=50 ms after the shell maps; missing-game/error paths reach welcome; close during initialization disposes late services without UI callbacks. Separately record first-feedback and ready times; no claim of actual startup speedup without comparable timings. Proposed issue: **[Performance] Show early loading feedback and defer startup IO**.

### 2. Load library snapshots outside Tk and render cancellable batches

**Problem/evidence:** `gui._maps_view`, `_filtered`, `_render_gallery`, `_art` rebuild every card. Native 250-map call blocks ~722 ms with no art/evidence. Warm filtering is only ~1.1 ms. Confidence high in native rebuild cost; populated evidence/art impact remains unknown.

**Approach/files:** `gui.py` plus focused helper if useful: background immutable catalogue/metadata/verification presentation snapshot, then small time/count-limited card batches on Tk. Cache only safely keyed display metadata/art; explicitly refresh on library change, import/remove/evidence action and file replacement. Keep exact contextual checks for details/play. Prefer this bounded approach to broad virtualization. Preserve selection/scroll/search/sort and native bindings.

**Effort/risk/dependencies:** medium/high; single GUI editor; generations, stale results, image ownership and safe verification labels need tests. No Tk/PhotoImage access from background workers. Safe baseline library methods retain locking and validation.

**Acceptance:** 250-map native initial call returns <=100 ms; rendering yields between batches with <=50 ms sentinel-gap target on the no-art/evidence fixture; stale work after navigation/search/library switch/close is discarded. Metadata missing-to-present/replacement invalidates; details/play validate current state. Target total ready time no >10% regression on the same fixture, with any miss documented. Proposed issue: **[Performance] Keep map gallery responsive with snapshots and bounded rendering**.

### 3. Cache the online catalogue and publish before optional maintenance

**Problem/evidence:** `gui._refresh_collection` serially gates list publication on provenance and optional community fetch. IA network median ~668 ms versus parse ~1.7 ms; optimizing parsing cannot fix this. No first-load disk fallback. Confidence high in sequence/network sample, medium in real end-to-end gain.

**Approach/files:** `collection.py` safe bounded source/schema-validated disk catalogue cache; `gui.py` cached first display and independent staged results. Publish freshly fetched records promptly, keep provenance/optional community work asynchronous and errors visible. Assign GUI state only on Tk. Stale cached lists must be labelled and all downloads still validate current declared bytes and package semantics.

**Effort/risk/dependencies:** medium; cache source/schema/size/symlink checks, atomic writes, freshness/generation and shutdown; shares GUI owner and uses reconciliation slice. Avoid concurrent UI jobs sharing one ambiguous busy flag.

**Acceptance:** valid warm cache display requires zero network calls and <=100 ms local processing; fresh list published within 150 ms of fake IA completion while community/provenance are blocked; selection/current tab/download state preserved. Malformed/oversize/foreign/linked cache rejected; failed refresh retains labelled last-good list. No stale callback after close/library switch. Proposed issue: **[Performance] Cache and stage collection loading**.

### 4. Hash each preserved archive once and commit provenance once

**Problem/evidence:** `application.match_collection_sources`, `_matches_remote`, `_record_source` hash once per same-size candidate and rewrite the full catalogue per match. At 250 maps, 31,375 attempts/250 writes and 2.714-second median in the collision fixture. Confidence high; real map-size throughput is unknown.

**Approach/files:** `application.py` and provenance tests: `(size, sha1)` lookup, operation-local digest reuse for shared archives, collect updates under the existing application lock, one durable atomic catalogue replacement. No persistent hash cache. Preserve deterministic first remote for duplicate identities, source timestamps/labels, originals and already-sourced skipping. Errors abort the entire uncommitted batch; previously committed catalogue remains intact. This conservative change is documented because old behavior could retain partial matches.

**Effort/risk/dependencies:** medium; independent application owner; immutable content/race checks, interrupted persistence and concurrent mutation tests required. No profile or verification semantic change.

**Acceptance:** at most one hash per distinct candidate archive and at most one durable write for a changed batch; zero writes for unchanged runs. Target 250-map unique-size median <=200 ms, same-size <=300 ms, unchanged matches; test same-size mismatch, duplicate remotes, shared archives, missing/linked/changed bytes, failures and preservation. Proposed issue: **[Performance] Make archive provenance reconciliation linear and atomic**.

### 5. Coalesce progress and bound each UI drain

**Problem/evidence:** `gui._update_download_rows` drains until empty, scans every row per event and writes every intermediate count. Fake 2,000-row/10,000-event backlog takes ~302 ms; native costs excluded. Stress fixture is not an observed normal arrival rate. Confidence high in scaling defect.

**Approach/files:** `gui.py` or focused helper: producer-side coalescing of intermediate progress, preserve discrete lifecycle events/logging, indexed row lookup, event/time budgets and prompt continuation. Update each affected visible row/selected detail once per batch; retain hidden row state. No download verification/cancellation or crash-monitor cadence change.

**Effort/risk/dependencies:** small/medium; same GUI owner; ordering and terminal-state preservation need focused tests. Do not let old progress overwrite Imported/Failed/Canceled, or drop terminal activity transitions.

**Acceptance:** fake stress total processing median <30 ms (10x baseline), <=16 ms callback target; bounded retained intermediate state and one row write per affected map/batch. Terminal counts/details/logs remain correct through retries/cancel/navigation/close and active producer. Native sentinel target <=50 ms during injected burst. Proposed issue: **[Performance] Bound and coalesce download progress presentation**.

## Implementation order and validation

First implement recommendation 4 (small independent, strongest local algorithmic evidence) while the single GUI owner implements 1, then 3, 5, 2. This minimizes GUI ownership conflicts and provides reusable lifecycle handling before gallery work. The ranking describes expected user impact/frequency/confidence, not development order. The collection staging and reconciliation savings overlap: report list-availability latency separately from maintenance runtime, never add them as independent elapsed savings.

Create five issues in Project 3 and link their bounded Orca Tasks. Commit this analysis before implementation. Use synthetic cache invalidation, cross-thread, late-result/cancellation/error and preservation tests; compare identical before/after workloads with sample spread. Build/verify arm64 app and disk image, inspect isolated rendering, then independent Sol/high final-diff review covering correctness/security/preservation and measurement claims. Route review fixes to implementation owners, rerun changed behavior, reconcile published origin/main and observe PR CI. Publish audited source/docs/tests only, no raw local artifacts or assets. Record final results and limitations in `PERFORMANCE_RESULTS.md`.
