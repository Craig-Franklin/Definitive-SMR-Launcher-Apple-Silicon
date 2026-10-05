# Compatibility audit and implementation plan

Research snapshot: 4 October 2026, local date. Target: Feral Mac build
222106.42031, executable SHA-256
`981ca7180759d8578babf54cd9a4f76b93db503b33a7f1938c6b1427b95680f0`.

## Decision

Build a scenario-aware data preparation service on top of the existing immutable
imports, versioned rules, prepared editions and isolated saves. First compute the
definitions the engine is likely to load, then diagnose typed references, and
only emit a translation when its intended behavior and applicability are known.

The strongest first repair candidate remains a separately labelled Corn and
Cattle industry-ID edition. The investigation has also found independent goods,
car and annex failure paths. Solving the industry-ID problem alone cannot certify
the whole library. A successful loading screen transition cannot certify later
construction, transport, era transitions or saved-game reconstruction.

This is a plan and a completed static research audit. The new analysis is not yet
integrated into Import or Play, and no new repair is approved for automatic use.

## Evidence and coverage

The whole main executable was analyzed/exported, with 151 explicitly recorded
failures. Only selected paths have been interpreted and independently checked;
this is not complete engine understanding. See [Engine map](ENGINE_MAP.md).

The read-only [library auditor](../scripts/research/audit_map_library.py) examined
221 original imports, represented by 223 catalogue editions, containing 227
scenarios and 2,579 loose XML files. The final measured pass took 2.984 seconds
on the development machine. This is an observed warm local scan, not a promised
time on other machines. No game or GUI was launched and no map was rewritten.

| Static finding | Imports flagged | Coverage / interpretation |
| --- | ---: | --- |
| Industry identity outside the fixed table | 128 | Industry definitions could be reconstructed for at least one scenario in 205 imports; 118 imports explicitly place an outside-table type. |
| More industry definitions than the 36-name table | 7 | A bijection for all declared types is impossible; reachability could reduce requirements but has not been proven. |
| Production good absent from reconstructed numeric registry | 5 | Industry/goods pair resolved for at least one scenario in 197 imports. |
| Registered good without a matching goods car | 22 | Goods/car pair resolved for at least one scenario in 195 imports; actual transport reachability remains unproved. |
| Rural industry without derived annex definition | 45 | Industry/depot pair resolved for at least one scenario in 198 imports; actual annex eligibility/reachability still needs checking. |
| Bridge identity outside the eight-name table | 6 | Bridge definitions reconstructed for at least one scenario in 198 imports. The definition may be ignored; this alone does not prove a crash. |
| Recognized bridge maximum span at least 10 times stock | 12 | Review heuristic, not a demonstrated engine limit or repair recommendation. |
| XML could not be parsed/read under the auditor's restrictions | 11 | Seven imports have an unreadable scenario; other errors may concern unused files. Feral parser tolerance is not simulated. |
| Explicit scenario XML reference unresolved | 32 | Missing or ambiguous loose candidates; this is incomplete analysis, not a proven engine lookup failure. |
| Duplicate or empty definition keys | 12 | Effective definitions for the affected factory are left unresolved; same-file fieldwise merging is not simulated. |
| Duplicate XML basenames | 2 | No engine search priority is guessed. |

Counts overlap and are not crash counts. Coverage means **at least one scenario**
in an import could be examined for that check; sibling scenarios may remain
unresolved. Twenty-seven imports have no findings from these checks; none earns
a runtime verification badge. No custom FPK files were found in these imported
trees, but stock packed assets and model-internal references remain outside the
scan. Other future packages can contain FPKs.

The earlier narrow industry screen flagged 131 imports. The broader audit flags
128 after using reader/merge semantics and declining to infer effective records
from ambiguous or duplicate definitions. The difference does not indicate repairs
or improved stability. Keep reports tied to their analyzer version and coverage.

Additional review findings include 34 imports with car definitions naming goods
outside the reconstructed registry and one case-only spelling review, one with duplicate car-to-good coverage,
and 21 with goal-good spelling differences. Eighteen of those goal reports have
case-only matches and three have no case-insensitive match. Goal lookup semantics
have not been traced, so these are review leads rather than mandatory rewrites.
The missing standard `Terminal` check found no affected import in resolved data.

### Representative cases

- **Corn and Cattle: Nebraska Rails:** 25 industry types, 22 outside the table;
  current audit flags the industry family without adding a goods/car/annex issue.
  This supports a controlled single-mechanism experiment, not a promised fix.
- **Austrian Hungarian Monarchy:** production text ` Bulk Wine` differs from
  `Bulk Wine`; goods-car coverage also needs investigation. Do not silently trim
  until lookup normalization and the intended reference are established.
