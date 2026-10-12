# Native UI and workflow acceptance

This record covers the final acceptance work for Project 5, issues #218–#221,
after Craig selected and approved native Design B. The canonical issue comments
record the published revision and signed-package checks. Earlier implementation
and audit documents retain their historical evidence; this record supersedes
their lists of remaining UI acceptance work.

## Library and keyboard

Native search uses an AppKit search field so Command-F reliably returns to the
library and focuses its actual text receiver. Tab enters the grid; arrow keys
navigate the rendered columns, Return opens the inspector, and Back/Escape restores
the selected card's focus. Long-title cards align their artwork at the top.

The library remembers the first visible map identity across width changes rather
than restoring an obsolete pixel offset. Deferred scrolling checks the current
ordered identities, destination, mounted-view generation and request token both
before scrolling and on completion. Empty results invalidate old anchors. Query,
selection and map-keyed drafts stay independent of viewport position.

Root observed the 250-record fixture at 900 points in dark appearance, scrolled to
maps 035–039, changed to 1180 points, and returned to 900 points. Map 035 remained
at the top through both layouts. Preferences/Command-F restored search focus;
a retained note on map 000 survived navigation and resizing. Original focus and
anchor failures were reproduced before these corrections.

Warm performance measured the actual production library view in an isolated
NSHostingView with identity-bound generated artwork and temporary raster markers.
Timing starts at query/sort assignment, after the search debounce, and ends only
when forced AppKit rasterization contains the expected ordered first viewport and
matching artwork. Each scenario has two warm-ups and 20 measured samples.

| Width | Maps | Search p95 | Sort p95 | Target |
| --- | ---: | ---: | ---: | ---: |
| 1180 points | 250 | 66.14 ms | 66.23 ms | 250 ms |
| 1180 points | 1,000 | 67.45 ms | 67.16 ms | 250 ms |
| 900 points | 250 | 47.97 ms | 50.41 ms | 250 ms |
| 900 points | 1,000 | 50.08 ms | 51.26 ms | 250 ms |

All 160 measured changes passed identity/artwork checks, made one query rebuild
and zero injected backend calls/starts, and retained selection and another draft.
Separate production regressions establish zero repeated metadata/artwork reads
for unchanged projections, targeted annotation/history updates, coalesced reads,
last-good refresh retention, explicit invalidation and stale-result rejection.

RSS before/after 44 changes was 202.0/214.7 MB (250 maps) and 228.6/231.8 MB
(1,000 maps) at 1180 points; at 900 points it was 175.5/204.9 MB and
215.6/223.7 MB. Highest sampled RSS was 261.5 MB and 244.1 MB respectively.
This records bounded repeated-change behavior, not a leak-free lifetime claim.

Machine: Apple Silicon Mac16,8, 12 CPUs, 24 GiB RAM, macOS 27.2 (26B5101f),
Swift 6.4 in Swift 5 language mode, optimized arm64 macOS 13 target. These are
forced native raster-readiness measurements including capture/check overhead,
not compositor/physical-display timing or signed-package performance. The source
manifest is `77a1e2693c68d34c72a2daed23d4f8dec765cbaba00bae02272501966fdf7d25`;
the later drop-type-only correction does not change library rendering.

## Details and personal images

Three visible repeat tasks used fictional Known, Missing and Other maps. The
baseline was the first native editor's actual view at
`5bcc6c9a7037f70a49aa52a9cfc9f919857910ca`, with the same closed temporary
provider. It does not recreate that revision's entire historical backend.
The earlier Tk view at `5c1185b` had no personal annotation editor.

Each baseline task used Edit Details, Choose Local Image, select the generated
image, Open, and Save Details: five core actions, 15 total. Folder-location setup
was recorded separately and excluded from the repeat task. Each final task starts
at Overview and uses one local-file drag gesture and Save Details: two human
actions, six total, a 60% reduction. The automation required a separate explicit
pointer-release click after each drag; those three additional tool calls are
recorded separately and are not hidden as ordinary clicks or saves.

Each final save visibly returned to Overview with the selected image and
“Personal details saved. Original package preserved.” Known author/date/briefing
needed no retyping; missing fields remained Not provided. Dragging opens a draft;
it never saves automatically. SwiftUI now requests only the local-file URL
representation, preserving strict single-file validation instead of receiving an
implicitly converted PNG provider.

Malformed and oversized generated images selected through the native picker
produced the useful 8 MiB error and retained the previous image and note.
Keep Draft & Close/reopen retained that note. Use Package Image followed by Save
restored the package's missing-image presentation while retaining the note.
Compiled checks cover local/remote/multiple providers, changed selection/edition,
discarded/recreated drafts, reversed callbacks, newer picker/package choices,
explicit-only persistence and preservation of other drafts/history/library state.
Personal edits remain edition-bound and separate from package and evidence facts.

## Contextual reporting

