# Loading feedback

A wait must be visible where its result will appear. Empty Map Library, Collection,
Map Details and Activity areas show a loading panel in the content area. Maps show
honest preparation counts; their cards then appear in bounded batches, with
remaining rendering progress beside the heading. Collection fetches have unknown
length and use an animated bar. Existing results stay visible during a refresh.
Failures replace the loading message with an explanation; retry restores progress.

A persistent activity bar also covers work that spans screens or operates on
existing content. Each concurrent operation owns a separate token. Completing one
operation cannot clear another operation's feedback. Foreground actions take
precedence over background maintenance. Unknown durations animate; known totals
show counts rather than an estimated percentage or invented remaining time.
Dialogs share this feedback, so their actions remain visibly active.

| Area | Work and feedback |
| --- | --- |
| Startup | Native loading shell and animated activity bar before discovery/history/monitor work |
| Map Library | Centered preparation panel with checked/total counts; cards appear in bounded batches; rendering count remains until complete |
| Collection | Centered panel before saved/online catalogue arrives; refresh indicator preserves existing rows; separate cache/source/community activity |
| Downloads/imports | Indicated fresh game/build preflight before bulk network reads, per-map download/import states, cancellation and final counts; indicated current-byte import-check reads after import |
| Details | Loading dialog appears before current metadata, verification, import-check and compatibility reads; closed dialogs stay closed |
| Activity | Loading panel before background history read; subsequent search filters the loaded history locally |
| Language/voice | Indicated voice enumeration, settings saves, system settings, translation and speech; setting controls are disabled during serialized work |
| Updates | Indicated checks, download/verification/staging, settings saves and final validation before quit/install |
| Crash reporting | Indicated preference saves and bounded pending-report reconciliation; existing protections stay enabled |

Worker callbacks publish plain data. Tk variables, widgets and progress state stay
on the UI thread. High-frequency preparation progress uses one coalescing slot;
stale generations cannot revive cancelled views. Gallery snapshots still prepare
current metadata/verification/art together before sorting and filtering the complete
result. This change does not stream partially verified records into sorting or
relax the current Details/Play checks.

For new asynchronous work, use a descriptive `_background(..., label=...)` or
`_submit(...)` label and pair empty content with a regional loading panel. Settle
feedback on success, failure, cancellation, navigation and close. Native external
file pickers, confirmation dialogs and system/browser windows own their own UI;
the launcher does not estimate progress inside another app.

Validation uses temporary libraries and injected services. Unit regressions cover
concurrent operations, delayed work, failures, empty results, progress counts,
regional retry and navigation, worker-start failure cleanup, Quit during launch
inspection, closed details dialogs and update-install failure handling. An
isolated native Tk smoke observes animation, preparation counts, partial card
rendering and independent foreground/background completion. These checks are UI
feedback evidence, not installed-library throughput or gameplay evidence.