- **MID EAST 2, AZ Canyon V02, Valencia DSC, Styx:** production-good findings
  demonstrate why the effective registry must be checked after merging/pruning.
- **Alternate Balkans:** unresolved bridge XML reference remains a candidate
  dependency issue. Its previous filename-only experiment failed loading; do not
  repeat or promote that rule. Inspect the newly reachable dependency chain first.
- **San Francisco Bay:** a goal uses `mail` while the registry uses `Mail`.
  Existing short gameplay/save evidence prevents treating this spelling screen
  as proof of instability.

The seven capacity cases are Central Mexico 2 (39 types), The High Desert (46),
Appalachia Rails (65), My Old Kentucky Home Redeaux (39), The Great Northwest (46),
WNYP Revision 2 (43), and My Old Kentucky Home (44). Thirty-six is the total
recognized industry table size, not a promise that every slot can safely stand
in for every custom type. Preserve these maps intact. Any reduced-content edition
requires explicit labelling and a demonstrated reachability/behavior rationale.

## Engine mechanisms driving the design

Offsets below are relative to image base `0x100000000`. These descriptions are
original analysis; raw game pseudocode/assets remain private.

| Mechanism | Evidence | Consequence for analysis or translation |
| --- | --- | --- |
| Missing industry ID becomes `0xffffffff`; animation grouping grows toward the unsigned index | `LoadIndustries +0xd87980`, `ConstructObject +0xd890a0`, `RandomizeIndustryAnimations +0xd89c00`; sampled runaway allocation at `+0xd89d52` | Validate registry membership and actual instantiated/animated types. Exact failing runtime name remains uncaptured. |
| Missing production good leaves index `-1`; classification reads before the vector | `AddProduction +0xd843b0`, `CreateGood +0xd79cd0`, assembly read `+0xd79df4` | Require production closure against registered goods; case-insensitive NONE is the sentinel. Empty references need separate review. |
| Missing goods car returns null; attachment dereferences it | Factory `+0xe90e40`; caller `+0xe9c951` then `+0xe9c959` | Validate transport-good-to-car coverage. Precache tolerates null, so loading is insufficient. |
| Missing annex/depot returns null; creation event dereferences it | `CreateDepot +0xd56fd0`, event `+0xd6a3e0`, dereference `+0xd6a4c2` | Validate derived annex and standard depot identities. Precache tolerates missing depots. |
| Standard start-track branches construct Terminal and use the result | `InitNewGame +0xe140b0` | Detect scenario pruning that removes the required depot; reachability depends on the startup branch. |
| Only eight ordered bridge identities are emitted | `LoadBridges +0xe7e950`, table loop `+0xe7f45e` onward | Track discarded custom keys separately from usable bridge layouts; do not blindly alias bridge types. |
| Saved cars/depots reconstruct using current definitions | `CreateTrainCar(stream) +0xe90f80`, `ConstructObject +0xe90bd0`, depot `+0xd57140` | Freeze each prepared edition and its saves; never silently replace definitions beneath old saves. |
| Car model/dummy names derive era suffixes | `ConstructObject +0xe90bd0`, `CreateCar +0xe90890` | Resolve derived assets, including packed files; checking literal XML filenames is insufficient. |

The unsafe goods/car/annex paths are supported by decompilation and selected
independent disassembly. They have not been reproduced on every flagged map.
These are behaviors of the inspected Mac executable; Windows-versus-Mac
differences have not been established by a Windows binary comparison.

### Loader rules that a resolver must preserve

1. Resolve the first/global reader separately from the scenario reader. A map's
   `RRT_Goods.xml` can provide the global definitions; always using untouched stock
   as the base would give incorrect results.
2. A distinct populated scenario goods list filters the first reader's names.
   New scenario-only goods are parsed but do not enter the numeric registry.
3. Goods-car, tender-car and depot subtrees merge named records, then a populated
   scenario subtree removes base records omitted from it. New scenario records
   survive. Cars and tenders are handled independently.
4. Missing scalar fields inherit. Certain list fields replace inherited lists
   only when their first child exists; an empty container can preserve old data.
5. New industries default to rural unless overridden. Boolean parsing accepts
   true/false or integer text, rather than only literal 0/1.
6. Annex naming searches for the substring ` Annex`, not merely an ending suffix.
   String normalization still needs further tracing.
7. Duplicate records, filename collisions, unknown lookup precedence and parser
   discrepancies must remain unresolved. Never turn a guessed union into a pass.

## Initial launcher end-to-end observation

