# AI game testing on the local Mac

This is an experimental agent driver for a finite local gameplay/save/reopen
test. Its supported launch route is the maintained launcher application service.
The driver is not a general desktop automation service or a compatibility verdict
for other maps or game builds.

Start with the applicable private `.handoff/START_HERE.md`, current Run/STATE and
existing game-queue lease. Preserve the current Run and original failed jobs.
Managed worktrees may use the private handoff location supplied by their owner
instead of a `.handoff` directory inside the checkout.
Verify the native session model, reasoning effort, terminal incarnation and
workspace before admission. A fresh compact execution session avoids carrying a
large project's entire conversation into time-sensitive visual decisions.

## Launcher entry points

The existing command bridge already provides structured status and launch:

```sh
PYTHONPATH=src python -m smr_launcher.cli status
PYTHONPATH=src python -m smr_launcher.cli play PROFILE_ID
```

CLI `play`, the app interfaces and the finite driver use
`LauncherApplication.play`. The driver supplies its existing bounded launch
callback and an asset-matched diagnostic profile copy. It does not click the
packaged launcher UI. Command startup alone does not establish a ready menu or
provide subsequent observation/input ownership. Do not launch the ordinary CLI
as an extra game process while a finite driver owns the queue.

The packaged JSON-lines service has an optional finite dispatch gate on its
existing `maptools.play_preflight` / `maptools.play_commit` pair. Supply
one absolute `launch_deadline` from the
owner's original monotonic action clock. The deadline must be in the future,
at most 480 seconds away at preflight, and identical at commit; it cannot be
extended by changing or replaying the ticket. `prepare_compatibility: false`
launches an existing selected edition. `prepare_compatibility: true` accepts only
an original with one applicable recipe and exactly one intact current cached
edition. Cache lookup is read-only and nonblocking; missing, changed, ambiguous
or known-issue cache refuses without compiling or registering an edition.
The review binds the original selection, plan and actual cached profile; the
commit revalidates all three. Ordinary nonfinite preparation retains its existing
compilation behavior. This dispatch primitive does not supply game-test admission.

The native Swift executable accepts `--play-deadline SECONDS` for a finite
owner's UI test. Pass the same absolute monotonic deadline described above;
the UI forwards it unchanged in both Play requests, including warning consent
and Play Original. Missing, duplicate, nonnumeric or nonfinite values refuse
before service startup. The backend remains responsible for expiry and the
480-second bound. Ordinary UI launches omit this option. This option supplies
neither a UI-test owner nor admission, observer, cancellation or restoration;
those must be established separately before a game test.

The agent driver also supports a separate, exact Orca 1.4.229 pre-input
binding. Its job must include the pinned installed helper, official source,
source/refusal analysis, official-release binary comparison and connection-loop
source. The complete entry validator and each mutable input recheck those files.
The frozen 1.4.224 contract remains unchanged; selecting a new version never
updates or reuses an old job/admission. The 1.4.229 binding does not reuse the
older TypeScript completed-click error settlement: such an error remains
uncertain and cannot authorize restoration. Matching source and official release
bytes is not a reproducible-build proof or runtime/game acceptance. A GUI launch
still needs its distinct owner and an exact reviewed, freshly admitted job.

The service rechecks its owner and the fixed deadline at the application/profile
exchange, settings update and actual `Popen` boundary. Finite launch refuses busy
locks instead of waiting. Native input EOF/disconnection prevents a later
dispatch; an outer owner cancels by closing its dedicated service input pipe.
Queued `workflow.quit` and a `closed` event alone are not cancellation proof.
An explicit `launch_dispatched: false` response is a consumed, pre-dispatch
cancellation, not launch success or proof that the profile was unchanged.
An already dispatched LaunchServices request cannot be undone by this check.
The outer owner must keep its independent observer armed until pending service
work and the exact game identity are positively settled, then perform owned stop
and restoration. Service exit or temporary game absence alone cannot authorize
restoration after an uncertain dispatch. No packaged game pilot is admitted by
these source checks; the existing source-backend driver remains unchanged.

## Gameplay from an owned test save

