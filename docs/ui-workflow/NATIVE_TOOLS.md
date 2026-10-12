# Integrated native tools

Craig requested one coherent native interface for all launcher tools on 11 October
2026. The accepted Design B grid and inspector remain the design foundation.
[Issue #221](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/221)
holds implementation and acceptance; this document maps the destinations rather
than creating a second active checklist.

| Capability | Native home | Existing boundary |
| --- | --- | --- |
| Briefing translation, original text and speech | Library inspector, Overview | Package text stays intact. Existing Apple translation provider and errors. |
| Legacy Mac tests and exact session reporting | Library inspector, Play History | Keep legacy scope separate from exact map/build/session assertions. |
| Issues, fix implementations and fix tests | Library inspector, Issues & Evidence | Existing validated ledger and applicability rules. |
| Compatibility recipes, experimental editions and import checks | Library inspector, Compatibility & Editions | Existing Python recipe/edition checks; separate editions and saves. Static checks do not prove gameplay. |
| Removal and activation without launch | Contextual map actions and Original Game | Explicit native confirmation; existing stopped-game, recovery and preservation guards. |
| Bulk imports, cancellation and map updates | Collection | Trusted catalogue, bounded downloads, serialized imports and per-item outcomes. |
| Installation, recovery, app updates, language and voice | Preferences | Existing discovery, consent, publisher verification and safe update shutdown. System Settings remains an OS destination. |
| Crash preferences, report status and local report folder | Preferences and Activity | Existing privacy and publication gates. Raw diagnostics never enter UI bridge responses. |

Briefing translation stays beside original text. Source and target languages are
explicit. Results stay in memory, keyed to the selected edition; Show Original
restores package presentation immediately. The service resolves current metadata
and validates archive/variant before calling the existing worker-only Apple helper.
No full-library refresh follows.

The contextual inspector now includes legacy Mac tests, history scope, issue links,
fix implementation/testing, compatibility recipes, experimental editions and import
checks. Legacy tests remain distinct from exact scenario evidence. Native removal,
original activation and Play warning review capture the exact target/build and
revalidate before existing protected operations. Play uses a bounded one-use review
ticket; changed warnings require fresh review. Uncertain writes retain drafts and
hold repeat submission for inspection.

Preferences contains General, Updates, Library and Crash Reporting categories.
Updates exposes installed/available/staged state, verified download progress and
explicit install-or-skip choice on quit. A manually staged release cannot gain
installation consent from losing the frontend. Collection supports selected bulk
imports, four bounded downloads, serialized guarded imports, cooperative cancellation
and per-archive outcomes. Progress also appears in Activity. Native alerts display
sanitized operation/resource/update failures; raw reports stay private.

These views call existing protected Python operations. Tk still owns the hidden
service event loop, initialization, polling and shutdown; it is not yet removed as
a dependency. Some legacy workflow routes remain for compatibility, but normal
native navigation uses the integrated destinations. No new UI toolkit, browser
runtime or third-party dependency was added. Removing the remaining Tk owner needs
equivalent lifecycle and operation coverage, rather than a wholesale rewrite of
validated services.

Source, synthetic rendering and packaged/game acceptance are separate. The current
integration evidence and remaining limits are recorded in
[IMPLEMENTATION.md](IMPLEMENTATION.md) and the canonical issues.
