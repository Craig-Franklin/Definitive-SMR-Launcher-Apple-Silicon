# SMR Launcher for Mac

Product context for [UI & Workflow, Project 5](https://github.com/users/Craig-Franklin/projects/5).
The canonical design milestone is [#222](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/222).

Craig uses the launcher to browse a personal map library, inspect maps, enter play,
collect missing details/images and record play or problem observations. He reports
repeated reloads, a heavy interface and tedious information entry. Success means a
stable browsing context, less repeated input and clear, trustworthy evidence.

The approved frontend is a macOS desktop application using SwiftUI/AppKit, with
Python services and advanced Tk tools retained. Familiar Mac
navigation, system typography, keyboard operation and native controls take priority
over visual novelty. The application runtime has no Python dependencies. An HTML
wireframe is a review artifact.

During this session Craig requested Apple's latest Liquid Glass design, asked about
Apple's native UI frameworks, and approved the recommendation to use SwiftUI with
AppKit where needed while retaining the Python services. This supersedes the earlier
Tk-only presentation constraint. Start with an isolated synthetic native wireframe
and a bounded backend-boundary assessment. Craig then selected B: artwork grid
with a persistent inspector. That selection satisfies the layout-choice gate.
Production integration must follow the selected patterns and preserve the Python
safety/evidence services. No minimum-OS change is implied by the review prototype.

Preserve original packages, images and saves. Personal annotations must be separate
from package facts. Preserve exact map/edition/build/session identities, failures,
unknowns and explicit player confirmations. Presentation caching cannot authorize
Play or certify compatibility. Legacy Mac tests and exact scenario evidence have
different contracts, even when their entry points share a design pattern.

The first milestone delivers the rough design and records Craig’s selection.
Follow-up implementation uses B and the native presentation direction. No game launch,
Play action, profile switching, restoration, save mutation, live crash collection,
paid service or new third-party dependency follows from this milestone. The native
frontend is now approved in principle; its staged implementation scope follows the
selected design and the reviewed architecture boundary.

The supplied brief resolves the discovery questions; no repeated interview is
needed. Proposed window sizes, density and information grouping are hypotheses for
wireframe review. Actual task timings, library scale, action reductions and native
accessibility require later implementation evidence in #218–#221.

Craig approved the SMR Native Design Review for implementation. The
[native implementation record](docs/ui-workflow/IMPLEMENTATION.md) describes the
built frontend and remaining acceptance evidence.
