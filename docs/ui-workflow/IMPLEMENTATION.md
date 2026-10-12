# Native Design B implementation

Craig approved the SMR Native Design Review after choosing B. This implementation
uses SwiftUI and AppKit for the main Mac window, retaining the Python services and
protected workflows. [Project 5](https://github.com/users/Craig-Franklin/projects/5)
and issues [#218](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/218),
[#219](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/219),
[#220](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/220)
and [#221](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/221)
remain the acceptance record. The [selected pattern sheet](../../DESIGN.md) and
[original review](REVIEW.md) explain the composition and its alternatives.

## Implemented behavior

The main window has Map Library, Collection, Original Game, Activity and Preferences.
B uses an artwork grid with a selected-map inspector. Below 1080 points it uses a
destination menu and either the grid or a Back to Library detail surface. Search,
sort and filters operate on an in-memory presentation without rereading packages.
Selection, drafts and native scroll positions survive ordinary navigation. Refresh
keeps the last usable presentation until a complete replacement succeeds.

Normal native launches hold a private per-user file lock for the process lifetime,
before starting Python services. A second cooperating copy activates the exact
recorded owner and exits; invalid ownership or lock errors refuse startup. The lock
file remains in place, and the operating system releases ownership on process exit.
Synthetic `--fixture` previews bypass the lock and never activate a normal instance.
Older builds do not participate: close an existing older launcher before the first
guarded launch. Existing transaction and game guards still protect operations; the
frontend lock is not proof that a previously closing Python service has exited.

The inspector includes Overview, Play History, Issues & Evidence, Edit Details and
Record Outcome. Personal titles, notes and selected image copies live separately
from package facts, keyed by archive and variant hashes. The image picker bounds
input at 8 MiB and creates a PNG of at most 360 pixels and 1 MiB. Private annotation
files use no-follow traversal, a private directory and atomic replacement. Clearing
a personal image restores package presentation. Editing other fields preserves an
existing image even when a presentation-art budget prevents showing it.

Personal and outcome drafts remain in memory while the app is open. Cancel offers
Keep Draft or Discard Draft; quitting warns about unsaved drafts. Drafts are not a
durable recovery store across app restart. A saved annotation is durable. Package
facts, original images and verification records are not rewritten by this editor.

The recorder starts with the exact map edition, executable build and recorded open.
It asks for Played, Problem, Saved, Resumed or Completed, revealing relevant fields.
A post-exit prompt refreshes and validates context before opening the form. A unique
existing game binding may be reused; ambiguous bindings require an explicit choice.
Saved and Resumed require an exact save digest in this UI. The optional continued-play
confirmation records both exact reopening and continued play in one validated batch.
Neither process observation nor a save digest asserts gameplay. Legacy Mac tests stay
separate from exact scenario evidence. Known failures remain visible.

Preferences groups language/voice, updates, library/setup and crash reporting. Shared
labels reuse existing translations; new workflow copy currently uses English. System
appearance and accessibility settings determine material appearance. Standard native
chrome receives Liquid Glass on supported systems; content remains readable. The
build retains a macOS 13 deployment target with guarded macOS 26 material APIs.
Compatibility recipes, legacy tests, fix testing, recovery, translation, bulk collection
and diagnostic/update presentation now use integrated native destinations. Read Aloud
has a Stop Reading control. Native consent calls the existing protected operations;
the bridge cannot bypass their guards.

## Service and packaging boundary

`src/native` owns presentation. `native_backend.py` communicates through private
stdin/stdout JSON lines with request identities, explicit success/failure and events.
Reply framing and JSON decoding run on a dedicated serial background queue;
snapshot metadata and image preparation run outside the main actor. The main actor
applies completed presentation state. Pipe writes stay ordered on their own queue.
There is no listening socket, web runtime or new third-party dependency. The hidden
Tk owner retains initialization, polling, crash observation, guarded operations,
update handling and safe shutdown. Normal native navigation uses integrated tools;
legacy routes remain only as a compatibility boundary.

Browsing caches never authorize an operation. Annotation and report mutations check
fresh exact identities. Report events use existing ledger validation. Lost transport
never retries a mutation automatically; an unfinished result is unknown and needs
inspection. Recent mutation receipts deduplicate the same request only during one
service session. Package and personal artwork share a 32 MiB projection budget.
A 96 MiB response guard returns a structured error before the native 128 MiB reader
limit. A sufficiently large metadata envelope therefore requires inspection rather
than silently dropping text or disconnecting.

The bundle's main executable is `SMRLauncher`. The original Python executable name
remains available for services and the existing signed update-helper contract. Both
executables and then the enclosing bundle are signed. On orderly shutdown Python
waits for the frontend's input EOF before exiting; the updater waits for Python.
This prevents replacement while the native frontend is still alive. A crashing
child can conservatively produce UNKNOWN even after writing a final response.
Advanced-tool Quit requests pass through the native draft warning. Closing the
advanced window hides it. If an existing update dialog cancels shutdown, later
quit requests require fresh approval. Disconnected native drafts still warn.

The hidden Python/Tk service uses the macOS accessory activation policy so it does
not appear as a second ordinary app in the Dock or app switcher. Tk establishes its
own application object before the service requires that policy. Explicitly showing
the existing advanced window temporarily restores regular policy and its menus;
hiding it restores accessory policy. Both the policy setter result and readback are
checked. A local isolated Tk fixture, without launcher initialization or personal
data, confirmed accessory → regular → accessory through public NSWorkspace reads.

Craig subsequently requested that all advanced tools join the main native design.
The separate Tk window is a migration boundary, not the intended final interface.
Integrate these workflows into the relevant native destinations and contextual
inspectors, preserving validation and explicit consent. Remove Tk ownership once
its remaining UI and event-loop responsibilities have native replacements. A full
Python service rewrite is not a prerequisite for this interface integration.
The [native tool destination map](NATIVE_TOOLS.md) records the integrated destinations.
Briefing translation uses edition-bound results, language choices and Show Original.
Contextual tools, Preferences and Collection now have native presentation. The hidden
Tk owner remains a lifecycle migration boundary.

## Verification and limits

Local full suite before the final quit correction: **977 tests passed** on Apple
Silicon macOS 27.2. After that correction, **35 focused native tests passed**,
including 33 bridge/annotation tests and one compiled Swift check. The existing
26 history tests also passed before the correction.
The Swift check covers retained selection/drafts, 1,000 synthetic maps and fragmented
pipe replies, events, rejection and disconnect. Forty in-memory query/sort samples
had p95 about 2.4 ms. That measures model computation, not updated viewport rendering,
memory growth or the full #218 performance target.

An isolated Tk lifecycle test ran the real constructor, startup layout, hidden root,
request pump, preferences and shutdown with temporary home, fake bootstrap/voices,
no installation and forbidden discovery/monitor/network calls. This is synthetic
service-owner evidence, not production startup against Craig's library.

The arm64 development app and DMG built locally with strict deep signature
verification, checksum and mounted-payload equality checks.
A packaged harmless JSON probe verified Python bootloader stdin/stdout without
initializing the launcher. Visual checks used `--fixture`: six fictional maps and
synthetic illustrations, no Python service, file writes, game actions or network.
They covered light/dark content, grid/inspector, editor, progressive report and the
900-point compact layout. An initial scroll-origin defect was corrected and
confirmed. Captures stay local. Native field readback verified a synthetic note.
Some provider button names and focus/navigation actions remain unverified; this
is not a VoiceOver or full-keyboard-access acceptance claim.

No game, profile, save, live crash collector, production update or installed-app
replacement ran. No release or merge follows from this delivery. macOS 13 fallback
compiled but has not been exercised on macOS 13. Before/after manual-action targets,
viewport p95 at 250/1,000 maps, wider accessibility settings and operational dialogs
remain acceptance work in the canonical issues. Engine knowledge consultation is
not applicable: the changes reuse existing evidence contracts and add no engine rule.

### Large-reply responsiveness correction

Read-only sampling of a hanging installed 0.19.1 launcher found its main thread
almost entirely in `Backend.receive`, repeatedly searching accumulated reply data
for a newline. Fragmented large replies caused quadratic scanning on the UI thread.
The correction searches only new bytes, decodes JSON off-thread in wire order and
prepares map presentation asynchronously. Disconnects still report unknown mutation
results, and the 128 MiB transport limit remains enforced.

With 1,000 fictional maps and 16 MiB of text in 8 KiB pipe fragments, the old parser
exceeded a bounded 20-second check. The corrected transport and model completed in
about 75 ms, with main-actor scheduling gaps below 3 ms in the local synthetic run.
That is transport/preparation evidence, not viewport rendering or real-library
acceptance. Regression checks also cover split UTF-8, coalesced ordered replies,
malformed/nonobject JSON, oversize rejection and unknown disconnected results.
Private live-process samples remain local. No running launcher was stopped or
replaced for this diagnosis.

## Parallel native integration

The current integration combines three isolated lanes: stable library presentation, contextual map tools and global
Preferences/Collection tools. Review corrections bind detail replies to the exact
executable build, preserve newer image edits across asynchronous saves, apply the
same aggregate artwork budget to targeted replies, reject overlapping queued request
identities and require explicit installation consent for manually staged updates.
Partial history appends and removal failures with retained transaction journals hold
UNKNOWN and preserve drafts. They never automatically repeat protected operations.

Warm library reads reuse the complete presentation. Explicit Refresh and relevant
library/build/history changes invalidate it. History projection validates the ledger
once, partitions by exact identities and uses the existing reducer. Read-only details
are coalesced and do not own the frontend mutation gate. Image reading, thumbnail
preparation and decoding run off the main actor. Protected operations remain ordered.
Cached presentation is never verification authority.

Independent synthetic before/after measurements used 20 Python samples and 30 Swift
samples per scenario. At 1,000 maps and 10,000 fictional history events, history
projection median fell from about 2,734 ms to 18.7 ms. Twenty warm projection reads
fell from 20,000 annotation reads to zero. The query cache trades roughly 6 MiB of
peak model RSS for reuse. These figures measure injected I/O and model computation,
not actual viewport timing or production startup. Initial real-library verification
remains costly; this change adds no persistent verification cache and does not weaken
verification requirements.

Native task-step comparisons with already loaded identities reduce legacy-test
entry from two navigation actions to one, history scope from three to one and fix
record entry from four to one. No-warning Play remains one user action; known-warning
Play requires a separate exact-target confirmation. These are source-derived and
injected counts, not a complete observed multi-checkpoint workflow or acceptance of
the 50% action-reduction target.

Local integrated verification passed **1,095 tests**, maintained Python quality and
formatting checks, and a full optimized arm64 frontend compile with warnings as
errors and macOS 13 deployment target. The image/save regression uses generated PNGs
and temporary FIFOs to control both completion orders. No real user file or service
is involved.

A batched synthetic render inspection covered wide light and 900-point dark layouts,
General/Updates/Library/Crash Reporting preferences, available/staged updates,
Collection unknown/partial outcomes, compatibility and play history. Long map names,
explicit failure text and disabled controls remained visible. Private copied-source
fixture overrides seeded category/update/job states only; no provider, collector,
installation, map or game operation ran. Captures stay local. No visual correction
was needed in that batch. Provider menus/button names and focus delivery remain
partly unverified; these renders do not establish keyboard or VoiceOver acceptance.

The canonical issues remain open for packaged accessibility and viewport timing,
complete observed action reductions, cold-start verification and authorized real
operation walkthroughs. The hidden Tk owner remains. Synthetic passes do not prove
actual game launch/exit, gameplay, save/reopen, live translation or production update
installation. Engine knowledge consultation is not applicable to this UI glue;
existing evidence and preservation rules remain authoritative.

## Build and isolated preview

```sh
./scripts/build_macos.sh
open -n "dist/Definitive SMR Launcher Apple Silicon.app" --args --fixture
```

Fixture options include `--fixture-width 900`, `--fixture-appearance light` or `dark`,
`--fixture-view "Edit Details"`, `--fixture-view "Record Outcome"`,
`--fixture-view Preferences`, `--fixture-empty` and `--fixture-error`.
The fixture label stays visible. A normal launch starts the existing real services;
do not use it as an isolated preview. When multiple installed/development instances
share a bundle ID, identify the exact executable and PID before UI observation.