The separate `ONE_AGENT_PACKAGED_CACHED_GAMEPLAY_V1` purpose uses one packaged
cached Play launch to reopen an exact retained test save and perform relevant
train/car gameplay. It carries forward already observed save/reopen components;
it does not repeat or grant credit for a new save cycle. Its granted stages
exclude new scenarios, filename entry and saving; its owner refuses a second
launch or retained-row double click. Rendered scene recognition remains the
visual actor's responsibility, not a machine scene predicate.

The private job binds the named save's token, complete file facts and a sealed
root settlement receipt for its creating trial. Both prior packaged launches,
both observed normal exits, positive exact game absences and preservation/lease
settlement must match. The receipt must be in the job's immutable baseline.
A successful controller exit, a filename match or an unbound observer alone is
insufficient. The named save is rechecked by the existing final input delivery
validator. A changed or replaced file refuses before input.

Only the five existing named test autosave slots can be declared as mutable.
Every pre-existing declared slot must have exact facts from that settled owned
trial; user autosaves and unknown files receive no exception. The owner retains
each threatened test-autosave preimage using the existing fsynced copy path,
checks every copy before restoration, preserves all named saves, and retains
new files. It does not import a checkpoint or register another profile.

After an independently settled owned gameplay run rotates those test slots, an
optional exact autosave-origin receipt binds the current facts separately. It
requires the matching packaged commit, settled service, exact observed normal
game exit and positive owned process absences, unchanged retained named save,
restoration and released lease. Both origin receipts remain immutable inputs;
the original two-launch save proof is still mandatory. Unknown or stopped-only
runs cannot supply this continuation proof. No history is rewritten.

A runtime grant still needs a concrete short gameplay plan based on observed
supply, demand and geometry, with bounded discovery. The actor must freshly
recognize the reopened world and pause before that useful gameplay. Current
input grammar, one-use admission, 16-decision stages, 390-second work cutoff,
480-second action deadline and 900-second cleanup bound remain unchanged.
A no-input partial takes the existing normal-quit path; unknown dispatch/input
or ownership still holds restoration. No source test or individual observed
component establishes gameplay, cargo delivery or full scenario compatibility.

## Finite visual execution

Use the project's Python 3.12 environment for the driver and all authoritative
clocks. The system Python on macOS may use a different monotonic clock origin.
Finish preflight, knowledge consultation and source reads before launch.

The private operational composition supplies a fresh job, admission and exact
source closure. The repository entry point is:

```sh
PYTHONPATH=src:scripts/research python -B scripts/research/agent_driver.py \
  /absolute/fresh-job.json /absolute/ADMISSION.json ADMISSION_SHA256
```

Use the existing private start helper when it owns queue claiming and STATE
installation; the entry point above does not acquire those on its own. Missing
private authority or a prior unsettled lease is a concrete launch refusal.
The repository alone is insufficient for another machine to run this experiment.
It requires a local installed game, imported/prepared map and profile, granted
macOS Accessibility and Screen Recording permissions, the supported native
provider, and the matching private finite-owner/helper composition. The job binds
their source and resource identities, current native actor/Run, queue, return
state and one-use admission. Those private inputs, game assets, saves, screenshots
and runtime receipts are deliberately excluded from Git. Obtain the current
composition through the existing project owner rather than reconstructing a job
from example identifiers or a consumed trial.
Reuse intact preservation baselines and accepted source evidence. Do not copy
unique user data again unless the next operation actually threatens it.
After Craig has played, use supported checks to establish the current return
profile and live preservation baseline. Capture the current preferences through
the existing owner. Do not reuse the previous trial's live baseline or restore
the profile that was active before personal play. A running unowned game belongs
to Craig and cannot be taken over by a testing admission.

The owner handles Feral Options locally: wait within the original bounds for
one fresh unique semantic Play control at the same index/window in two
consecutive guarded frames, submit it once, then capture the next
scene. Play submission does not establish game-menu arrival. Missing or duplicate
semantics never authorize coordinate fallback or repeated Play.

The owner writes a request for each rendered scene that needs recognition. Inspect the
image and its recorded window/scale, then reply promptly using the existing
one-use paired decision helper. In-game controls use positions derived from the current
image. Never reuse coordinates as proof that a different scene is ready.

