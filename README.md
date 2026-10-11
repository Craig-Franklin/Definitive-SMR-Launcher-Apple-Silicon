# Definitive SMR Launcher — Apple Silicon

A native Mac launcher for the Steam edition of **Sid Meier's Railroads!** on
Apple Silicon. The launcher manages community maps, independent profiles and
saved games. The game itself runs through Rosetta.

**This fork develops and releases the Mac app only.** The inherited Windows
launcher remains in the repository for upstream provenance and comparison;
Windows development, testing and releases are outside this fork's scope.

## Install and use

Download the installer from the [latest Mac release](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/releases/latest).
Open the `.dmg` and drag the app into Applications. A Steam copy of the game is
required; the launcher does not include the commercial game or personal maps.

The [Mac user guide](MACOS_README.md) covers setup, map imports, play history,
updates and save preservation. The app can:

- Browse and import community maps into a private library.
- Keep original archives and separate map profiles, with their own saves.
- Show map details, community reports and explicitly recorded Mac test results.
- Recover interrupted profile switches and install verified app updates.

The app is still in development. A successful download, static check or map load
does not prove gameplay or save/reload compatibility. See the
[feature comparison and remaining validation](docs/FEATURE_PARITY.md).

## Development

Start with [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks, review and delivery.
AI sessions follow [AGENTS.md](AGENTS.md). The [code map](docs/ARCHITECTURE.md) and
[documentation index](docs/README.md) explain where to look next.

Checks follow the changed files. Build only when needed. GitHub issues carry
current scope and acceptance; historical plans are reference material. The
[compatibility programme](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/issues/1)
has separate execution authority and is currently paused.

## Upstream and licensing

This remains a fork of [ageekhere/Definitive-SMR-Launcher](https://github.com/ageekhere/Definitive-SMR-Launcher).
Its Windows source, assets and history retain their upstream provenance. The
[inherited Windows README](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/blob/90fc6dd910c6b0942e875f0aadd195814a4295e3/README.md)
is preserved in Git history. Upstream changes can be reviewed and incorporated
without treating Windows features as Mac support claims.

New Mac code lives in `src/smr_launcher/`. See the
[Mac code license](APPLE_SILICON_LICENSE.md) and
[inherited asset attribution](docs/UPSTREAM_ASSETS.md).
