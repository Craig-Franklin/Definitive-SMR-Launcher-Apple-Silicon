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
| Community reviews and voting | Browser link | Opens the original map's GitHub discussion. Inline star totals remain to be implemented; the upstream index contains no scores. |
| Read a briefing aloud | Implemented | Uses the installed macOS speech voice. Voice selection/download UI remains to be implemented. |
| Return to the original game | Implemented | Complete Original Game profile with its own preserved saves. |
| Launcher installation and updates | Implemented; release QA pending | First-run Install in Applications, verified copy and relaunch; signed updates. End-to-end release testing awaits notarization. |
| Original launcher icon | Implemented | Same inherited Windows artwork, converted to macOS ICNS. Original is 64 × 64. |
| Update a map without losing the earlier edition | Partial | New archive contents create a separate variant and retain earlier saves. Automatic map-version discovery remains to be implemented. |
| Persistent activity log viewer | Pending | Current UI reports operation errors and progress; no dedicated historical log viewer yet. |
| UI languages and translated briefings | Pending | Current Mac interface is English; original briefings are preserved. |
| Custom difficulty | Pending Mac compatibility work | Must become a separately labelled profile edition with its own saves; Windows writes directly to game XML. |
| Map editor toggle | Pending Mac compatibility work | Windows setting has not been validated against the Feral Mac edition. |
| OpenSpy server/player browser | Pending Mac compatibility work | Online compatibility must be established for the Mac game. |
| Windows OpenSpy executable replacement / LAA patch | Windows-specific | A Windows EXE patch cannot be applied to the Feral x86_64 Mach-O game. |
| Windows Steam/disk edition selector | Windows-specific | Mac currently supports the Steam Mac edition and alternate Steam library locations. |

The Mac app additionally preserves immutable source archives, isolates saves by
exact variant, journals profile switches, and binds local gameplay results to
the exact game and map assets. A community “Reported stable” label never grants
a local Mac “Verified” result.

## Next parity work

Prioritize a persistent activity viewer, map-update discovery, inline ratings,
and language/voice controls. Validate editor, difficulty and online play against
the Mac edition independently before exposing controls that alter the game.
Retain originals and existing saves throughout that work.