The installed launcher v0.6.0 was used to select the separately labelled **Corn
and Cattle — Experimental industry IDs v1** edition and press Play. The game
loaded a fresh scenario with zero AI opponents, wrote an autosave and a uniquely
named manual save, then quit normally. A second Play from the launcher reopened
the same edition; the named save loaded and the simulation advanced into later
months. The original profile's full file manifest matched after restoration, and
the test save remained with its exact edition.

Observed peak physical footprints were approximately 1.80 GB for the fresh
session and 1.60 GB for the reload session. Both were watched under a 6 GiB limit.
The first launch still required selecting Corn in the game's scenario menu:
activating map assets in v0.6.0 did not set the scenario selection. A subsequent
source-GUI test confirmed the new Play implementation corrects a stale stock
selection, skips intro movies and disables the fixed-stock Quickstart path.
It preserves a valid prior choice within a multi-scenario package and leaves
Original Game preferences unchanged. This source fix is not yet published.

This is load/save evidence for one exact experimental output, not full gameplay
verification. Track construction, trains, bridges, goals, era transitions and
inherited-field behavior have not passed validation. The edition keeps the
**Not verified** status. Original maps and the game executable were unchanged.

A preceding automated Corn run retained its timeout result: OCR read the HUD's
1945 date as `19+5`. The driver now tests this observed glyph narrowly, requiring
a full month name and a separate currency field in the top-right HUD, following
an exact scenario-title match and two positive observations. Failure reports now
preserve the last screenshot and bounded OCR geometry for diagnosis.

### Independent Corn v2 observation

A second, separate experimental edition restores audited custom-record defaults
after applying the industry and annex identities. It adds 52 explicit industry
scalar values (50 empty strings and two false booleans), clears eight inherited
annex creation strings, and clears one inherited alternate-load animation table.
Original explicit model, production, placement and display-name data remain
unchanged. Static comparison includes first-child scalar selection and
child-present list replacement; an empty list container alone does not clear an
inherited list. This recipe remains private and is not applied automatically.

The actual source launcher GUI selected and launched this edition. Automatic
scenario selection, movie skipping, exact-title confirmation and two positive
HUD observations passed. A short session built a track extension, opened the
custom train-selection screen, wrote autosaves and a named manual save, and quit
normally. A second Play from the launcher loaded that named save with the track
and cash balance intact; simulation continued into March 1948. Observed peak
physical footprints were approximately 1.70 GB and 1.48 GB. Original Game's full
file manifest matched after restoration, and the edition retained its own saves.

Train operation, cargo delivery, annex construction, bridges, goals and era
transitions remain untested. Some English interface text still uses stock alias
names. The edition therefore retains **Not verified** status. V1 evidence is not
transferred to this different output identity.

Further engine review ruled out two shortcuts as complete repairs: removing
animation event names does not avoid the invalid-index access, which occurs
first; replacing animated models with direct NIFs removes their event-controlled
visual behavior and leaves independent invalid-ID problems in build-menu
duplicate checks and icon lookup. No map-only replacement for the fixed name
registry or safe English build-menu naming hook has been established.

### Test driver calibration

The current driver passed a stock Southwest calibration in 18.949 seconds,
including exact original-profile restoration. Regressions address a stale Feral
Play control, small setup controls missed by full-frame OCR, and adjacent speed
icons grouped into the HUD date. Title and separate currency/date assertions
remain required. Process exit and cleanup races only count as absence when an
independent process check proves it; an unidentifiable live process or group
continues to block restoration. Earlier failed attempts retain their original
outcomes and recovery records.

## Implementation stages and completion gates

### 1. Versioned scenario resolver and diagnostics

**Current:** research CLI implemented and run across the library. Fifteen synthetic
regressions cover registry pruning, overrides, vector inheritance, ambiguity,
capacity, root validation and boolean/default behavior.

**Next:** move tested logic into an application service. Trace and implement
resource-manager precedence before claiming exact effective definitions. Add
typed edges for cities, goals, trains/tenders, events, terrain and model assets.
Index stock/map FPKs read-only using a validated format reader; preserve unknown
formats. Validate scenario roots, factory roots, duplicate merge behavior and
engine XML tolerance against focused fixtures.

Each finding must carry scenario identity/hash, consumer field, source candidates,
selected source and hash, resolution confidence, engine mechanism, severity and
coverage. Cache by analyzer/resolver versions and stock dependency hashes.

**Gate:** no false compatible result for an unresolved dependency, and one
scenario's clean result must not apply to sibling scenarios.

### 2. One narrow industry-ID compatibility recipe

Start with the independently prepared Corn candidate. Build a deterministic
injective alias plan only after resolving all industry references, used/reserved
identities, omitted fields, in-city/rural behavior and display-name paths.
Update typed industry/annex references, not arbitrary substrings or prose.