The original Tk checkpoint form was visibly inspected at `5c1185b` with a known
synthetic session. Scenario, checkpoint, game and explicit confirmation required
four semantic decisions per checkpoint. Its child controls were unavailable to
the automation provider, so no completed historical pointer trace is claimed.
Independent source replay supports 16 decisions for four checkpoint assertions;
the 31-pointer-action baseline is source-derived only.

The final production view with a closed temporary ledger completed Played,
Saved, and Resumed with explicit Continued Play. These required 2, 2 and 3
semantic decisions, respectively: seven total for four assertions, a 56.25%
reduction. Ten core pointer actions completed the three submissions. Exact-save
selection required five separate picker interactions and is excluded from the
stated decision ceiling, as specified by #220. The selected generated save's
exact digest was retained for Resumed; continued play was never prechecked.

Four player_confirmed rows became visible: playable, save_made, save_reopened
and continued_play. Existing unknown_exit remained. Repeating Played showed
Already recorded and disabled confirmation. Keep/reopen retained session context.
Completed—retired required two decisions and left Needs inspection visible.

A partial-write UNKNOWN case retained the draft/context and disabled edits and
resubmission. A prewrite rejection retained an editable draft, survived Keep/reopen,
and succeeded only after a fresh deliberate confirmation. Regression checks also
cover queued confirmations, ambiguous context, separate session/build/edition
drafts, exact-save choice races, stale exit prompts and duplicate continued-play
intent. No automatic retry or observed process exit asserts successful gameplay.

## Verification boundaries

The final prepublication full suite passed 1,109 tests in 87.100 seconds under
Python 3.12.15. Maintained Python quality/formatting checks passed for 154 files;
full optimized arm64 frontend compilation passed with warnings as errors and a
macOS 13 deployment target. Independent review resolved five concrete findings:
queued UNKNOWN resubmission, hidden continued-play intent, stale save digest,
discarded-drop draft resurrection/pending ownership, and stale viewport callbacks.
The frozen production review manifest is
`6cee7ff7fe1e04978842b65d63f36352b54d1ab4ab9215af35b8b783a6109b08`;
the final single-file drop registration has SHA256
`9c1a3bf06747e7f3f8a1f78373f25f2cd162a3a13a3f75c2cac419cba51d7d56`
and receives a separate narrow review. Optional synthetic drag tooling was also
reviewed independently. Raw receipts and screenshots stay local.

Synthetic preview and source checks do not prove gameplay, real save/reopen,
production update installation, collector behavior, or macOS 13 runtime behavior.
Cold startup still performs current verification: the earlier 236-entry observation
was 23.717 seconds, including 23.529 seconds in verification. No persistent startup
verification cache or weaker operation guard was introduced. System accessibility
settings and VoiceOver speech require separate device-level evidence; exposed AX
names and observed keyboard focus are the evidence here. Engine knowledge use is
not applicable: UI glue reuses the existing evidence contracts without a new rule.

## Published package confirmation

The final application is [v0.24.0](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/releases/tag/v0.24.0),
from GitHub-Verified, single-parent source
`45324fafc5a946da3734ed8122be4987665b2062`. Clean replay preserved concurrent
personal-fork recipe and verification-badge work. The combined tree passed
1,117 local tests in 84.372 seconds, quality/formatting for 155 files and full
arm64 macOS 13 compilation. [CI 38194351655](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/actions/runs/38194351655)
passed 1,117 tests in 143.934 seconds, ten research-helper tests, quality and
release gates.

Downloaded ZIP SHA256:
`a1dc6e8052109fafee1c32baecd2d674db33e74d00916be69e30fd4e881d108c`.
The release checksum and GitHub asset digest matched. Strict deep codesign and
Gatekeeper Notarized Developer ID assessment passed. GitHub attestation verification
enforced both the exact source digest and this personal fork's release workflow.
The installed app was not replaced.

Root checked the unmodified signed package with synthetic-only launch flags:

- 900-point dark: Tab/arrow grid traversal, visible focus, long-title wrapping,
  Return to focused inspector and Escape to the same card; Command-F from
  Preferences focused the real search receiver. Search, no-match/reset, reverse
  sort and Known issue filter passed.
- 1180-point light: persistent grid/inspector, personal editor, progressive
  Played reporting and native General/Updates preferences passed. Standard About
  showed Version 0.24.0; closing it and Command-F returned to responsive search.
- 900-point light empty state distinguished an empty library from loading.
- 1180-point dark error state retained all cards; Dismiss and search worked.
- 900-point dark initial loading showed Opening your library and a busy indicator.
- 1180-point light refresh retained all cards and allowed search while its busy
  indicator remained active. These flags seed states; they do not perform reads.

All owned package previews exited afterward. No normal launcher service, live
update installation, game/profile/save operation or collector action ran. These
package checks join the production-source and closed-provider acceptance above;
they do not turn synthetic assertions into actual gameplay evidence.
