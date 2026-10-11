# Crash evidence and cross-map comparison

The installed launcher retains immutable reports privately. Its sanitized issue
contains bounded typed diagnostics, including source/retained counts. A report
fingerprint includes map context and controls existing publication deduplication.
The separate family key below helps compare reports across maps; it does not
change incident identity or automatically consolidate issues.

## What the first cohort showed

Issues [#193](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/193),
[#195](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/195),
[#196](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/196)
and [#215](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/215)
retain every reported thread, image and fault-thread frame, plus 20 fault-thread
registers each. Their fault stacks have 25, 22, 18 and 22 frames respectively;
21, 18, 14 and 18 are supported game frames. Increasing collection limits would
not recover missing evidence in this cohort. The shorter stack summary is capped
at 20 frames; the structured diagnostic contains the additional frames.

All four have an unresolved translated fault PC, no selected-scenario binding,
and no observed operation or triggering-resource binding. Their original reports
also lack a termination object and typed numeric VM information. A larger upload
cannot supply facts absent from the source. #195 was manually submitted and does
not establish automatic reporting. #193 has separate controlled-operation
evidence; that evidence does not make its collector scenario field known.

## Family signature version 1

`crash_evidence.py` consumes validated sanitized payloads. It uses the full
retained fault-thread stack, preserving ordered game frames and duplicates.
Each supported frame requires a nonzero module UUID, matching architecture,
image size and an in-range image-relative offset. It does not translate a PC.

The signature object contains `family_version: 1`, executable SHA-256,
architecture, translation state, exception type, signal, termination namespace,
and `game_frames`: ordered `[uuid, architecture, offset]` triples. SHA-256 hashes
UTF-8 JSON with sorted keys and separators `(',', ':')`. Map/archive/variant/
scenario identity, PID, time, image base, absolute PC and exception addresses are
excluded. The original report fingerprint remains intact.

Coverage is `unknown` with no key when there is no supported game stack, required
identity is missing, a game-frame identity is incomplete, an image reference is
unavailable, or the fault stack was truncated. An unresolved fault PC or absent
termination namespace permits a `partial` candidate key; the missing namespace
is explicitly hashed as `UNKNOWN`. Other sufficient signatures are `complete`.
These labels describe signature evidence, never diagnosis confidence or gameplay
acceptance. Keep the coverage label and limits beside the key.

A matching key nominates a family for investigation. Different keys may still
share a mechanism. Reusing a fix requires a diagnosed defect and an independently
checked applicability rule covering build, resource/model structure, dependencies,
provider resolution and operation. Preserve input identities, originals, features
and saves. Static matches and runtime repair acceptance remain separate results.

## Private preparation provenance

New private session records include optional `catalogue_preparation` when the
supplied profile matches a catalogue entry. Its `game_executable_sha256` is the
build recorded at preparation; `recipe_receipt_sha256` identifies the existing
compiled-recipe receipt when present. Both accept only lowercase SHA-256 values;
missing or malformed identities are `null`. Ordinary preparations have no recipe
receipt. Archive and variant identity remain in the existing session context.

These are catalogue claims captured with the session, not fresh receipt integrity
checks, dependency scans, observed engine providers or proof of scenario selection.
The running executable identity remains separate. A delayed report can reuse the
retained session after catalogue changes. Older sessions remain readable without
this optional member. Existing incidents are not retroactively enriched. The
snapshot stays in private session files; it adds no public payload fields and does
not alter report/family fingerprints or publication grouping.

## Next useful collection work

Track delivery and remaining work in
[#216](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/216).
Prioritize explicit scenario/operation evidence and frozen input/dependency
provenance. Launcher observations and user-supplied context need separate source
labels; an active profile cannot prove the in-game scenario. Any additional upload
field needs strict validation and privacy review. Do not collect arbitrary logs,
save contents, symbols, assets or memory contents to fill these gaps.

Unresolved Rosetta addresses require supported matching-build evidence. Closed
native-capture methods remain closed. Consult the
[knowledge workflow](rea-research/KNOWLEDGE_USE.md) and its coverage limits before
applying a native finding. Neither this helper nor synthetic checks prove a map
compatible or establish that a repair preserves gameplay and save/reload behavior.
