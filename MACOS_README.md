# Apple Silicon Mac launcher

This branch is a GitHub fork of `ageekhere/Definitive-SMR-Launcher`. The inherited
Windows launcher and its history remain upstream work. New Mac services live in
`src/smr_launcher/`; their license is scoped in
[`APPLE_SILICON_LICENSE.md`](APPLE_SILICON_LICENSE.md).

## Current status

An arm64 Mac app can now be built from this checkout. It finds a Steam Mac
installation, preserves a clean Original Game profile, imports a local map
archive into an independent private library, shows an image gallery, and
switches complete profiles with an interruption recovery journal. It can also
browse the Internet Archive collection and verify a selected download before
private import. The app is
**still in development**: it has passed synthetic safety tests and local
clean-profile enrollment, but custom-map gameplay and save/reload checks are
pending. There is no tested downloadable release yet. Map tiles marked “Not
verified” have passed only static checks.

The Mac app does not ship the commercial game or add personal maps and saves
to the repository. Inherited upstream files retain their own provenance; the
Mac app uses archives supplied by each user.

## Build and run on an Apple Silicon Mac

1. Install **Sid Meier’s Railroads!** from Steam on its default public branch.
   Launch the stock Mac game once. Play a stock scenario, make a manual save,
   quit, reopen, and load it before setting up the launcher.
2. Install [Homebrew](https://brew.sh/) if necessary, then install the arm64
   Python and Tk packages:

   ```sh
   brew install python@3.12 python-tk@3.12
   ```

3. Clone this fork on the Mac and build the app:

   ```sh
   git clone https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon.git
   cd Definitive-SMR-Launcher-Apple-Silicon
   ./scripts/build_macos.sh
   open "dist/Definitive SMR Launcher Apple Silicon.app"
   ```

The script creates a local `.venv` and a `dist/` app bundle; both are ignored
by Git. It checks the arm64 executable and local ad hoc code signature. This
development build is not notarized. A downloadable signed release will need
separate release validation.

If Steam stores the game on another drive and the launcher cannot find it, use
**File → Choose Steam Library Folder…** and choose that drive's `steamapps`
folder. This is a folder picker; you do not need to find `Steam.app`. The Mac
edition must be the public
branch and Steam must report it fully installed. Quit Railroads before setup,
import, or profile changes. In the app, open **Setup** and choose **Set Up
Clean Game**. The app copies the clean profile into its private library and
keeps Original Game's existing saves. Use **Import Map…** to select a local
`.7z`, `.zip`, or `.tar` archive, select its gallery tile, and choose **Play
Selected**. To return to the stock profile, choose **Original Game → Play
Original Game**. Start the game through the launcher when changing maps; an
external Steam or Finder start can race a profile switch.

The **Collection** tab loads a searchable list of currently published map
archives from the Internet Archive. Choose a map and **Download & Import** to
check its declared length and SHA-1 before the normal package inspection and
private import. A collection listing is only a download source; it does not
indicate that the map is compatible with this Mac game build.

The private map library is under
`~/Library/Application Support/Definitive SMR Launcher Apple Silicon/`. It
contains independent original archives, prepared map profiles, icons, and
saves. Do not edit its frozen original copies or the preservation backup. The
app keeps future saves with the selected profile. If a switch is interrupted,
quit Railroads and reopen the launcher to recover the journal before starting
another game.

## Development checks

On macOS with Python 3.9 or newer:

```sh
python3 -m unittest discover -s tests -v
```

The tests use synthetic profiles and archives. They do not require a Steam
installation or downloaded maps and cannot establish that a custom map plays.
The interface design is documented in [`docs/UI_DESIGN.md`](docs/UI_DESIGN.md).
