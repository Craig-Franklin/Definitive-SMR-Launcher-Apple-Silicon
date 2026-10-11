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

The first implemented slice integrates briefing translation beside its original
text. Source and target languages are explicit. Translations stay in memory, keyed
to the selected edition; Show Original restores package presentation immediately.
The service resolves current metadata and validates the requested archive/variant
before calling its existing worker-only Apple helper. No gallery refresh follows.
Cached map selection stays available while a request runs; changes to data retain
the existing operation gate.

The remaining Tk tools and alerts still need migration, including launch warnings,
operation failures and update-install failures. Move their presentation and consent
into native views while calling existing protected operations. Remove the separate
Tk window and event-loop ownership after those responsibilities have replacements.
SwiftUI/AppKit own the interface; rewriting Python validation is not necessary to
achieve that. Any later service migration needs equivalent outcome/failure coverage.
