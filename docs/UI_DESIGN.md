# Mac launcher interface

The Mac window keeps the original launcher’s leading controls and visual map
gallery, as shown in the [upstream README screenshot](https://github.com/ageekhere/Definitive-SMR-Launcher/blob/main/README.md).
It adapts that layout for a Mac: a stable left navigation area, search and
actions near the top, selectable map thumbnails, and a separate Original Game
view. Selecting a tile exposes its scenario count and source hash; the Play
action operates on that selection. Map status remains “Not verified” until
gameplay and save/reload evidence exists.

The layout follows Apple’s guidance for [sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars),
[image collections](https://developer.apple.com/design/human-interface-guidelines/collections),
[toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars),
and [focus and selection](https://developer.apple.com/design/human-interface-guidelines/focus-and-selection/).
The visual restraint follows the MIT-licensed
[Uncodixfy guidance](https://github.com/cyxzdev/Uncodixfy/tree/e0e028058b5259debdd94b78147c6d6c77bf7da2):
plain surfaces, modest corners, consistent spacing, and no decorative effects
competing with the maps. This document credits that guidance; no Uncodixfy
source file is copied into the app.

The app does not bundle the upstream background or downloaded map artwork.
It does use the original launcher icon, converted from the inherited 64 × 64
ICO into an ICNS bundle. Attribution is included in the app; see
[`UPSTREAM_ASSETS.md`](UPSTREAM_ASSETS.md).
Thumbnails come from a user-selected map archive and remain in that user’s
private launcher library. The imported-map gallery uses those thumbnails. A
separate searchable Collection table shows remote archive names, per-map
download progress, and sizes. Download & Import All uses four parallel transfers
and serial guarded imports into private storage; Download & Import prepares a
selected map. Remote entries have no
artwork until their archives are imported. macOS accessibility and keyboard
behavior require release testing on the packaged app; visual inspection alone
does not establish VoiceOver support.

Map tiles show author and declared creation date. Map Details contains the
complete metadata, source links, original text, briefing with macOS read-aloud,
community information and exact local testing evidence. Date sorting keeps
unknown dates last. Archive modification dates are explicitly separate from
map creation dates. Collection supports multiple selection and labels already
imported archives. Known local failures produce a launch confirmation.

The main download is a signed, notarized DMG with the original launcher icon,
a drag arrow and an Applications shortcut. Installation uses Finder; the app
has no self-install button or copy/relaunch service. A separate ZIP supports
existing automatic updates.

New libraries open a focused welcome screen with no sidebar or map search.
Get Started uses the guarded setup transaction, preserves the original profile
and saves, then opens Collection. Missing games offer discovery retry and a
Steam folder picker. Existing libraries open Map Library directly.
