# Compatibility rules

The launcher keeps a user-supplied map archive as an independent, read-only
original. An Original variant copies the archive's install roots into a
complete, separately prepared game profile without the stock profile's saves.
A compatibility edition is a different prepared profile with a different
content identity. The rule engine in `src/smr_launcher/rules.py` changes only
that disposable prepared copy.

Each rule declares a stable ID and positive version, provenance and reason,
exact source archive and game executable SHA-256 hashes, and one or more file
patches. Each patch has a path under `UserMaps/` or `CustomAssets/`, a full
file preimage hash, a unique old byte sequence, replacement bytes, and a full
expected output hash. Rule selection requires exact archive and game matches;
multiple versions of one rule and overlapping or case-colliding paths fail.
The engine validates every patch before writing any. It leaves an already
patched file alone when its output hash matches, so the same rule is
repeatable. The prepared edition ID includes the ordered rule fingerprint
and output asset hash.

The generic byte-patch interface is also used by exact experimental recipes in
[`recipe_service.py`](../src/smr_launcher/recipe_service.py). Current candidates
cover Arizona bank/industry identifiers, Great Lakes industry identities, and
Hill Valley tannery plus additive train-car resources. An offered recipe is not
a gameplay pass. Preparation, warning review and runtime verification are
separate; retain the native Play review's stated limits.

A static inspection result or synthetic fixture does not prove compatibility.
Before claiming a repaired scenario works, verify loading, relevant gameplay,
manual save, normal quit, exact reload and continued play on the named build.
Record archive, game, rule versions, prepared output, observations and date.
Changed inputs require the existing context/invalidation checks; a matching
recipe name alone cannot reuse an old result.

The synthetic fixtures in `tests/test_rules.py` and
`tests/test_variants.py` cover exact applicability, unexpected inputs,
idempotence, overlap, original preservation, and separate compatibility
identity. They contain no downloaded map or game content.

## Risk-based check order

Use the existing scenario evidence contract to choose discriminating checks.
Record setup, trigger, expected result and expectation basis in
`actions[action].protocol`, with explicit applicability and basis. Bind expected
loaded features through `features` and retain the actual scenario/build/resource/
options context on observations. Do not add a parallel verification ledger.

| Changed mechanism | First discriminating checks | Follow-through before claiming the mechanism works |
| --- | --- | --- |
| Industry identifiers and matching ownership references | Exact prepared scenario loads; authored industry placement/model remains visible. Compare unchanged production and placement inputs with the recipe's declared changes. | `industry_construction_production`, `annex_construction`, `train_purchase_routing` and `cargo_transport` for the affected goods; `goals_events` for changed ownership references. Cargo needs separate goods, attached/loaded car and destination delivery evidence. |
| Converted legacy model, including Arizona's Bank candidate | `load` and `camera` show the affected object and its retained geometry/materials. A loading screen alone does not inspect the model. | Exercise the object's applicable authored behavior and animation, then the relevant construction/production/goal actions. Retained feature expectations must name the actual object; a visible substitute is not feature equivalence. |
| Added donor train-car resource family | `load` reaches the exact scenario world; inspect the relevant train-car selection with its authored goods. Resource presence or completed precache alone is insufficient. | `train_purchase_routing`, `cargo_transport` and applicable `era_transition` exercise the actual added versions. Verify expected geometry/materials/animation while used, then save and reopen that same edition. |

Identifier, dependency and model changes prioritize `manual_save`, `reload` and
`continue_after_reload` against a unique exact named file, with normal quit,
maintained relaunch and verified restoration. This order does not assert that a
save path is already admitted or complete. Keep original/current user saves
outside scratch exceptions. Repeated preparation and edition A-to-B-to-A save
retention are separate packaged-integration checks.

Run focused source refusal/positive controls for the changed mechanism and an
unchanged control before its representative runtime case. An original known to
fail is not automatically a safe runtime control. A representative result
supports only its exact context, not every map using the family. Record remaining
per-scenario actions as unresolved until observed, or give a specific reviewed
non-applicability reason; late-game reachability and an unpurchased train do not
justify a pass or non-applicability.

Initial exact-scenario load screening can proceed independently of deeper
construction, AI, era, goal and save work. Scheduling those checks later never
removes final obligations in issue #26 or promotes load-only evidence to whole-
scenario acceptance. Keep crash/stop, normal exit and restoration outcomes
separate from gameplay results.