Refuse automatic translation when slots are exhausted, dependencies are unclear,
references may be embedded in uninterpreted assets, inherited defaults would
change behavior, or several scenarios sharing files require conflicting aliases.
Do not remove industries or alter terrain/goals to make the count fit.

**Gate:** exact preimage/allowed-diff checks, deterministic output, idempotence,
source preservation, isolated fresh saves, bounded load and gameplay/save evidence.
An initial loading success proves only that edition's load result.

### 3. Compatibility build service and cache

Use existing `rules.py`, `variants.py` and guarded activation. Add a build
descriptor rather than inferring edition kind from its display name.

```text
build key = hash(
  original archive + extracted-content manifest + scenario scope,
  game executable + resolved stock dependencies,
  resolver version + translator version + selected rules + options
)
```

Publish outputs atomically with full manifests and provenance. Reuse only intact
matching outputs. Interruptions leave recoverable build state. A stale build
creates a new edition; retain the old edition and its saves. Use the existing
transaction manager as the sole active-profile switch path.

**Gate:** cache invalidation, changed stock assets, tampering, interrupted publish,
concurrent preparation and saved-game retention all have targeted regressions.

### 4. Import and Play integration

Import preserves Original and records per-scenario diagnostics. Play validates
the chosen edition and reuses its cached build. Prepare again only when inputs
change. Experimental editions remain explicit choices until appropriate tests
justify promoting a particular rule for particular inputs.

Use separate user-visible evidence for static analysis, experimental preparation,
fresh load, gameplay/save checks and known issues. Automating load tests must not
grant the existing full Verified badge. Replace verification's current free-text
scenario scope with exact scenario identity to avoid package-wide overclaims.

**Gate:** repeat Play needs no full retest; old saves open only their matching
edition; failures leave active data untouched; existing installations migrate
without losing prior evidence or saves.

### 5. Representative runtime regression suite

Run static checks in the background and cache by content. Run live games serially
when the desktop is available; the supplied GUI driver has passed a stock Southwest calibration. Changes to
the driver invalidate that calibration and require another stock run.
No working headless arbitrary-scenario validator has been established.

| Test cohort | Purpose |
| --- | --- |
| Stock Southwest U.S. and previously working San Francisco | Calibrate selection/loading; protect known-good behavior. |
| Corn industry-ID candidate | Test one traced failure mechanism with exact independent output. |
| Missing production-good case | Validate effective reader/pruning analysis and a separately reviewed repair. |
| Missing car and annex cases | Exercise train attachment and annex placement after loading. |
| Bridge changes and Alternate Balkans | Water crossing/bridge construction, dependent resources and save/reload. |
| Multi-scenario, duplicate-key and capacity-exhausted packages | Confirm refusal/uncertainty; do not destructively simplify. |
| Era change and saved-game reconstruction | Exercise derived model variants and exact-edition continuity. |

Every run records exact inputs, selected scenario, test implementation/settings,
positive scene/action evidence, memory/time trace and termination reason.
Differentiate a menu/harness timeout from a game crash or memory runaway. Retain
bounded watchdogs and full profile restoration. Longer play remains a separate
evidence tier. Expand across the corpus after representative gates pass; rerun
only tests affected by a changed dependency/rule.

## Remaining research queue

- Resource-manager search order, case/whitespace normalization and global overrides.
- Packed-resource indexing and embedded NIF/KFM/animation references, including
  engine era suffixes and missing-scene fallback behavior.
- Train-car pre-cache closure: derive base/v1/v2 model, dummy, texture and
  animation dependencies from each scenario's train-car XML, then distinguish
  loose-file absence from stock FPK or embedded-resource resolution before
  proposing an additive compatibility rule.
- Terrain/image geometry and dimensions, location coordinate transforms, numeric
  bounds, track/bridge allocation loops and crossover behavior.
- City placement, goals/events, economic timing and runtime industry creation.
- Save serialization and backwards-compatible migration feasibility; retain
  strict edition isolation unless a migration is separately established.

These are explicit coverage gaps. There is no blanket XML/FPK conversion, universal
stability guarantee, or automatic save migration in this plan.

## Reproduce the static audit

```sh
.venv/bin/python scripts/research/audit_map_library.py --installed-library \
  > .handoff/evidence/compatibility-library-audit.json
.venv/bin/python -m unittest discover -s tests -p test_map_library_audit.py -v
```

The JSON contains local source paths and per-map findings; keep it private. The
development snapshot also has a private CSV matrix with one row per original
import. Only original tooling, aggregate findings, analysis and synthetic tests
belong in the public source tree.