An `action`-stage `observed` decision may also carry exactly
`["hotkey", "--key", "CmdOrCtrl+S"]` with receiver `owned_window` after confirming
the new track and funds change. This opens Save As from the same fresh scene,
removing a second decision for the shortcut. It cannot commit a save or quit;
the filename and saving guards still run separately.

Keep the live loop to one scene decision. With composed tool calls, write the
chosen reply, fetch the next request and display its image sequentially inside
one call. Return the new image and its window/scale metadata directly to the
agent. Use the existing paired helpers and image viewer; do not add a controller
or batch game inputs. A pending or ended response carries no new image and does
not authorize resending the previous input. The parent already checks freshness
at dispatch, so a separate per-reply clock diagnostic is redundant.

The action limit is 480 seconds from the original operation anchor; restoration
has its existing 900-second bound. A decision may wait up to 120 seconds, but
input still requires a frame younger than 60 seconds at final dispatch. A late
proposal requests another frame without input. Native mutable calls have their
own bounded completion wait; successful dispatch freshness does not mean that
the game must finish loading within the screenshot's freshness interval.

## Direct catalogue scenario load

`ONE_AGENT_PACKAGED_CATALOGUE_LOAD_V1` reuses the existing direct packaged Play
and load lifecycle for one exact existing one- or two-scenario edition with distinct
selector basenames. It never prepares or selects a cached repair. The fresh job
binds both retained scenario paths, the one requested scenario path and its exact
rendered title/subtitle, edition/build/assets, complete warning and current return.
The target must have no pre-existing saves. Save, Load Game, construction and
purchases are excluded; original observer, input, quit and restoration bounds apply.

Direct and cached LOAD share their existing anchor+120 launch deadline when
waiting for the first game scene. An actor cannot request a partial finish before
any scene exists. If readiness expires, the owner takes its existing cleanup path
without inventing a scene or blind quit input. Once a frame has been accepted,
later loading, normal quit and original390/480/900 bounds remain unchanged.

Select the requested scenario from the current visible game list and verify its
title/subtitle before one Start. A matching profile or main menu does not prove
the selected scenario. Then observe its playable world/HUD and fresh paused state,
quit normally and verify preservation. An absent or ambiguous title means a
no-input partial followed by normal quit. Evidence for one scenario never verifies
the other scenario or establishes gameplay, repair or save/reopen acceptance.

## Gameplay and exact save/reopen

1. Observe the selected scenario and start it. Dismiss initial news, pause using
   the visible pause control and confirm the game's paused banner. Pausing keeps
   scratch autosave rotation out of initial save binding while track building works.
2. Identify the actual endpoint and direction in the current image. Build a short
   straight extension onto nearby clear ground. Reject a red invalid preview
   immediately and use a fresh image to shorten or change direction; long terrain
   searches can exhaust the time needed for final quit. Confirm new rail and a
   funds reduction; purple preview and the starting track are insufficient.
3. Open Save As, enter the job's unique token in the observed filename field,
   confirm the exact text/caret and commit once. Refuse an overwrite prompt.
4. Bind the exact new file through the existing stable-file checks and request
   normal quit once. If the game stays open, inspect a fresh frame and handle its
   actual quit menu/confirmation without saving again; do not repeat Cmd-Q or
   mistake a successful shortcut delivery for exit. After exact process absence,
   relaunch through the maintained launcher backend.
5. Select that exact token in Load Game. Confirm the saved world and first track
   extension, then use its visible direction for a second short extension and
   observe its result.
6. Quit normally. Let the owner stop its children and restore the previous
   profile/preferences. Independently verify restoration before releasing the
   existing lease and admitting another initialized test.

Keep actual visual observations, exact-file identity and terminal cleanup
separate from synthetic/source results. An empirical cycle is complete only
after all six steps. An unknown delivered input remains unknown even if cleanup
later succeeds. Retain the original failure and fix the concrete cause before a
fresh attempt; do not clear flags or reset a consumed job's deadline.

Loading may rewrite the diagnostic copy's `Auto0` through `Auto4` autosave slots.
Only the agent owner explicitly permits those scratch files to rotate after
stable save binding. The exact named target retains its inode, bytes and metadata;
all other files and the owned directory identity remain strict. Original user
profiles and saves stay outside this exception. Generic exact-save helpers retain
their strict default.

