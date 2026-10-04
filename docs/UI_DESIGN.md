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
Thumbnails come from a user-selected map archive and remain in that user’s
private launcher library. The current gallery shows imported maps; a remote
full-catalogue view is still in development. macOS accessibility and keyboard
behavior require release testing on the packaged app; visual inspection alone
does not establish VoiceOver support.
