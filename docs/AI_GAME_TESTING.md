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
