# Wireframe review for issue #222

**Decision recorded, 11 October 2026:** Craig selected B, artwork grid with
a persistent inspector, after approving SwiftUI with AppKit where needed. [Project 5](https://github.com/users/Craig-Franklin/projects/5) and
[#222](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/222)
remain canonical. This is a design package, not an additional active checklist.

Open [wireframes.html](wireframes.html) locally. Review A and B, then use the screen
buttons to inspect the selected pattern across details/images, reporting,
preferences, states and the compact pattern sheet. The State menu and appearance
toggle show variations. Resize the browser to compare wide and narrow composition.
No server, installation, network, file access or launcher connection is required.

[PRODUCT.md](../../PRODUCT.md) records supplied context.
[DESIGN.md](../../DESIGN.md) records the proposed pattern rules and recommendation.
Both documents now record Craig’s explicit B selection.

## Baseline and evidence strength

Inspected source: `5c1185bacb50bd34aa8f53a822ad303b84da8101`.
Startup: `python3 scripts/check_session.py` verified Orca caller/workspace;
`git status --short --branch` showed a clean dedicated worktree.
Read Project 5, #217, #222 and #218–#221. #217 is completed; #222 was already
In Progress and remains open. Follow-up implementation remains Todo.

Source observations rechecked in this session:

- `gui.py:_show` destroys content on destination changes. It remembers a map scroll
  fraction, but `_maps_view` clears `_gallery_snapshot` and starts loading again.
- `_render_gallery` starts another snapshot; `_begin_gallery_batches` filters it,
  then destroys and rebuilds cards. Search/filter/sort/column changes repeat work.
  Existing cancellation, batching and worker limits remain valuable.
- `gallery.py:gallery_snapshot` reads catalogue, metadata, contextual verification,
  bounded artwork and history summaries. Presentation reuse needs explicit
  invalidation; it must not replace action-time identity or evidence validation.
- `history_ui.py:checkpoint_dialog` shows attempt, scenario, checkpoint, game,
  exact-save hash, failure attribution and failed checkpoint together. The exit
  prompt already passes `attempt_id`; reuse it. Seven visible choice fields do
  not mean seven required user decisions in every current task.
- Current Map Details mixes a facts table, nine notebook tabs and several bottom
  action rows. Metadata comes from package readers. Source links, briefing,
  translation/read-aloud, legacy Mac tests, exact history, checks and compatibility
  options must remain reachable.

Current source was rendered locally with six fictional records using Python 3.12
and Tk 9.1. The private fixture bypasses discovery, live services, crash monitoring,
network/background operations and game actions. It uses current library/card,
details, checkpoint and preferences widgets. Gallery resizing and snapshot delivery
are controlled for capture; this is not a performance baseline. Synthetic history
exists only under this worktree's ignored `.local/ui-workflow/fixtures` directory.

Local captures: `.local/ui-workflow/evidence/current-library.png`,
`current-details.png`, `current-report.png`, `current-preferences.png`.
The card capture shows three tall cards in the first viewport at the current
1120 × 720 source geometry. The details capture shows compressed tab labels;
the report capture shows all seven choice fields plus a save picker. These are
synthetic current-source renders, not the installed app or actual user workflow.
No source/artifact equivalence, packaged timing or actual gameplay is established.
The preview's inherited discovery status text does not mean discovery ran.

Finder and Preview were inspected in task-created windows containing only the
synthetic fixture folder and the synthetic library capture. Captures and native
accessibility snapshots remain local (`finder.*`, `preview.*` in the evidence
directory). Finder showed destination sidebar, list columns, view controls,
toolbar search and restrained separators. Preview showed a compact toolbar,
separate sidebar visibility control and content-focused reading surface. No
personal document contents were inspected. Do not publish these desktop captures.

## Reference decisions

Apple's [macOS guidance](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos),
[windows](https://developer.apple.com/design/human-interface-guidelines/windows),
[sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars)
and [toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars)
ground the desktop structure. The JS-rendered HIG pages returned only their shell
to the text reader; indexed first-party excerpts and actual reference windows were
used for the bounded observations below, not an exhaustive HIG audit.

- Reuse Finder's stable destination/content separation and selection-based detail
  pattern. Apple documents the [Preview pane](https://support.apple.com/en-gb/guide/mac-help/mchl1e4644c2/27/mac/27)
  as a place for a selected file's content and metadata. This supports the inspector
  pattern, but Finder's full file-operation vocabulary would overwhelm map browsing.
- Reuse the distinction between list and visual recognition described in
  [Finder view choices](https://support.apple.com/en-gb/guide/mac-help/mchldaafb302/mac).
  B tests artwork-led browsing. It is not an exact copy of Finder Gallery, which
  arranges items in a bottom row.
- Reuse Preview's quiet reading surface and optional supporting controls. Apple's
  [Preview guide](https://support.apple.com/en-ie/guide/preview/prvw11470/mac)
  documents optional thumbnails and a resizable sidebar. Editing tools need not
  surround the map briefing all the time. Preview's annotation toolbar and floating
  inspector model are unnecessary for routine launcher metadata entry.
- Native materials such as translucent titlebar/toolbar effects are not a Tk
  deliverable. Craig subsequently approved SwiftUI/AppKit to receive the system
  appearance; the native proof below supersedes Tk as the presentation direction.

## Numbered task flows

1. **Browse and inspect.** Open Map Library; search/filter/sort the existing
   presentation; select a map; read Overview, History or Issues & Evidence in the
   detail pane. Returning from another destination restores query, selection and
   scroll. At narrow widths, selecting opens detail; Back restores the list.
   If a selected map is hidden by a filter, explain it and disable its actions.
2. **Enter play.** Select a map; inspect any visible known-issue warning; choose
   Play. Existing fresh validation, stopped-game checks and guarded confirmations
   still run before activation/launch. The mockup only displays this boundary.
   Double-click/Return on a row opens details, never starts the game.
3. **Add details/images.** Select map; Edit Details; review package facts without
   retyping; add personal title/notes and explicitly choose a local image; Save
   Details. File validation stays near the image field; retain old image and draft
   on failure. Keep Draft and later recovery use the same map identity. Cancel
   with dirty input offers Keep Draft or Discard Draft. No package bytes change.
4. **Record a known session.** Open the post-exit prompt or Record Outcome from
   that session; inspect prefilled context; choose the actual outcome; confirm only
   required missing bindings; Record Confirmation. Target for an unambiguous,
   already bound session: outcome, optional checkpoint distinction, confirmation
   within three decisions. This is a design target, not a measured improvement.
   The unknown-session example deliberately adds scenario/game choices.
5. **Record a problem/save/resume.** Choose Problem to expose phase and incident
   attribution, retaining Unknown when unresolved. Saved/Resumed reveal read-only
   exact-save selection and explicit checkpoint wording. Continued play after
   reopening remains separate. Fix/test is a separate contextual flow requiring
   the implemented fix, exact failure/checkpoint and qualifying test session.
   Legacy Mac Test remains a distinct labeled route. Defer retains draft; retry
   must reuse the same operation identity in the eventual implementation.
6. **Change preferences.** Cmd-comma or the lower sidebar shortcut; choose General,
   Updates, Library or Crash Reporting. Preserve existing immediate-save semantics
   for language/voice, signed update checks, privacy defaults and capability gates.
   Returning restores the library. The mockup never saves system/app preferences.

The browser prototype demonstrates composition, selection, search/filter/sort,
progressive outcome fields and in-memory draft recovery. File dialogs, Play,
compatibility commands, import, publication and system settings are labeled
stubs. It is not a complete implementation of the keyboard/evidence contracts.
Drafts disappear when the page reloads; durable draft storage belongs to #219/#220.
No measured 50% reduction or packaged-app UX pass is claimed.

## State and size coverage

| State | Proposed behavior |
| --- | --- |
| Populated | Six fictional maps, including long title, missing author/date/type/art and different evidence labels. Known issue stays visible. |
| Empty library | Explain Import or Collection; no “no matching maps” ambiguity. |
| First load | Bounded skeleton and reading label; no false empty-library claim. |
| Refreshing | Keep last good rows, selection and scroll. Small status label describes work. |
| Refresh failure | Keep rows; explain freshness; Retry. Current validation remains mandatory for actions and evidence. |
| No results | Clear search/filter recovery; underlying library preserved. |
| Input/image failure | Inline error; retain personal text, previous image and pending draft. |
| Unknown/ambiguous | No automatic pass; require necessary scenario/game/incident choice or retain unknown where permitted. |
| Narrow | 900 pt review: destination menu, list or detail, explicit Back. Proposed app minimum remains 900 × 590. |
| Default/wide | 1120 and 1440 pt review: stable panes and independent scrolling. |
| Appearance | Light and dark palette; meaning never relies on color. HTML appearance is a composition check only. |

## Tk feasibility and implementation risks

| Pattern | Existing toolkit path | Gap or later acceptance |
| --- | --- | --- |
| Stable split layout | `ttk.Panedwindow`, frames and current services | Retained state, worker invalidation and independent pane scroll need #218 tests. |
| Compact selectable rows | Native `ttk.Treeview` or minimal existing card adaptation | Wrapped variable-height rows plus thumbnails are awkward in Treeview; evaluate a small fixture before committing. No new dependency. |
| Details and progressive forms | Existing `ttk.Notebook`, frames, native entries/comboboxes | Prefer fewer top-level sections; preserve capabilities and contracts. Verify layout at minimum height and longer translated labels. |
| File/image intake | Existing native file dialogs; bounded image handling | Provenance, immutable originals, annotation/draft persistence and rejection cases need implementation/review. |
| Native chrome | Standard Tk Mac window, menus, controls | No promise of unified AppKit toolbar, vibrancy, native attached sheet or SwiftUI sidebar parity. |
| Appearance and fonts | Current palette plus named Tk fonts | Verify actual system font mapping, accent/dark changes and scaling; avoid hard-coded browser metrics in Python. |
| Keyboard and accessibility | Tk focus/menu bindings and native controls | Current synthetic Tk accessibility tree exposed only the window chrome, despite rendered widgets. VoiceOver/full keyboard access are significant unverified gaps; HTML does not resolve them. Craig subsequently approved the native direction; the synthetic proof below is separate evidence. |
| Reporting semantics | Reuse attempt-ID route and existing stores/validators | Do not merge legacy/exact evidence or infer success. Durable drafts and idempotent retries require implementation checks and scoped independent review. |

Impeccable 4.5.2 / engine 0.1.14 context ran with update/staleness checks disabled.
It reported `platform: null` and `hasVisualImplementation: false`. Operate/shape
guidance informed task hierarchy; supplied scope takes precedence over a repeated
discovery interview or a full visual build workflow. Its web detector has no verdict
on Tk. Engine knowledge use: not applicable; no engine rule or evidence contract
was changed. The synthetic examples repeat current distinctions without certifying
compatibility on any build or map.

## Native proof and migration boundary

Craig requested current Apple Liquid Glass and approved SwiftUI with AppKit where
needed, retaining Python services. He then selected B. [NativeWireframe.swift](NativeWireframe.swift)
implements both alternatives and the shared surfaces as a standalone synthetic
review application. B is the default. It uses standard system navigation, search,
controls and a glass primary button. No backend, file, network, subprocess or game
APIs occur in this source. Drafts are in memory and keyed by synthetic map identity,
including the whole reporting context. Play/import/image/save/report actions are
labeled stubs; no event or preference is written.

Build the review artifact only, from the repository root:

```sh
mkdir -p .local/ui-workflow
xcrun swiftc -parse-as-library -target arm64-apple-macosx26.0 \
  docs/ui-workflow/NativeWireframe.swift \
  -o .local/ui-workflow/SMRNativeReview
```

This command compiles an executable, not the signed production launcher. An isolated
local app bundle was used for visual capture. Environment: macOS 27.2 arm64,
Command Line Tools Swift 6.4, macOS SDK 27.0. Full Xcode is not selected. The first
compile exposed a missing SwiftUI State macro plugin in Command Line Tools; the
source explicitly selects the public `SwiftUI.State` property wrapper through a
type alias. The preview deployment target of macOS 26 is local proof only; the
production build's macOS 13 support has not changed. OS availability/fallback,
release packaging, signing and update integration require implementation evidence.

The existing `scripts/backend_entry.py` delegates to `smr_launcher.cli`. The CLI
currently exposes JSON `status`, `setup`, `import`, `play` and `choose-library`
commands. It is a partial bridge: metadata/images, contextual history, preferences,
progress/cancellation and crash lifecycle are not complete frontend operations.
No real CLI command was executed for this design work. A new frontend must call
Python services through explicit reviewed operations; it must not replicate
activation, identity validation, evidence interpretation or privacy logic in Swift.
Begin with read-only library snapshots and stable selection/invalidation under
#218. Keep existing guarded mutations in Python, expanding the bridge only for
specific selected workflows in #219–#221. No web server, paid API, new third-party
dependency, replacement game runtime or release is part of this artifact.

## Review result

The HTML interaction receipt reports 14 focused checks passed, including per-map
draft recovery, exact game/completion choices, progressive save/problem fields,
filtered selection safety, retry and B-preserving navigation. Final light/dark,
wide/narrow, editor/report/error/privacy captures remain local. These establish
composition and synthetic interactions, not native or gameplay behavior.

Independent artifact review identified draft loss, incorrect choice restoration
and B-to-A task return in the first HTML revision; all were corrected and reviewed.
It also identified shared reporting context in the native proof; all report fields
are now keyed by map. Independent follow-up review passed the final native source/boundary and the B
capture, then passed the final HTML grid/detail alignment. Reviewed SHA-256:
`fdf249ba782a0d1c4774e97824e3c3e054410f1673d5b78a37345988de5cbf4a`
(native source), `8875fdcd1ccafc9890bdb4a9f117344bceb4910f009744c29bd5f4c9d07b7938`
(HTML). The final native preview compiled successfully;
`python scripts/check_quality.py` passed with the Python 3.12 development interpreter.
Native capture and publication revision are linked in the canonical issue. Native accessibility exposes real controls,
but the provider still omits several button names despite explicit labels.
VoiceOver, full keyboard access and all system accessibility/material settings
have not passed acceptance. The native narrow-window collapse remains an
implementation gap; the HTML demonstrates the intended grid/detail Back pattern.

This milestone records B and reusable rules. #218–#221 remain the canonical
implementation work. No source check, rendered synthetic app or player label here
establishes actual game compatibility, save/reopen or crash-collection behavior.
