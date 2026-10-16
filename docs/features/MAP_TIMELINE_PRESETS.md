# Map years and personal timeline presets

Status: draft feature plan. Craig selected saved start/end year presets per map,
while retaining the original options. Implementation has not started.

Branch: `Craig-Franklin/map-timeline-presets`, based on `8ebd0a7`.
Tracking: [feature issue #233](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/233).

## User outcome

When Craig opens a map's Overview, he can see its authored year options and save
additional personal ranges for a new game. For example, a synthetic map could
offer an original 1830–1930 option and a personal “Long game” 1830–2050 option.
The dates in this example are illustrative, not a verified supported range.

“Playable years” describes the requested feature. Until runtime behavior is
verified, the interface should say **Year options** and **Declared by the map**,
rather than promise that every intervening year works.

## Overview interaction

Add a Year options section after the existing map facts and before Briefing.
Keep the current artwork grid, inspector, warnings and contextual Play action.
Use standard SwiftUI controls and the existing native design rules in
[DESIGN.md](../../DESIGN.md).

- Show each original selectable range with its name and start/end years. If a map
  contains several scenarios, group options under their scenario. Do not collapse
  distinct options into a misleading minimum-to-maximum range.
- Show personal presets beside the original options, with a clear Custom label.
  Default to the original selection. Remember later selections for the exact map
  edition; never change the selection merely because another map was opened.
  Keep preset selection on the originating map's Overview; resolve the selected
  preset to its exact prepared edition without requiring manual library searches.
- **Add Timeline…** opens a small inline editor in the inspector. Choose the base
  scenario and original option, then enter a name, start year and end year. Seed
  the year fields from the chosen original. **Save Timeline** saves the preset
  without launching the game. Cancel preserves the current selection.
- Play uses the selected option through the maintained launcher service. Show the
  selected years near Play and identify a custom option as experimental. Editing
  a custom preset creates a replacement edition; existing saves stay bound to the
  old edition. Removing a preset from the picker never removes its saved games.

The editor explains: “Applies to new games. Original map goals and train
availability stay unchanged. Longer timelines may need testing.” Show this once
in the editor, not as repeated confirmation dialogs.

## Timeline policy

The first version changes only the selected calendar option. It does not shift
goal deadlines, rescale eras, invent later locomotives, remove retirement dates,
change difficulty, or edit an existing saved game. Goal periods may therefore
cover only part of a personal timeline; expose their declared dates separately
when available. Extending a range is not a promise of additional content.

Do not implement the feature by appending a goal era or by changing the last
goal's deadline. Calendar selection, scenario era selection and goal resolution
need separate evidence. Before choosing XML fields, establish which resource
defines the selectable ranges and how the inspected Mac build consumes it.

Start/end fields accept whole years with start earlier than end. Do not invent a
hard-coded safe historical range. Establish and test supported engine bounds
before enabling custom preparation. Before-range or after-range requests remain
experimental even when they pass numeric validation. A start with no usable
starting content must produce a useful explanation, not a compatibility claim.

Missing years display **Years unavailable**. Malformed, duplicate, unresolved or
ambiguous declarations display **Years need inspection**, with a reason. A known
starting year and unknown ending year remain partial information. Preserve the
current original Play behavior when custom preparation is unavailable.

## Smallest implementation path

| Stage | Work | Completion evidence |
| --- | --- | --- |
| 1. Read authored options | Add one bounded, read-only year reader for the selected prepared scenario and its relevant resources. Identify calendar options separately from goal periods. Return source, values and unknown/ambiguous states. | Synthetic declarations cover one/multiple options, multiple scenarios, partial dates, malformed values, duplicate references and unresolved dependencies. A motivating real map receives a private identity-bound inspection. |
| 2. Present and save presets | Expose the reader through the native details response. Add the Overview controls and local preset persistence. Bind a preset to archive, parent variant, scenario and relevant input digests. | Presets survive restart, remain scoped to the right edition, preserve originals and show validation beside the relevant field. Selection/drafts survive ordinary navigation. Stale inputs require explicit review before reuse. |
| 3. Prepare and play a custom edition | Extend existing immutable variant preparation with an explicit timeline transformation. Give the output its own identity/profile and use existing activation and Play. Resolve preset-to-edition mapping without adding a second launcher or activation system. | Exact transformation and unrelated-byte preservation tests pass. Running-game, changed-input, interrupted-preparation and activation failure cases retain original profiles and saves. One independent AI review covers the frozen preparation/activation diff. |
| 4. Verify the longer timeline | Under separately authorized finite game testing, check the original control and one custom range on the motivating map. Cross the original ending boundary and check continued play. Save uniquely, quit, reopen that exact save and continue. | Record native selection/start-year behavior, boundary behavior, goal effects and exact save/reopen outcome separately. A successful load alone does not complete this stage. |

Stages 1–2 can land before custom Play is enabled. They do not complete the full
feature: a saved personal label without an effective new-game range is not a
working timeline extension.

## Reuse and code touchpoints

- `src/native/LauncherView.swift`: `overview(_:)` owns the existing Overview facts
  and Briefing. Keep the new section here; add draft/selection state through
  `src/native/LauncherModel.swift` using the existing map identity conventions.
- `src/smr_launcher/native_backend.py`: `NativeLibrary.details()` currently returns
  scenario paths, history and checks. Add structured year options here rather
  than parsing game files in Swift or eagerly scanning the whole library.
- `src/smr_launcher/application.py`: `MapRecord.scenarios` supplies the exact
  scenario inventory. `create_edition()` demonstrates preparation without
  activation or copying saves; `play()` is the maintained launch boundary.
- `src/smr_launcher/variants.py` and `editions.py`: reuse staged output, hashes,
  freezing and registration. Existing edition options only support editor and
  difficulty; timeline support needs a reviewed extension. Compiled compatibility
  editions currently reject option composition; preserve that refusal until a
  combined transformation is explicitly supported.
- Reuse bounded private-file and atomic-write helpers for a small preset store.
  Keep executable options separate from display-only annotations. Existing
  scenario verification remains the sole evidence reducer; a new timeline starts
  with unknown gameplay/save coverage.

No new dependency, generic mod editor, shared preset service or save migration is
needed. Existing unrelated compatibility work remains outside this feature.
The first version targets imported maps with an Overview. Adding these controls to
the separate Original Game destination is additional scope.

## Acceptance and verification

The finished feature shows the exact authored options or an honest unknown state;
retains the original options; persists named personal ranges; applies the chosen
range to a new game; and preserves original packages, prepared editions and saves.
A changed preset creates a distinct effective edition without transferring old
gameplay or save/reopen results.

Focused unittest coverage should prove parsing outcomes, preset validation and
identity, exact transformed output, preservation and guarded failure behavior.
Native fixtures should cover empty/error/partial states, several options, long
names, dirty drafts and stale selection. Check keyboard operation, VoiceOver,
light/dark appearance and the existing 900-point narrow-window navigation.
Use Python 3.12 for checks and `python scripts/check_quality.py`; build only when
native integration needs packaged-app verification.

Source tests, native rendering, effective game selection, continued play beyond
the original end and exact save/reopen are separate acceptance results. Record
them in the feature issue. Do not transfer a result to other maps or builds.

## Knowledge consulted and open decisions

Planning consulted the current knowledge-use contract, validation scope,
SMR-K-002 resource-resolution limits and the developer guide. Earlier bounded
static work in [#140](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/140)
and [#142](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/142)
supports keeping calendar options, ordered scenario eras and goal deadlines
distinct. Those results do not prove the XML transformation or runtime behavior
needed here. Private consultation evidence stays outside Git.

Knowledge status for this plan is partial. No real map or current executable slice
has been matched for timeline editing, and safe numeric bounds, effective resource
selection and save/reopen behavior remain unresolved. Stage 1 must resolve these
before custom preparation. Keep goal dates and content unchanged as the proposed
first-version policy. Craig's motivating map/range and final design confirmation
remain to be recorded.
