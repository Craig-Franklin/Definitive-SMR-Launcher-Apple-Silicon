# Launcher performance implementation and validation

11 October 2026. Implements the five recommendations in [PERFORMANCE_ANALYSIS.md](PERFORMANCE_ANALYSIS.md) for [Performance project #3](https://github.com/users/Craig-Franklin/projects/3), issues [#198](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/198), [#199](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/199), [#200](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/200), [#201](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/201), and [#202](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/202).

## Resulting behavior

The native loading window enters Tk's loop before discovery, executable identity hashing and monitor construction. Bootstrap returns services/data through a closeable mailbox; all widgets, preferences and monitor handover remain on the UI thread. Initialization temporarily polls results every 10 ms, returning to the existing 100 ms cadence afterward. Closing disposes queued and late monitor services. The loading view is independently authored; no upstream splash code or artwork was copied.

The map gallery builds an immutable presentation snapshot in one background worker, with only the latest replacement pending. It rereads current catalogue, metadata, artwork and contextual verification for each presentation. Tk destroys and builds cards in batches of at most eight / 8 ms; generation checks reject obsolete results. Details and Play still obtain current verification independently. Optional artwork is limited to 1 MiB and 360 pixels per PNG, and 32 MiB total snapshot bytes; unsafe, oversized or budget-excluded artwork uses a placeholder. Selection, scroll, filters, sort, localization and native bindings are preserved.

A validated saved online catalogue opens without networking and displays its saved date/source. Refresh is explicit. Fetched IA records publish before cache persistence, provenance matching and optional community refresh, which use independent bounded maintenance lanes. Errors remain visible. Cache inputs have bounded bytes/counts, exact schema/source checks and descriptor-relative no-follow reads. Atomic writes guarantee old-or-complete-new bytes; a directory-fsync failure after rename can leave complete new bytes with uncertain durability.

Archive provenance now indexes remote identities, hashes each distinct preserved archive at most once per operation, and commits a changed batch once under the existing lock. No persistent digest cache is added. Descriptor-relative regular-file reads, before/after identities and a final check reject changed or linked bytes. Duplicate remote identities retain first-match behavior. Inspection failures abort the uncommitted batch, rather than leaving earlier partial matches; pre-rename persistence failures preserve old catalogue bytes. A post-rename fsync failure has the same complete-new/uncertain-durability boundary as above. Cooperative locking cannot eliminate the final check-to-commit race with an external noncooperating writer.

Download producers coalesce intermediate byte counts per map. Discrete lifecycle events use a bounded 4,096-entry queue with backpressure, and closing wakes waiting producers. UI drains process at most 64 events with a 4 ms deadline, use indexed rows and write each affected visible row once per batch. Terminal events, retry boundaries, hidden-row state, activity logs and completion ordering are retained.

## Comparable measurements

These are shared-host synthetic workloads, not game compatibility or real-library benchmarks. GUI measurements use arm64 CPython 3.12.15 / Tk 9.1 on macOS 27.2; provenance uses arm64 CPython 3.9.6. Each comparison uses the same interpreter and fixture. OS caches, thermal state and CPU contention were not controlled. The paired reruns below replace the original analysis numbers for calculating improvements; differing sampling methods and host conditions explain why the baseline values differ.

Native GUI samples use a fresh process and temporary HOME each time, exclude Python imports and packaged bootloader work, and refuse network/subprocess operations. Window Map events measure native mapping, not compositor first paint. Welcome-ready means completion of the welcome view after a synthetic discovery failure. Gallery completion means widgets and idle layout completed, not physical input response. A 10 ms timer measures event-loop gaps, including the final unfinished interval.

| Native Tk workload, n=5 | Before median ms | After median ms | Interpretation |
| --- | ---: | ---: | --- |
| Startup window mapped, injected 500 ms discovery | 766.630 | 330.398 | 57% earlier feedback; imports excluded |
| Startup welcome-ready, same injection | 762.870 | 757.759 | 0.7% faster; within 10% target |
| Startup maximum post-map timer gap | 103.531 max | 13.468 max | <=50 ms target met on this fixture |
| 250-map gallery initial call | 861.001 | 7.299 | <=100 ms target met |
| First gallery card observed | 865.030 | 158.707 | Includes snapshot work and first batch |
| All gallery cards / idle layout | 873.810 | 592.754 | 32% lower elapsed time |
| Gallery maximum timer gap | 925.949 max | 44.710 max | <=50 ms target met, no artwork/evidence |

Startup ranges before/after: mapped 754.259–817.482 / 325.679–343.732 ms; ready 749.542–788.740 / 754.140–772.545 ms. Gallery ranges before/after: initial call 845.384–914.889 / 3.450–10.054 ms; all cards 856.389–925.935 / 585.910–607.247 ms. After measurements use integrated production-source commit `0cc4d80070706b140120e23c40cb49657cb95a29`; before uses `0fdd684`. The final gallery harness supplies the executable identity required by integrated map warnings and valid 64-hex variant keys. Preflight asserts absent gameplay verification; final snapshots assert normal Not verified labels with empty notices. Earlier gallery timings using the incomplete fake installation were discarded and both sources rerun. Startup also samples the final unfinished interval immediately before quitting; both startup sources were rerun after that instrumentation correction, and the monitor smoke samples its final handover tail. Final documentation and benchmark corrections do not change production code.

Provenance timings use 4 KiB opaque files, fresh synthetic catalogue resets outside each timed operation, real locking/durable writes, n=3. The retained old algorithm and new algorithm are measured separately; old always runs first, so cache/order bias remains. Counters run separately from timings. Original file bytes and decoded matching outputs are checked.

| Provenance workload | Before min / median / max ms | After min / median / max ms | Hashes; durable writes |
| --- | --- | --- | --- |
| 25 unique-size archives | 18.650 / 18.962 / 19.887 | 6.517 / 6.772 / 7.050 | 25→25; 25→1 |
| 25 same-size archives | 37.422 / 38.871 / 39.752 | 6.524 / 6.551 / 6.787 | 325→25; 25→1 |
| 250 unique-size archives | 957.503 / 961.139 / 1018.280 | 62.998 / 63.334 / 64.251 | 250→250; 250→1 |
| 250 same-size archives | 2884.821 / 2902.246 / 2963.556 | 63.264 / 64.043 / 64.203 | 31,375→250; 250→1 |

The 250-map medians meet the <=200 / <=300 ms targets: 15.2x and 45.3x faster on these fixtures. This establishes algorithmic improvement, not throughput for large real archives. Unchanged already-sourced operations perform no hash/write.

The progress comparison extracts the exact original method from baseline `0fdd684` and runs it against the current method using a fake Treeview with 2,000 rows / 10,000 events, n=11. This paired workload sends five events to each row; the original analysis sent 10,000 events to the final row, so it is a different stress distribution. Producer prefill is excluded from drain timing and measured separately. Sum-processing excludes continuation scheduling and elapsed wall time.

| Source/fake measurement | Before min / median / max ms | After min / median / max ms |
| --- | --- | --- |
| Sum of progress callbacks | 151.174 / 152.020 / 153.576 | 1.380 / 1.425 / 1.659 |
| Maximum individual callback | 151.174 / 152.020 / 153.576 | 0.048 / 0.069 / 0.295 |
| Producer enqueue work | 3.369 / 3.520 / 9.282 | 3.185 / 3.245 / 3.338 |

Retained progress events and fake row writes both fall from 10,000 to 2,000. The <30 ms sum-processing and <=16 ms callback targets pass for this fake fixture. An additional single native smoke uses a real 2,000-row Treeview and active producer of 10,000 Downloading events. It observed 155.834 ms elapsed to final counts, 4.311 ms summed callback processing, 0.211 ms maximum callback and 12.902 ms maximum 10 ms timer gap. Producer time was 5.160 ms; peak queued events and row writes were both 2,000, and all final counts were correct. Initial row construction is excluded. This single sample supports the <=50 ms native burst sentinel target for intermediate byte progress; terminal-event log/fsync bursts, actual downloading and physical input remain unmeasured.

A 221-record / 22,585-byte saved catalogue read and validation takes 0.474 / 0.498 / 0.546 ms, n=11, with zero network calls. This meets the local data-processing <=100 ms target; native row construction is excluded. Tests block provenance/community work and establish that fresh IA rows publish independently. A separate 221-record fake callback fixture deliberately waits a 100 ms normal poll phase after fetch and blocks cache persistence, provenance and community tasks. Fresh rows publish in 100.999 / 103.943 / 105.490 ms, n=11, meeting the <=150 ms target while all maintenance remains blocked. Native row construction and Internet latency are excluded. Original IA network median was 667.775 ms versus 1.688 ms payload parsing; no claim is made that caching accelerates the remote service itself.

## Validation and independent review

The baseline had 746 passing tests. Added regression coverage includes UI-thread handover, startup failures/late close, cache validation and atomicity, blocked optional maintenance and stale completions, progress backpressure/terminal ordering/retries/cancel, gallery generation/batching/resume, current metadata/art replacement and fresh details/play verification, secure archive identity and lock serialization. Published main `465bd9a` is integrated to retain ResourceGuard, MapIssueStore and persistent Known issue behavior. The final integrated suite passed **817 tests in 48.689 seconds** on CPython 3.12.15. Focused integration checks include 41 GUI, 11 scenario-view, 11 game-health and 9 map-issue tests. `git diff --check` passed. The arm64 PyInstaller app passed deep strict ad hoc signature verification. The development DMG built, passed checksum verification, and its mounted contents passed the repository's bundle/image verification. These are local development artifacts, not notarized releases.

The coordinator visually inspected the rebuilt package's welcome screen using the exact owned test PID in an empty temporary HOME, then verified the executable identity before terminating that test process. The temporary HOME's symlinked parent caused secure discovery to refuse the Steam library path; the welcome/error flow rendered correctly. No discovery/readiness latency is inferred from that screenshot. The installed launcher was untouched. Native source fixtures separately measured startup and gallery performance.

Independent ordinary Sol/high review found and routed fixes for aggregate artwork/decode bounds, cache post-rename guarantee wording/testing, and published-main reconciliation. Earlier evolving-source observations also led to preserving image references, resuming canceled gallery work after unrelated operations, and correcting the benchmark's final timer interval. No Astra advisor was needed. Final review checks the integrated source, hashes and claims; reviewer has no implementation authorship or native GUI ownership.

A single successful-bootstrap native smoke injects a 500 ms fake monitor constructor: visible mapping was observed at 341.378 ms, UI handover at 766.679 ms and maximum post-map timer gap 14.140 ms. A separate close-during-construction smoke records one late monitor disposal and zero UI bootstrap applications after close. Both use injected app/monitor services and deny external operations; no real monitor/game starts. The close check is lifecycle evidence only, with no mapping/timing claim.

## Reproduction and remaining limits

`scripts/benchmark_provenance.py` runs the before/after archive comparison. `python3.12 scripts/benchmark_ui_work.py` runs the paired fake progress/cache/publication comparison and prints JSON; it requires the pinned baseline in git history and the included test fixture module. On an arm64 macOS Python with Tk, `python3.12 scripts/benchmark_gui.py --mode startup --samples 5` and `--mode gallery --maps 250 --samples 5` run opt-in isolated native fixtures. `--source-root` accepts an extracted baseline tree containing `src` for the same harness. These scripts never launch a game or open real profiles. Raw local diagnostics are excluded from source control.

Representative installed/packaged cold and warm startup, Gatekeeper first launch, populated verification/art libraries, native terminal-event/log-persistence bursts, actual user input feel and real archive throughput remain unmeasured. The successful-bootstrap monitor check and intermediate-byte-progress native check above are single injected smoke samples, not representative timing distributions. Loading feedback does not eliminate discovery work. Batching can increase total completion time under host contention; it bounds the intended per-batch work, not every Tk or filesystem operation. Gallery display snapshots are momentary observations and never authorize Play. No game, live profile/save, installed launcher, release or main-branch operation was used for validation.
