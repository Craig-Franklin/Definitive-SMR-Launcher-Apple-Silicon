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
and diagnostic/update dialogs remain reachable in the existing advanced Tk tools.
Those dialogs have not yet been replaced with SwiftUI. Read Aloud has a Stop Reading
control. Setup opens its existing consent UI; the bridge cannot bypass it.

## Service and packaging boundary

`src/native` owns presentation. `native_backend.py` communicates through private
stdin/stdout JSON lines with request identities, explicit success/failure and events.
Reply framing and JSON decoding run on a dedicated serial background queue;
snapshot metadata and image preparation run outside the main actor. The main actor
applies completed presentation state. Pipe writes stay ordered on their own queue.
There is no listening socket, web runtime or new third-party dependency. The hidden
Tk owner retains initialization, polling, crash observation, guarded operations,
update handling and safe shutdown. Advanced tools appear only on explicit navigation.

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
