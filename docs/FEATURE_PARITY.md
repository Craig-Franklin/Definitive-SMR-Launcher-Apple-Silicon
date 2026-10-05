# Windows and Mac feature comparison

This comparison comes from the inherited Windows source in this checkout and
the current Mac implementation. It is not a claim that the Windows executable
was run on this Mac. Full feature parity remains a project goal.

| User task | Mac status | Notes |
| --- | --- | --- |
| Browse map thumbnails, search and launch | Implemented | Search also matches package names, authors and briefings. |
| Read map information | Implemented | Author, modifiers, version, creation/update dates, type, briefing and original `mapInfo.txt`. Missing dates stay unknown. |
| Identify a map's source | Implemented | Collection links are bound to matching preserved archive bytes; explicit author source links are also shown. |
| Sort and filter maps | Implemented | Creation/update date, author, name, declared player type and local Mac verification. |
| Download one, several, or all maps | Implemented | Command/Shift selection, up to four downloads, progress, cancellation and checked serial imports. |
| Read community compatibility reports | Implemented | Cached upstream stability/multiplayer reports appear separately from local Mac tests. Refresh Collection refreshes this information. |
| Community reviews and voting | Implemented | Inline cached public poll results, total votes, approximate stars and separate Not Working share. Explicit refresh; voting opens the original discussion. Hidden results remain unavailable. |
| Read a briefing aloud | Implemented | Select any installed macOS voice; manage additional voices in System Settings. Reads original or translated text. |
| Return to the original game | Implemented | Complete Original Game profile with its own preserved saves. |
| Launcher installation and updates | Implemented; fresh-Mac UI QA pending | Drag-to-Applications DMG, guided first launch and verified automatic updates. Actual isolated v0.3.0-to-v0.3.8 replacement verified; fresh-Mac validation remains. |
| Original launcher icon | Implemented | Same inherited Windows artwork, converted to macOS ICNS. Original is 64 × 64. |
| Update a map without losing the earlier edition | Implemented | Explicit Check Map Updates finds newer numeric versions within exact filename families and changed bytes of the same verified archive. Selected updates import separately; earlier editions and saves remain. |
| Persistent activity log viewer | Implemented | Searchable local operation history, failures and per-map import results, retained between sessions with bounded storage. |
| UI languages and translated briefings | Implemented with limits | Eight interface languages; diagnostic/status prose can remain English. Briefing translation uses installed Apple models on macOS 26+, explicit source/target selection and original-text restoration. No third-party translation service. |
| Custom difficulty | Experimental edition available | Copies inherited eleven-level XML into a separate profile, refuses existing map difficulty definitions, uses empty independent saves. Mac menu/load/gameplay validation remains required. Commercial game files are unchanged. |
| Map editor toggle | Unavailable: export workflow required | Mac binary contains the setting, but editor saves can modify protected assets and prevent normal profile switching. Must establish an isolated export path or integrated capture/reidentity transaction before enabling it. |
| OpenSpy server/player browser | Excluded from this work | Mac online compatibility has not been established. |
| Windows OpenSpy executable replacement / LAA patch | Windows-specific | A Windows EXE patch cannot be applied to the Feral x86_64 Mach-O game. |
| Windows Steam/disk edition selector | Windows-specific | Mac currently supports the Steam Mac edition and alternate Steam library locations. |

The Mac app additionally preserves immutable source archives, isolates saves by
exact variant, journals profile switches, and binds local gameplay results to
the exact game and map assets. A community “Reported stable” label never grants
a local Mac “Verified” result.

## Remaining validation

Validate the experimental difficulty override in a fresh Mac scenario. The
inherited template has two differently cased time-field names compared with the
stock Mac XML; those semantics and override precedence are not proven by static
XML parsing. No map is marked Verified by creating an edition.

The editor remains unavailable until edited outputs can be preserved without
weakening asset integrity or save ownership. Ordinary updates and other portable
features do not depend on the Windows LAA or OpenSpy patches.