The private case records the tested game/map identities, actual cycle results
and repeatability count. This guide does not claim that the experimental route
has met those acceptance criteria.

## Focused source checks

For the first Alternate UK transport acceptance task, the same driver also
supports `ONE_AGENT_UK_TRANSPORT_ACTION_V1`. This is a single-launch operation:
observe the world, establish a station/engine/car/route, observe cargo delivery,
then quit normally and restore. Its distinct admission/review decisions cannot
be substituted with a save-cycle grant. It uses the same 480/900-second bounds
and existing input grammar, with `press-key --key B` for the documented depot
tool during transport actions. It does not run the save/reopen cycle or permit the
paired Save As shortcut. Goods offered, matching cars and actual delivery are
separate observations. Movement or income alone cannot establish delivery.
Raw cargo credit remains false until the owner reviews the actual evidence.

Transport's `action` stage also admits one fixed coordinate zoom-out action:
`["scroll", "--x", "X", "--y", "Y", "--direction", "down", "--pages", "0.1"]`.
Use current rendered world coordinates converted by the frame's scale into
window-relative points, as for clicks; the provider adds the actual window origin.
The pinned native provider emits one downward wheel line for this fixed value.
The owner permits at most two dispatched scroll attempts in the entire operation.
Other stages, purposes, directions, page values and target/focus overrides refuse.
Provider completion does not prove camera movement. Inspect the next image; if
the first scroll has no visible zoom effect, end work with `partial` and quit.
Do not import a checkpoint or relaunch to obtain a preferred starting city.
Choose a nearby route from the actual current world: verify source supply and
destination demand before construction. Bound discovery to at most four actions
and 90 seconds after observing the playable world, leaving decisions and time for
the short connection, second depot, actual train/car purchase, delivery and quit.
No viable pair within that discovery bound means `partial`, not a terrain search.

Transport work ends at 390 seconds from the original anchor, leaving time for
the existing observed quit/confirmation and receipt path inside 480 seconds.
The final input guard rejects new transport work at that cutoff. A dispatched
provider call still uses its original bounded completion wait. A transport
reply with `choice: "partial"` and an empty action ends work immediately and
skips remaining transport stages. It records the incomplete prerequisite and
quits normally; it grants no cargo or full-scenario credit. The same transition
applies when a transport stage exhausts its 16 decisions. Cancellation, lost
ownership and unknown delivery still use the existing refusal/cleanup path.

Run with the repository's Python 3.12 development environment:

```sh
PYTHONPATH=src:scripts/research .venv/bin/python -B -m unittest discover \
  -s tests -p test_agent_driver.py
.venv/bin/python -B scripts/research/individual_stop_support/check.py
.venv/bin/python scripts/check_quality.py
```

The driver controls exercise the composed operation and input refusals using
synthetic observations. The isolated observer checks exercise normal exit and
refusal without signaling real processes. Reuse accepted results when the
relevant source and inputs are unchanged. Runtime evidence must separately show
the actual full cycle and preservation of its current return state.

The repository skill is
[smr-game-driver](../.agents/skills/smr-game-driver/SKILL.md).


The separate `ONE_AGENT_PACKAGED_CATALOGUE_MENU_V1` purpose tests one existing
catalogue edition through the released backend's `--native-service` Play API.
Its exact grant binds the signed package, target edition/assets/build, current
return profile and file preimages. It fixes `prepare_compatibility=false` and
requires an empty warning. The root never activates the target before Play.
The independent observer arms before the single commit; the owner binds the
new game's birth and executable, then closes and reaps the service before
menu interaction. Unknown unbound dispatch retains the observer and holds
restoration, even when no game PID is visible.

This purpose allows fresh Feral Play, one visible Credits or Options submenu,
Back/Escape and normal quit. It forbids scenario selection, loading, gameplay,
saving, settings changes, copying, registration and recipe creation. Menu work
ends at 390 seconds; action and restoration retain the original 480/900 bounds.
After exact owned absence and settled dispatch/input, the maintained profile
switch returns to the captured current profile. Only target Settings.ini and
the two declared global preference files are restored; their postimages remain
in private evidence. Existing saves in both editions and sibling catalogue
profiles must match; every new target file stays intact. Foreign activation,
a journal, changed existing data or uncertain dispatch holds restoration.
This is packaged **backend** acceptance, not a Swift UI click or map gameplay.

