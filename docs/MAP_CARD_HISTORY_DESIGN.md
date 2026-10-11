# Map card play history — issue #203 interview

**Decision and validation record.** The feature was delivered through PR #209;
[#203](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/203)
carries completion and release evidence. The checks below describe the original
offline/native UI validation and establish no actual gameplay result.

Issue: <https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/203>

## Agreed decisions

1. When a map previously crashed but later played successfully, its main card
   light stays **amber until a fix is tested**. Craig selected this on
   11 October 2026. A later successful play alone does not clear amber.
2. A tested fix clears amber when the checkpoint that previously crashed
   passes and the result is explicitly recorded as a successful fix test.
   Craig selected this on 11 October 2026. Saving, exact-save reopening,
   and continued play remain separate checkpoints; they are not additional
   requirements for clearing this warning. The test must apply to the
   relevant failure and exact edition/build, as required by issue #203.
3. The card's open/start counter counts a **confirmed game process for that
   map**, rather than a Play click or first playable state. Craig selected
   this on 11 October 2026. Launch attempts and map/gameplay checkpoints
   remain separate. Final counters are Opens, Started, Completed, and Crashes. Save resumes
   follow decision 6 below.
4. A game counts as completed by **either retiring or meeting all scenario
   goals**, with the completion reason recorded. Craig selected this on
   11 October 2026. Player confirmation follows decision 5; deduplication
   across save resumes follows decision 6.
5. Offer **both a short prompt after game exit and later confirmation in
   Map Details** for checkpoints the launcher cannot reliably detect.
   Craig selected this on 11 October 2026. Label player confirmations
   separately from automatic observations; unconfirmed outcomes stay unknown.
6. Reopening an existing save in a newly confirmed game process adds **one
   open, continues the same game, and counts that game's completion once**.
   Craig selected this on 11 October 2026. Example: starting a game and
   returning to its saves twice yields 3 opens, 1 game started, and at most
   1 completion. Separate new games remain distinct from save resumes.
7. For a map with no unresolved recorded crash, **green requires a completed
   game on the current edition/build**, by retirement or meeting all scenario
   goals. Craig selected this on 11 October 2026. Loading, playable state,
   save/reopen, or a successful fix test alone do not earn green. A fix test
   can clear amber under decision 2 without supplying completion evidence.
   Name the checkpoint alongside the light; this is separate from the
   existing verification badge and does not establish full compatibility.
8. **Red is for a confirmed crash; resource/watchdog stops are amber** with
   their own "Resource stop" label. Craig selected this on 11 October 2026.
   Unknown exits remain unknown rather than being inferred as crashes.
   A past crash followed by successful play still follows decision 1.
9. Retain the **full checkpoint trail in Map Details**, with a compact
   summary on the card. Craig selected this on 11 October 2026. Checkpoints:
   launch attempted, process confirmed, map loaded, first playable state,
   save made, exact save reopened, continued play, retirement/goals met,
   confirmed quit, crash during load, crash during play, crash with unknown
   phase, resource/watchdog stop, cancelled attempt, unknown/unfinished
   session. Do not infer an unobserved checkpoint. Player confirmations
   follow decision 5.
10. Scope linked issues and fix/test status to the **exact scenario and
    edition**. Craig selected this on 11 October 2026. Broader map issues may
    appear as related context without transferring fix tests or clearing
    another variant's warning.
11. Use the **Roomier grouped card at 230 pixels wide**. Craig selected this
    after reviewing the native Compact/Roomier comparison on 11 October 2026.
    Keep all agreed card fields visible. Use aligned counters, grouped
    Issue/Fix/Test rows, and a quieter verification/author/date footer.

## Approved mockup and design record

Craig reviewed a native comparison of Compact (190 × 544) and Roomier
(230 × 500), with identical illustrative information, and selected Roomier.
The agreed decisions, wording, examples, and mockup were posted before feature
implementation and read back in
[issue #203](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/203#issuecomment-6110715651).
All sample counts and issue numbers are fictional.

```text
┌──────────────────────────────┐
│           Map image          │
│ A Map with a Longer          │
│ Scenario Name                │
│ ● Past crash                 │
│ 3 opens       1 started      │
│ 0 completed   1 crashes      │
│ Last · Continued play        │
│ ──────────────────────────── │
│ Issue   #9001 · Open         │
│ Fix     Proposed             │
│ Test    Not tested           │
│ ──────────────────────────── │
│ Not verified                 │
│ Example Author               │
│ Created 15 June 2010          │
└──────────────────────────────┘
```

The implemented card stays 230 pixels wide and wraps long fields. Each gallery
row uses its tallest card's natural height. Artwork fits a 144-pixel area without
cropping. Grey labels distinguish Never opened, Start unconfirmed, Not completed,
and Outcome unknown. Red identifies an unrecovered confirmed crash; amber
identifies a past crash awaiting its checkpoint test or a resource stop. Green
requires a completed game without unresolved failures. Text accompanies all colors.

Map Details has Play History, Issues & Fixes, and History Scope tabs. The main
trail uses readable open/game/save references; Scope retains full event identities
and historical variant/build records. URL text is underlined and clickable,
with Tab/Shift-Tab and Return support. Existing briefing/community text also gains
links. The existing verification label remains separate.

## Recording and persistence

- `play-history/events.jsonl` is a bounded, private, append-only local ledger:
  directory mode 0700, regular file mode 0600, exclusive locks, stable observer
  identities, duplicate rejection, complete-batch validation and fsync. Corrupt,
  interrupted, oversized, symlinked or non-private storage reports Needs inspection;
  it does not display false green results or guessed zero counts.
- Existing monitor observations supply launch attempts, exact process incarnations,
  confirmed crashes and resource stops. No new process probe, game control,
  compatibility dispatch, or publication path is introduced. Failed process probes
  do not imply exits. A freshly observed process disappearing records an unknown
  outcome and offers the short Record now / Later prompt once. Choosing Later
  leaves the outcome unknown; Map Details remains available for later confirmation.
- Player confirmations select the recorded open and scenario. A new game gets its
  own identity; resuming a known game reuses it. Retirement or all goals count that
  game once. Exact-save reopening and subsequent continued play require the same
  save digest and recorded prerequisites. A chosen save is read only for hashing;
  neither its path nor contents are copied into history.
- Attribute a retained failure to its scenario, phase and failed checkpoint in
  Record checkpoints. Its original incident identity/time are retained. Record a
  proposed or implemented fix separately. A successful fix test must target that
  attributed failure, an implemented fix, and the same failed checkpoint on a newly
  confirmed open after implementation, within the exact archive/variant/build/scenario.
  Record the checkpoint success before recording the test pass. A failed retest
  restores the warning; a different incident cannot inherit an old test.
- Issues link either to an exact scenario/edition or as related original-map
  context. Related links cannot supply exact fix linkage. Refresh issue states is
  an explicit bounded GET operation, sending only issue numbers in the personal
  repository. The last known state remains readable offline, with unavailable or
  stale labels and its check time. Issue closure changes no game/fix-test fact.

Migration imports exact retained monitor sessions and structured map failures in
one batch. Sessions with no confirmed process remain launch attempts; unknown map
identity cannot create a map open. Retained failures without scenario/phase retain
that uncertainty. Legacy verification records supply no invented starts, resumes,
completions or tests. Existing sanitized published crash URLs are related context
because the crash collector cannot identify the selected scenario. Old editions
and builds remain stored and excluded from the current card's counts/confidence.
Raw diagnostics are never copied into this ledger. Resource protection retains its
existing durable evidence before stopping; its history append is deferred to the
monitor's next normal poll, and a history failure cannot prevent that retention.

## UI research

Craig found the first native draft busy and requested a clearer presentation
without losing information. Research consulted on 11 October 2026:

- [Apple: Layout](https://developer.apple.com/design/human-interface-guidelines/layout):
  alignment, spacing, grouping, hierarchy and resizable windows.
- [Apple: Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles):
  concise language, consistent hierarchy and iterative prototypes.
- [Apple: Color](https://developer.apple.com/design/human-interface-guidelines/color):
  consistent meanings and text alternatives for color.
- [NN/g: Visual hierarchy](https://www.nngroup.com/articles/visual-hierarchy-ux-definition/):
  contrast, scale, alignment, proximity and restraint with enclosing borders.
- [NN/g: Icon usability](https://www.nngroup.com/articles/icon-usability/):
  retain meaningful text labels alongside icons.
- [NN/g: Progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/):
  keep frequent information visible and details easy to reach.

Application here is a design judgment reviewed through native prototypes, not a
research-proven usability result for this launcher. All agreed card fields remain
visible. Detailed evidence is available through clearly labelled tabs.

## Validation and scope

Focused offline checks cover event deduplication/restart/concurrent writers,
three opens with one resumed game and one completion, edition/build isolation,
ambiguous exits, warning precedence, explicit checkpoint tests, related issue
scope, offline GET failures/staleness, interrupted/private storage and save/data
privacy. Native checks use injected fictional records and never initialize the
real LauncherWindow application. They cover all 14 example states, long names and
authors, actual gallery reflow at widths 1000/790/550/290, equal row heights,
confirmation forms, a recorded confirmation, the once-only Later prompt, and
click activation with an injected browser callback. No real browser request is
needed for those link tests.

History is read once per background gallery snapshot. Cards use the resulting
plain data and retain cancellable, incremental creation and destruction from the
launcher performance work. The integration check verifies the ledger read stays
off the UI thread, current-build isolation, and visible handling of corrupt history.

Run the offline suite with `.venv/bin/python -m unittest discover -s tests`.
Preview the actual components with `.venv/bin/python scripts/preview_map_history.py` using
a Python with native Tk (Homebrew Python 3.12 on the development machine). This
standalone preview creates only a temporary fictional library, offers 14 examples,
and performs no application discovery, process observation, game launch, network
refresh, or profile operation. Preview confirmations disappear when it closes.

Local screenshots, logs and layout receipts remain in ignored
`.local/issue203-preview/`; they are development evidence, not player data.
Native widget checks do not establish VoiceOver quality, packaged-app behavior,
or live game-exit behavior. No actual gameplay, fix test, compatibility result or
game completion was established during this implementation.

The compatibility programme remains paused: no game tests, worker dispatches,
compatibility RCA/repair or shared runtime/profile changes. Development and verification use this worktree and temporary fictional data.
Private saves, diagnostics and unrelated local changes are preserved. GitHub
publication and merge require the exact personal-fork guard and readback. Source
merge, packaged release, installed-app behavior and real gameplay evidence remain
separate milestones.
