# Selected Mac UI direction — B

**Status: Craig selected B on 11 October 2026.** SwiftUI with AppKit where needed
is the approved presentation direction. This document records the selected rules
for #222 and implementation follow-ups. Existing app behavior is
described in [the current interface notes](docs/UI_DESIGN.md).

Open [the local click-through](docs/ui-workflow/wireframes.html) in a browser.
All names, images, sessions and observations are synthetic. Controls simulate
navigation and drafts in memory; they cannot reach the launcher, disk or network.
The [review and evidence record](docs/ui-workflow/REVIEW.md) gives the source baseline,
numbered task flows, coverage, toolkit gaps and verification limits.

Craig subsequently approved a native SwiftUI frontend with AppKit where useful,
retaining the Python services, and requested current Apple Liquid Glass conventions.
The [native review source](docs/ui-workflow/NativeWireframe.swift) is an isolated
synthetic prototype, not production launcher integration. The HTML remains a
structural click-through; its flat rendering does not demonstrate Liquid Glass.

## Native material direction

Use standard SwiftUI navigation, toolbar and search controls to receive the system's
current Liquid Glass appearance. In macOS 27, Apple describes edge-to-edge sidebars,
semibold selected sidebar labels and refined toolbar/scroll-edge behavior in
[Modernize your AppKit app](https://developer.apple.com/videos/play/wwdc2026/289/).
Use glass for navigation and functional controls, with ordinary readable surfaces
for map content, evidence and forms, following [Materials](https://developer.apple.com/design/human-interface-guidelines/materials).
Do not add custom translucent map cards or stack glass layers for decoration.

Follow the system's tint, Reduce Transparency, Increase Contrast and Reduce Motion
settings. Apple's [macOS 27 update notes](https://support.apple.com/en-us/127257)
describe readability refinements and adjustable tint. Do not create a parallel
launcher tint preference or synthesize optical effects in Python. The native
prototype uses standard controls and a system glass Play button; it establishes
only local rendering at the installed OS/SDK, not all material/accessibility states.

SwiftUI/AppKit adoption and the Python boundary are described in the review record.
Craig selected B after approving the native direction. These decisions do not
authorize gameplay or change the minimum macOS version for released builds.

## Two library alternatives

**A — Focused list, initial recommendation.** Sidebar for destinations; compact list with a
small thumbnail, full map name and meaningful status; persistent detail pane for
the selected map. Search/filter/sort stay above the list. Import belongs to the
library toolbar; Play belongs to the selected map. Details, editing and reporting
reuse that pane, with the library still visible. This favors scanning names, long
titles and frequent information entry. The tradeoff is less space for artwork and
one map's detail at a time.

**B — Artwork grid with inspector, selected.** The same sidebar and toolbar; larger image
tiles with name and status; selected map in a trailing inspector. This favors visual
recognition and casual browsing. Fewer maps fit on screen, wrapping varies by title,
and narrow windows require a separate detail surface. It is a real alternative,
not a promise to implement a second view alongside A.

Craig selected B. Use visual recognition as the primary browsing pattern, with
stable tiles and an inspector for reading, editing and contextual reporting.
Keep each tile to artwork, full name and meaningful status; put counters and
provenance in the inspector. The initial A recommendation favored text scanning;
its compact list remains a reviewed alternative, not an additional implementation
requirement. Preserve B through search, task return and narrow-window navigation.

## Shared pattern sheet

Values below are proposed Mac layout points, not physical screenshot pixels.
Prefer SwiftUI semantic font roles and platform metrics over browser equivalents.

| Pattern | Proposed rule and use |
| --- | --- |
| Type | System UI font. Map title 20 pt semibold; section 13 pt semibold; body/control 13 pt; secondary 11–12 pt. Use SwiftUI `.title2`, `.headline`, `.body` and `.caption`; hashes use a monospaced system font. HTML uses `-apple-system`. Verify actual point metrics and user scaling on Mac. |
| Space | 4 pt base; 8 pt related controls; 12 pt row padding; 16 pt groups; 24 pt pane margins/section separation. Align text baselines and shared label columns. |
| Density | A rows approximately 72 pt with two-line names and status. Wrap longer names to natural height; no lost full name. B tiles approximately 190 pt wide. Minimum proposed app size 900 × 590; default 1120 × 720; wide 1440 × 900. |
| Surfaces | Standard SwiftUI sidebar/toolbar materials for functional navigation; ordinary readable content surfaces. Use current system Liquid Glass, not hand-painted glass cards. Single separators where useful. Support system light/dark and accessibility appearance. No decorative gradients or animated entrance. |
| Color | System accent for selection, focus and primary action. Light secondary text `#5f6368` on white; dark `#b9bdc4` on `#202124`. Warnings `#805500` / `#f2c16b`, errors `#b3261e` / `#ffb4ab`. Always pair color with text. Browser colors are review approximations, not system color API values. |
| Icons | One consistent line style. Use text labels for important actions. Native symbols only if supported without a dependency; otherwise small authored monochrome shapes or text. No emoji or ornamental glyphs as controls. |
| Controls | Native SwiftUI controls, with AppKit only where needed. Regular control size, approximately 28–32 pt in the HTML mockup. Use platform intrinsic size in implementation. Focus outline remains separate from selection fill. Tk remains the incumbent baseline only. |
| Sidebar | Map Library, Collection, Original Game, Activity. Preferences in the app menu, with an optional lower sidebar shortcut. Setup/recovery remains reachable through preferences/help rather than competing with browsing. Original Game remains a distinct destination. |
| Toolbar | Library title, search, filter, sort, Import Map. Secondary commands in labeled menus. No Play on every row. Toolbar wraps or collapses secondary commands before hiding labels. |
| Selection | Stable ID, not row index. Selected map and keyboard focus are separate. Retain query, sort, filter, list/detail scroll and selection across navigation; filtered-out selection is explicit and disables map actions until visible/reselected. |
| Buttons | One contextual primary action: Play in overview, Save Details in editor, Record Confirmation in report. Secondary actions use quiet native buttons. Destructive commands remain secondary and use existing guarded confirmation. |
| Forms | Known package facts read-only with provenance. Personal title/notes/images editable separately. Ask only for missing facts. Inline validation next to the field; retain draft, selection and errors. Save is explicit; Cancel offers Keep Draft or Discard Draft when dirty. |
| Sheets | Short protected decisions or native file selection only. Normal detail reading/editing stays in a pane. A narrow window shows a Back to Library detail surface, preserving the list. Existing warning/activation confirmations remain mandatory. |
| Feedback | Quiet inline success; small refresh label while last good rows remain. Retry near a recoverable error. Empty library and zero search results have different instructions. Never use an empty pane to imply loading. |
| Evidence | Summary and provenance in overview; timeline, identities and detailed rules under disclosures. Always expose known issues. Player-confirmed, automatic observation, legacy report and unknown remain distinct text states. |
| Keyboard | Cmd-F search; Cmd-O import; Cmd-comma preferences; arrows move list selection; Return opens details; Space previews details. Play requires an explicit focused button/action, never a double-click or implicit Return from a text field. Escape goes back or cancels with draft protection. Tab follows visual order; Return in multiline notes inserts a newline. |
| Accessibility | Name every control; announce loading/error without stealing focus; text equivalents for images and status; no color-only meaning. Honor keyboard access, contrast, Reduce Motion and font scaling. VoiceOver and full keyboard access are acceptance checks, not claims established by HTML. |

## Cross-screen rules

- Overview contains artwork, name, key facts, concise status and the contextual Play
  action. Serious known issues stay visible above secondary details. Counts move to
  Play History. A completed play never silently clears an unresolved failure.
- Details uses three readable sections: Overview, Play History, Issues & Evidence.
  Briefing/translation/read-aloud belong to Overview. Package text, static checks,
  compatibility options, legacy Mac tests and exact identities are disclosures with
  explicit labels. No existing capability is silently removed.
- Edit Details reviews package facts and personal presentation side by side. Local
  image selection previews a copy; missing/invalid images never erase a valid image.
  Reverting personal fields restores package presentation, not package bytes.
- Reporting starts from a session when available, then asks what the player reached
  or what went wrong. Unknown scenario/game/incident bindings require a choice.
  Exact-save read-only selection appears for save/reopen evidence; fix testing uses
  its own contextual flow with existing prerequisites. A generic “played” answer is
  never translated into completed, saved, resumed or verified.
- Preferences groups existing language/voice, updates, library/setup and crash
  reporting. Privacy controls keep their current saved values and capabilities;
  the mockup displays illustrative values only. No default or publication policy
  changes. State explicitly which choices save immediately and which need Apply.
- At 900 pt, collapse navigation to a destination menu and show either grid or
  detail. Back restores selection/scroll. At wide sizes, use three resizable panes.
  Avoid pretending that three tiny panes are usable in a narrow window.

## Implementation boundary

Craig approved SwiftUI with AppKit where needed as the native presentation direction.
Preserve Python service contracts and safety/evidence authority. The existing JSON
CLI is a partial bridge, not a complete frontend API. Native preview evidence and
the migration boundary are recorded in the review record. The current Tk feasibility
table remains useful baseline evidence; it no longer constrains the selected frontend.

This package changes no production app source or evidence contract. #218–#221
inherit B and these shared rules. #222 records the selected composition and
review evidence; implementation acceptance remains in the existing follow-ups.