Focused offline controls: `python -m unittest discover -s tests -p test_catalogue_menu.py`.
The temporary service and profile fixtures do not admit a live trial or prove
that a game reached its menu. Runtime observations require separate review.


`ONE_AGENT_NATIVE_GUI_CATALOGUE_MENU_V1` is the closed native GUI alternative.
It requires the reviewed Orca 1.4.229 contract, the existing MENU `agent_save`
metadata shape, one existing original catalogue target, and a distinct current
return. It does not save a game. `catalogue_menu.native_gui` pins `display_name`,
`frontend_sha256`, `initial_warning`, and `launcher_files` (exact facts or absence
for `update-preferences.json` and `updates/pending.json`). The ordinary target,
package, preservation and mutable-preimage fields remain required. The final
no-preparation warning is `catalogue_menu.warning`; it must not be substituted
with the initial compatibility warning. No cached preparation descriptor applies.

Released artifact verification remains the default, with no fallback on refusal.
Only this native GUI purpose also supports explicit package `artifact_mode:
"local-development"`. Its exact private build receipt binds base `5419a743`,
the complete tracked/nonignored working-tree source and modes, the maintained
`./scripts/build_macos.sh` invocation and pinned build dependencies, and the actual
output bundle digest including internal symlinks and signature files. The frontend
and bundled helper have separate exact digests. The closed output directory is
`.local/native-gui-development`; never install this build over `/Applications`.
Live deep strict codesign verification remains required, followed by actual ad-hoc
identity checks on the bundle, frontend and helper. A local receipt contains no
release tag, ZIP or Gatekeeper claims. Mixed fields, another purpose, source drift,
missing/changed bundle contents and non-ad-hoc signatures refuse before launch.
The source snapshot includes all accumulated local changes, rather than treating
HEAD as the built source. Preserve the actual builder/toolchain receipt privately.

Local GUI selection uses the visible direct Issues & Evidence section button and
fresh full expanded archive/variant/build identity above the complete warning.
It has no popup or scrolling fallback. Provider treeText may omit native button
labels, so current rendered recognition remains required. Local app testing is
separate from released-app acceptance and still needs a fresh reviewed finite job,
current ownership/availability, platform permissions and runtime admission. All
dispatch, observer, normal quit, restoration and save-preservation rules below
remain unchanged.

One exact LaunchServices frontend receives the immutable anchor+390 deadline.
The owner binds its native lease and its Swift-owned backend's PID, birth and
executable. Fresh visual decisions select the exact original card; substring
search results need not be unique. Current UI archive/variant/build evidence is
required before Play. In finite `--play-deadline` mode, header Play and the
Original choice only obtain reviews. Even an empty warning requires explicit
final confirmation; ordinary nonfinite no-warning Play is unchanged. The owner
validates the fresh exact final sheet/control, then arms the observer once outside
the input mutation guard. Actor and preflight review time consume the original
390-second work interval without consuming observer readiness. The conservative
arm timestamp is recorded before the maintained arm call; failure or a frame that
expires afterward cannot renew it or re-arm.
If the current Issues & Evidence body places provenance below the viewport,
GUI selection permits only `perform-secondary-action --element-index <fresh-area>
--action "scroll down"`, with receiver `provenance_control`. The current detail
scroll area must advertise that action and contain the exact target title and
Issues & Evidence body. At most two dispatches are counted in the final input
hook after cancellation/freshness guards, before attempted input and Popen. This
allowance does not use the transport scroll grammar or counter. After each action,
the actor must inspect the fresh viewport: stop if it did not move down; use a
second action only if the first visibly advanced and metadata remains below.
This movement judgment is actor protocol, not machine scene proof. Exact metadata
is still required before observing selection or Play; pre-Play HOLD is unchanged.

