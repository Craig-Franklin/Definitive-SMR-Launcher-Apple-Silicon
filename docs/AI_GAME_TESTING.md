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
