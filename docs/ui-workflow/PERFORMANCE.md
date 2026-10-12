# Native UI performance audit

Audited on 11 October 2026 against `52972b5723e8159fb2351fe98b5142fbdd21a27d` (0.19.4). This is a performance audit of the Mac SwiftUI/AppKit frontend and Python/Tk bridge. It does not score iOS, browser performance or gameplay. [Issue #218](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/218) remains the canonical performance work.

## Highest priority

1. **Recording one outcome rebuilds every map.** `LauncherModel.confirm()` called `refresh()`, which forces a complete snapshot, clears every detail cache and reads the selected map, before a second forced detail read. The user reported disabled/stuck UI after Record Confirmation. A read-only process check found the frontend idle and the backend CPU-heavy; a short private sample showed backend hashing. This supports expensive follow-up work, but does not prove which exact backend request was active. The narrow correction removes the full snapshot and second read, and updates the matching card summary from one fresh detail response. Recording validation remains unchanged.
2. **Routine refreshes perform full verification and artwork work.** `LauncherModel.refresh()` always requests `refresh: true`; `NativeLibrary.snapshot()` consequently rebuilds `gallery_snapshot()`. Previous read-only real-library evidence measured 236 entries in 23.717 seconds: verification 23.529 seconds and metadata 0.074 seconds. Nested timings must not be added. Reuse presentation across revisions with explicit freshness and targeted invalidation. Preserve fresh validation for guarded operations and evidence claims.
3. **Ordinary selection eagerly reads evidence.** `NativeLibrary.details()` loads and validates the complete history ledger, runs verification and calls `import_checks()`, even for Overview. `LauncherApplication.import_checks(refresh=False)` can hash the prepared asset tree when checking an existing matching report. Fetch detailed evidence when its section needs it, while retaining presentation with appropriate freshness. Actual selection/asset-read latency was not measured in this audit.
4. **All requests share one busy gate.** `LauncherModel.request()` waits in 20 ms increments for any request; the backend also dispatches one request only when its global busy flag clears. Grid selection and other controls use this same flag. Independent cached presentation should remain usable while reads run. Coalesce superseded or identical reads; retain ordering and validation for mutations. Synthetic concurrent loads of the same uncached detail produced two requests. An immediate preferences request behind a synthetic 250 ms request waited 311.91 ms; this is model/pipe evidence, not rendered latency.

## Further avoidable work

| Area | Source finding | Proportionate next change |
| --- | --- | --- |
| History summaries | `gallery_snapshot()` calls `summarize(all_history, context)` for each map; the reducer scans all events again. | Group already validated events by all exact map/build identities once per operation, preserving order and reducer semantics. |
| Warm native snapshots | Cached gallery items still cause every annotation to be read/validated and every package image to be base64-encoded. | Retain immutable native presentation and encoded artwork between revisions; update changed annotations and keep secure reads on refresh. |
| Python reply delivery | `_emit_native()` performs JSON encoding, a UTF-8 allocation, pipe writing and flush on Tk's completion callback. | Encode large read responses off Tk and use one ordered writer, preserving event order and shutdown/EOF behavior. |
| Read-only tools | `_native_workflow()` invalidates the whole snapshot even for speech, settings/help and opening advanced tools. Inherited `_submit()` also marks maps dirty. | Invalidate for actual completed library, annotation/history or issue changes. Keep explicit refresh. |
| Shared evidence stores | Per-map verification rereads issue/evidence/legacy stores, sometimes repeatedly for the same map. | Reuse validated operation-local inputs where semantics permit. Presentation reuse is not current verification authority. |
| Summary projection | `asdict(summary)` recursively copies `events` before the native snapshot discards that field. | Project scalar summary fields before recursive conversion. |
| Main-actor search/sort | `visible` rebuilds search strings and sorts for each getter access. Library empty checks, grid iteration and selected-map visibility can request it three times per view evaluation. | Compute once per presentation/query/filter/sort revision. Do not make status or draft edits redo unchanged search work. |
| Images | `MapItem.init()` constructs two package-image objects when no override exists. Draft preview rebuilds base64/NSImage inside `artwork()`. `chooseImage()` reads up to 8 MiB and creates the thumbnail/PNG on the main actor. | Reuse package artwork, prepare draft artwork once and move bounded file conversion off the UI thread. Keep size/type checks and prior-draft retention. |
| Preferences | Each preferences request enumerates system voices despite an explicit Refresh Voices control; configured timeout is 15 seconds. | Reuse the last voice list until explicit refresh. Timeout is a bound, not observed latency. |
| Delivery cadence | Native requests start on a 25 ms tick, but inherited worker results normally drain on a 100 ms poll. | Use the existing shorter drain interval while a native request is in flight. |
| Long history | Map history uses an eager `VStack`/`ForEach` inside its scroll view. | Use lazy rows if a long-history rendering baseline demonstrates a problem. No current frame timing proves this is a user-visible bottleneck. |

## Synthetic measurements and limits

Local native benchmarks used optimized Swift 5 language mode with Apple Swift 6.4, arm64 macOS 27.2. They ran at reduced scheduling priority on a non-idle host, with no windows, launcher service, game actions or personal data. Thirty timed samples followed one warm-up per search scenario. Measurements are model/CPU timings, not viewport timings or the issue's rendered p95 acceptance target.

| Synthetic operation | Result |
| --- | --- |
| 250 maps, 128-character description, one matching search/sort | p95 0.68 ms |
| 1,000 maps, 128-character description, three repeated matching searches/sorts | p95 7.87 ms |
| 1,000 maps, 4,096-character description, one no-match search | p95 13.04 ms |
| Same long-description search computed three times | p95 39.09 ms |
| 360px generated PNG: draft base64/NSImage construction | p95 0.29 ms |
| Same generated PNG: thumbnail creation plus PNG encoding | p95 10.68 ms; excludes file access and larger images |
| 250 image-bearing MapItem constructions, 20 samples | p95 119.31 ms; model work already runs off the main actor |

Separate Python in-memory benchmarks used fictional event dictionaries and mocked annotation reads. Each history approach had three timed samples; snapshot projection and JSON emission had three, and scalar/deep-copy comparison five. A fourth, untimed snapshot call supplies the reported total annotation-read count. These are diagnostic measurements with limited sample counts, not acceptance targets:

- 1,000 exact map contexts and 10,000 events: current repeated summary scan median 3,156.193 ms; grouping once then running the unchanged reducer median 13.103 ms. Complete outputs matched.
- 1,000 cached maps with 32 KiB art: native projection median 38.914 ms, excluding annotation disk access. Four projections still made 4,000 annotation-load calls.
- The resulting 44,864,826-byte reply: JSON/UTF-8/null-sink emission median 78.740 ms. Actual pipe backpressure and Swift processing are excluded.
- A summary with 10,000 events: recursive conversion then discarding events median 25.805 ms; selecting scalar fields first median 0.001 ms.

The local raw samples, UI state and diagnostic receipts stay private. Source and synthetic evidence do not establish packaged rendering, full game exit or gameplay acceptance. Ordinary map search already works from memory, the grid uses `LazyVGrid`, Collection uses `List`, Activity uses `LazyVStack`, image sizes are bounded, and the large-reply Swift decoder is off the main actor. Preserve those properties. No engine rule changed or new game-compatibility claim follows from this audit.


## Stable presentation correction for #218 and #219

The correction retains the native in-memory projection until explicit Refresh,
a library/build change, a monitor-history error change or an existing invalidation.
Refresh still rereads secure metadata, artwork, annotations and verification.
External file edits are discovered by Refresh; the library labels displayed summaries
as potentially stale. No disk startup cache was added. Operation validation continues
to use current records and the existing protected Python paths.

One annotation save now returns its validated, identity-bound presentation and changes
only that map. Its stored image still obeys the aggregate projection budget. One history
confirmation updates the affected summary through the existing fresh detail read. Other
cards, query, sort, selection, scroll memory and drafts remain in place. Failed reads
retain last good presentation, and changed-edition replies cannot replace it. Read-only
speech/settings/navigation no longer invalidate every map. The inherited submission
helper keeps conservative invalidation as its default for mutations.

The native model reuses search text and query/sort results until their inputs change.
Repeated detail reads coalesce by edition and library revision; a brief selection delay
avoids submitting superseded selections. Read requests do not own the model's mutation
busy flag, while the service keeps its existing serial dispatch and guarded operations.
Concurrent refresh requests coalesce, with a follow-up refresh retained when the library
changes during a read. Package artwork is constructed once when no personal override
exists. Selected-image reading, thumbnail creation and PNG encoding run off the main
actor; the draft retains one prepared preview and survives unsupported/oversized files.
Saving waits for that image preparation, and late work checks its map and draft identity.

The history gallery partitions already validated events once by all three exact map/build
identities before calling the unchanged reducer. Scalar summary projection excludes
history events before recursive conversion. Warm native snapshots reuse annotations and
encoded artwork; an annotation save rereads only that map's prior document. Explicit
Refresh and guarded mutations retain their secure reads and failures.

The following source/synthetic measurements compare `d8e3f755` with this correction on
an arm64 Mac16,8, macOS 27.2 (26B5101f), Apple Swift 6.4 in optimized Swift 5 language
mode with a macOS 13 target, and Python 3.12. Runs used reduced scheduling priority on
a non-idle host. Swift scenarios used one warm-up followed by 30 samples, alternating
the query and sort and reading the resulting array three times per sample. They measure
model computation, **not an updated viewport**.

| Model scenario | Before p95 | After p95 |
| --- | ---: | ---: |
| 250 maps, 128-character descriptions, matching queries | 2.09 ms | 0.71 ms |
| 1,000 maps, 128-character descriptions, matching queries | 8.15 ms | 2.35 ms |
| 250 maps, 4,096-character descriptions, no matching query | 12.60 ms | 4.06 ms |
| 1,000 maps, 4,096-character descriptions, no matching query | 48.01 ms | 15.95 ms |

Each Python scenario used 20 measured samples, with one warm-up for history. Generated
history events passed the existing schema and relation validators. Metadata, artwork,
verification and annotation reads were mocked; secure disk access, JSON encoding, pipe
backpressure and frontend processing are excluded. Full summary/projection outputs
matched before and after.

| Python scenario | Before median | After median |
| --- | ---: | ---: |
| 250 maps, 2,500 history events, full gallery | 174.67 ms | 4.58 ms |
| 1,000 maps, 10,000 history events, full gallery | 2,831.27 ms | 18.58 ms |
| 250 maps, warm native projection with 32 KiB art per map | 16.34 ms | <0.001 ms |
| 1,000 maps, warm native projection with 32 KiB art per map | 64.04 ms | <0.001 ms |

Over 20 warm native projections, annotation loads fell from 5,000 to zero at 250 maps
and from 20,000 to zero at 1,000 maps. Focused regressions also check zero repeated
query builds for unchanged navigation/status/drafts, one read for duplicate delayed
details, one affected annotation read, exact-build/history-order equivalence, and no
full snapshot after annotation/history confirmation. Retained search text has a memory
cost: the complete Swift benchmark's maximum RSS increased from 17,170,432 to
23,461,888 bytes. That is process-level synthetic peak memory, not a leak or production
memory-growth measurement.

The final large-response check used 1,000 fictional maps and 16 MiB fragmented text:
49 main-actor scheduling ticks, a maximum gap of 2.15 ms and about 95 ms total. This
single transport/model run and delayed-read interaction checks do not establish frame
latency. Full frontend compilation for arm64 macOS 13 passed without warnings.
No visible preview, real launcher service, installed-app replacement, game/profile/save
action or live collector ran. Packaged viewport p95, accessibility/scroll/focus behavior,
real-library refresh costs and #219 manual-action/visible workflow acceptance remain
open. Independent review and cross-lane integration belong to the coordinator.
Engine knowledge consultation is not applicable; no engine rule changed.