The single original request is published inside the validated final confirmation
ingress, before that exact launch input. Its real
awaiting-binding acknowledgment must arrive within the original ready+20 window;
the conservative caller cutoff starts before arming and never renews. The existing
input guard is reused without nesting another mutation guard. Late acknowledgment
refuses final confirmation input.

After header Play, a short mechanical sequence reads the exact initial warning/identity
and uniquely named Play Original Edition, then a fresh distinct final warning and
Launch This Edition Anyway. Both reviews remain preflight-only. After the final
control is validated, one arm and the actual request acknowledgment precede its
input. Provider, identity, frame, cancellation and deadline guards run again after
acknowledgment. Fresh target-correlated success and exact game binding must then
finish within that single request+20; review time before arming does not extend or
reset either observer clock. An empty or changed warning still refuses the pinned
review; the frontend must never dispatch it implicitly. It reuses native
frame/input/validation and the pinned authorized bodies, with no coordinates,
assumed advancement or intervening model replies. Its receipts are machine
evidence, not fabricated actor visual decisions. Missing/changed/ambiguous
observables remain HOLD. Ordinary observer binding must finish within the single
original request+20 window; the caller cannot override the frozen deadline.
Native confirmation sheets may enumerate as a second untitled frontend window.
Only the two mechanical review reads can retain the already bound Map Library
receiver in that exact owned pair. The raw enumeration stays intact; unrelated,
hidden, replaced or additional windows refuse. The fresh AX sheet must contain
the complete expected review label and its exact consent button. Whitespace
flattening is allowed, but warning text, edition and build must match in full;
the longer initial warning cannot substitute for the final warning. Game-window
selection and all original input, observer and cleanup bounds stay unchanged.
No source `app.play`, direct game open, preparation, import or save is permitted.

Acceptance combines a fresh target-gated UI success transition, current exact
selection, active profile/assets, one expected game birth in the original interval,
and positive observer binding. A generic status or PID alone grants nothing.
The observer stays bound while normal launcher Quit settles both frontend and
backend absence, excludes updater/helper work and reaps only the owned open child.
Backend absence is recorded without invented EOF, returncode or protocol response.
Then the existing visual menu, normal game quit and preservation/restoration path
runs under the original 390/480/900 bounds. Unknown dispatch remains HOLD even if
the GUI dies; neither `service=None` nor a frontend-only exit permits restoration.
Draft/update prompts receive no automatic consent. A missing observable or route
ends the attempt; there is no selector fallback or replay.

Run `python -m unittest discover -s tests -p test_native_gui_menu.py` for public
synthetic controls. Set `SMR_GUI_COMMON_ROOT` to the retained reviewed common tree
to also run the composed checks against its actual frame, frozen Wrapper/Observer
state machine and lifecycle implementation. The fixture steps the original API
over temporary receipts and a simulated monitor; it does not fabricate ready,
awaiting-binding, bound or normal-exit acknowledgments. Delayed eight-second actor
responses must still bind within 20 seconds; readiness/binding overruns refuse
new input. Those checks simulate OS/provider boundaries and use temporary
profiles; they never launch the installed app or game. Without the private tree,
those composed checks are explicitly skipped. Source checks do not establish a
live GUI launch, game menu arrival or gameplay acceptance.


`ONE_AGENT_PACKAGED_CACHED_MENU_V1` is the separate original-to-cached menu
purpose. Its grant binds the exact original selection, prepared target, recipe
preparation descriptor and full warning text. It uses `prepare_compatibility=true`
and refuses any changed preflight warning or plan before commit. The prepared
target, original canonical/prepared data, return state and all saves are pinned.
It reuses the same service/observer/menu/quit/restoration path and finite bounds;
it cannot compile, create an edition, start a scenario or load/save a game.
Current input/output recipe receipts establish file correspondence, not effective
engine resource precedence or gameplay compatibility. A new released package and
fresh exact admission are required before testing this purpose live.

`ONE_AGENT_PACKAGED_CACHED_LOAD_V1` separately permits loading that exact repaired
scenario through the same packaged cached Play path. Its grant additionally
binds the reviewed rendered scenario title. The visual actor must verify that
title before starting once, then observe the playable world and HUD and pause
using a visible control. This first pilot requires no pre-existing target saves,
so automatic saving cannot rotate a user's save; every new file is retained.
No construction, purchases, loading a save or saving is
permitted. An unavailable title, incomplete load, 16-decision cap or original
390-second work cutoff leads to the existing normal-quit path, without extending
the 480/900-second bounds. An uncertain dispatched input still holds restoration.
World observation, normal exit and preservation require independent review;
they do not establish gameplay, repair completeness or save/reopen compatibility.

An explicitly reviewed load-only job may bind the existing `WindowProbe inspect`
helper for one lost-window observation. On the first empty provider window list
after a known frame, it records that exact old window's metadata and current
process identity, waits briefly, then makes one provider follow-up. If still
empty, one final metadata read ends the observation through owned cleanup.
The total cap is ten seconds, further bounded by the original work/action
deadline. Natural window recovery resumes fresh frame validation and the LOAD
normal-quit path; it never expands into saving or reopening. No activation,
unhide, bundle open or focus retry is permitted. This helper addresses one
window ID and omits visibility, alpha and sharing state. Missing old-window
metadata is inconclusive about replacement windows, crashes or hangs. Owned
cleanup stop is distinct from a normal quit and earns no normal-exit credit.

`ONE_AGENT_PACKAGED_CACHED_SAVE_REOPEN_V1` is the distinct unique-save/exact-reopen
integration purpose. It uses two recorded packaged Play transactions in the same
480-second operation: first normal exit and exact stop must precede relaunch;
second normal exit must precede restoration. The second packaged launch has the
original 390-second cutoff, including its final dispatch boundary. Both launches
retain separate service/observer receipts. The existing exact-file save-cycle
helper revalidates the unique save immediately before every relaunch/reopen
delivery. There is no source-backend fallback, construction or transport stage.

The save pilot accepts a target with no pre-existing saves, without declaring or
copying a prior autosave. Its fresh complete preservation baseline must still
match before launch. New named saves and autosave slots remain in that edition.
Otherwise it requires the exact owned `Auto0` from a previously settled trial,
with its original readback and current file facts bound in the job. A successful
two-launch trial requires both exact committed service responses, settled services,
matching job and distinct game identities, both observed normal quits and positive
game/service absences, plus terminal, preservation and lease settlement. A zero
return code alone does not qualify an autosave. A partial
trial can supply that provenance only after its first normal exit, both services
have settled, and root preservation and lease settlement passed. The second
service must either have sent no commit, or have launched the exact known target
with its distinct owned game independently proved absent. An owned cleanup stop
can establish that absence; it cannot earn normal-quit or full-cycle credit.
This does not turn the failed invocation into reopen success.
When that prior `Auto0` exists, the pilot preserves it durably in private evidence
before launch; the replacement autosave remains in the target edition. Restoration verifies the
old copy and never writes it over the newer file. Every other pre-existing save
remains subject to strict unchanged-file checks, including named saves retained
from an earlier attempt. A target with pre-existing saves and missing or unproven
autosave provenance refuses admission.
The named new save keeps its strict file identity through
reopening. The already-preserved `Auto0` and scratch slots `Auto1..4` created within
this invocation may rotate; pretrial `Auto1..4` still refuse admission. This is a
focused preservation rule for owned test data, not a general save backup or import
path.

An observed paused world may pair with opening Save As once. The filename field,
exact token, save commit and exact reopened row/world still require their own
fresh evidence. An empty `observed` reply for the exact focused filename field
authorizes the owner to submit Return once from that same frame after capturing
the before-save inventory. It does not require a second model decision. The
existing final delivery fence rechecks the exact mapping, receiver, frame age,
cancellation, target absence and original deadline. A proven stale frame with no
input uses the existing fresh `saved` decision; an unknown dispatch never retries.
The private commit receipt distinguishes mechanical input from agent observation.
On reopen, match the saved world/HUD and use the current visible
pause control if needed; do not assume the saved pause state persists. Incomplete
world/filename/reopened prerequisites enter normal quit with a no-input partial;
unknown dispatch or failed exact-exit evidence retains its original failure and
cleanup hold. Successful source controls do not establish actual save/reopen
acceptance, gameplay or a complete scenario.
