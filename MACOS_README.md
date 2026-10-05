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
browse the Internet Archive collection, download archives in parallel, and
verify them before private import. The app is **still in development**:
San Francisco passed a short user gameplay/manual-save/reload check on the
tested Steam build. Alternate Balkans crashed during initial track/train play;
its cause is unresolved. Arizona and Africa Diamonds still need gameplay checks.
Signed and notarized releases are published; downloaded v0.3.8 passed archive,
publisher and Gatekeeper checks, and an isolated v0.3.0-to-v0.3.8 update passed.
No extended campaign result is claimed. Map tiles without a recorded gameplay
check remain “Not verified.”

The Mac app does not ship the commercial game or add personal maps and saves
to the repository. Inherited upstream files retain their own provenance; the
Mac app uses archives supplied by each user.

## Install the Mac app

1. Install **Sid Meier's Railroads!** from Steam on an Apple Silicon Mac, using
   Steam's default public branch. The game runs through Rosetta; accept macOS's
   Rosetta installation prompt if needed.
2. Open the [latest Mac release](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/releases/latest)
   and choose **Download the Mac installer (.dmg)** in the release notes.
3. Open the disk image and drag **Definitive SMR Launcher Apple Silicon** onto
   the **Applications** shortcut. Eject the disk image.
4. Open the launcher from **Applications**. Accept the normal macOS confirmation
   for a downloaded app if shown. The app and disk image are Developer ID signed
   and notarized. No Homebrew or separate Python installation is needed.
5. The welcome screen finds the Steam game. Quit Railroads, then click
   **Get Started** to preserve the original game profile and its existing saves.
   The map collection opens when setup finishes.

If the game is not found, the welcome screen offers **Check Again** and
**Choose Steam Library Folder…**. Open Railroads once from Steam to create its
profile, then quit and check again. Existing launcher libraries open normally;
installing an app update does not require repeating setup.

The ZIP asset is retained for the built-in updater. Use the DMG for a new
installation; GitHub's source-code downloads are for development.

Every push to `main` builds and runs tests. Conventional Commits determine
whether to publish: `feat` adds a minor release, `fix`/`perf` add a patch, and
breaking changes add a major release. Documentation, tests and chores alone do
not create a release. A qualifying push releases its exact head after signing
and notarization; failed checks leave the last working release available.

## Build from source

1. Install **Sid Meier’s Railroads!** from Steam on its default public branch.
   Launch the stock Mac game once. Play a stock scenario, make a manual save,
   quit, reopen, and load it before setting up the launcher.
2. Install [Homebrew](https://brew.sh/) if necessary, then install the arm64
   Python and Tk packages:

   ```sh
   brew install python@3.12 python-tk@3.12
   ```

3. Install Xcode 26 or newer and select its developer tools with `xcode-select`.
   The build compiles an Apple Translation helper; the launcher reports a clear
   limitation when translation is unavailable on an older Mac.
4. Clone this fork on the Mac and build the app:

   ```sh
   git clone https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon.git
   cd Definitive-SMR-Launcher-Apple-Silicon
   ./scripts/build_macos.sh
   open "dist/Definitive SMR Launcher Apple Silicon.app"
   ```

The script creates a local `.venv` and a `dist/` app bundle; both are ignored
by Git. It checks the arm64 executable and local ad hoc code signature. This
development build is not notarized. Use the published release for automatic
updates.

## First run and maps

On first launch, **Get Started** checks the stopped Steam game and preserves
its original profile. Custom content already in the game's asset folders stops
setup with an explanation; it is never silently removed. Existing saves stay intact.
A stock scenario and save/reload test is useful for diagnosing game problems,
but is not an installation prerequisite or an automatic verification badge.

If Steam stores the game on another drive, choose that drive's `steamapps`
folder using the welcome screen or **File → Choose Steam Library Folder…**.
The Mac edition must be on Steam's public branch and fully installed. Quit
Railroads before setup, imports or profile changes. Setup
keeps Original Game's existing saves. Use **Import Map…** to select a local
`.7z`, `.zip`, or `.tar` archive, select its gallery tile, and choose **Play
Selected**. To return to the stock profile, choose **Original Game → Play
Original Game**. Start the game through the launcher when changing maps; an
external Steam or Finder start can race a profile switch.

The **Collection** tab loads a searchable list of currently published map
archives from the Internet Archive. Choose a map and **Download & Import** to
check its declared length and SHA-1 before the normal package inspection and
private import. **Download & Import All…** retrieves the full collection with
up to four parallel downloads, then inspects and imports each verified archive
one at a time. Progress beside each map name shows download percentage,
archive verification, import, and whether it was imported or failed. The
button shows the collection's archive download size before starting, and
**Cancel Imports** stops remaining work after the current safe operation.
Imports require additional space for extracted map files and Railroads must be
closed. Importing all maps does not activate them; choose one from Map Library.
A collection listing or verified download does not establish compatibility
with this Mac game build.

### Map details, dates and sources

Select a gallery tile and choose **Map Details…**, or double-click the tile.
The app reads the archive's original `mapInfo.txt`: author, modifiers, version,
creation date, update date, player type and complete briefing. The original
text is available in its own tab. **Read Briefing Aloud** uses the installed
macOS speech voice, with a Stop control. Search includes author and briefing
text; the library can sort by creation/update date or author and filter by
declared player type or local Mac testing status.

Creation and update dates are the map package's explicit declarations. Missing,
invalid or ambiguous dates display **Not provided**. Internet Archive file
modification dates are labelled separately and never used as creation dates.
Refreshing Collection matches older imports to the preserved archive's length
and SHA-1 before attaching a source link. Maps imported locally without a match
retain their local-archive label. Source records and dates do not modify maps
or saves.

Use Command-click or Shift-click in Collection to download several selected
maps. Refresh also updates cached upstream community reports. Map Details
shows these reports separately from local Mac verification, with a link to
the original community reviews and rating discussion. Community reports do
not establish that a map works on the Mac. Maps with a recorded local issue
show a warning before launch.

### Record a Mac test and inspect an import

After playing a map, quit Railroads and open **Map Details → Record Mac Test…**
before switching maps. Record only checks you completed: fresh scenario loading,
gameplay, autosave, manual save, reopening and reloading that save, and continued
play after reloading. Add the test scope and duration. All six checks with no
reported issue earn **Verified** for the exact archive, prepared variant, assets
and game build. Partial tests remain **Not verified**; a reported crash or other
issue becomes **Known issue**. These are local observations, not a guarantee of
campaign reliability or community certification.

Imports automatically inspect bounded XML files for parse problems and explicit
XML references missing from loose map/game files, including likely filename
mismatches. **Map Details → Import Checks** shows findings and limitations;
**Run Import Checks** also checks maps imported with older launcher versions.
Packed assets and game behavior are outside this scan. A clean report never
awards Verified, and warnings do not silently rewrite map files.

### Remove a map while keeping its saves

Quit Railroads. If the map is active, choose **Switch to Original Game** in its
Map Details, then **Remove Map…**. Confirm to remove its installed/prepared map,
imported files and launcher-owned archives. Archives and inputs still needed by
another edition stay until that edition is removed. Source archives outside the
launcher library are untouched.

Saved games are independently copied, hashed and retained before removal starts.
Reimporting the exact map/variant with the same game build restores those saves;
a different map version receives its own empty save directory. Interrupted
removals recover before another library operation. The retained saves and local
test history remain in the private launcher library.

### Updates, language and activity

- **Collection → Check Map Updates** compares exact filename families and known
  archive hashes. Select the offered updates to import separate editions. Old
  editions and saves remain available; no automatic save migration is attempted.
- **Map Details → Community → Refresh Rating** reads public GitHub poll results.
  Approximate stars, total votes and “Not Working” votes remain separate from
  local Mac verification. Voting opens the upstream discussion in your browser.
- **Language & Voice** offers English, Dutch, French, German, Hindi, Italian,
  Simplified Chinese and Spanish. Choose an installed voice or open macOS's voice
  manager. Diagnostic/status prose and map-authored text may remain English.
- **Map Details → Translate Briefing** uses Apple translation on macOS 26 or
  later. Choose source and target languages; both models must already be
  installed. The original remains available with **Show Original**. Reading aloud
  uses the currently displayed briefing.
- **Activity** provides a searchable, bounded history of local launcher actions,
  import results and errors across sessions.
- **Map Details → Experimental Editions…** can prepare the upstream custom
  difficulty definitions in a separately labelled profile with empty saves.
  Its Mac loading and gameplay behavior require testing. Editor saving needs an
  isolated export/capture workflow, so that option remains unavailable.

The Mac app uses the original Windows launcher's icon artwork; see
[asset provenance](docs/UPSTREAM_ASSETS.md). The current
[Windows/Mac feature comparison](docs/FEATURE_PARITY.md) lists implemented,
partial, pending and Windows-specific features. Full parity is not yet claimed.

The private map library is under
`~/Library/Application Support/Definitive SMR Launcher Apple Silicon/`. It
contains independent original archives, prepared map profiles, icons, and
saves. Do not edit its frozen original copies or the preservation backup. The
app keeps future saves with the selected profile. If a switch is interrupted,
quit Railroads and reopen the launcher to recover the journal before starting
another game.

## App updates and public releases

The **Setup → App Updates** section has **Check for Updates** and an optional
**Automatically install verified updates when I quit the launcher** setting.
The setting is off by default. A new stable release is accepted only from this
personal fork, at a higher version, with the expected release archive name,
size, and GitHub SHA-256. Before an automatic replacement, the app also checks
the Apple Developer ID publisher, exact bundle identity/version, arm64
executable, and macOS acceptance of its notarization. The previous `.app` remains as a sibling
backup. Maps, profiles, and saves remain in the separate private library.
Automatic installation requires a Developer ID signed release in a folder the
user can write, such as `~/Applications`. A source-built or ad hoc signed app
does not automatically replace itself. If an update is staged while another
launcher operation is active, it waits for a later idle quit.

The personal fork's `.github/workflows/macos-app.yml` runs tests and builds a
development app for every main push and pull request. A successful main push
creates a signed release only when Conventional Commits since the last release
require one:

- `fix:` and `perf:` increment the patch version.
- `feat:` increments the minor version.
- `!` or a `BREAKING CHANGE:` footer increments the major version, including
  while the project is below 1.0.
- Documentation, tests, chores and other non-release commits still run CI, but
  do not publish a release by themselves.

The highest required bump wins when several commits land together. Release notes
are generated from those commits. Existing published tags and reserved legacy
versions are respected during migration from workflow-run numbering. Tags point
to the exact GitHub-Verified source commit; CI stamps the release number into the
app without creating a bot commit. Release jobs queue rather than cancel one
another. The build uses a macOS 26 SDK for the optional Apple translation helper;
the helper checks runtime availability before using translation.

Uploads are checked in a draft before publication. Rerunning an already
published build verifies and preserves its existing artifacts; a delayed older
build cannot replace a higher version as the latest update. The app's optional
automatic update setting consumes this fork's latest stable release.

Signing and notarization credentials come from 1Password. Put the Developer ID
Application `.p12` attachment, its export password in a concealed field, the
Apple team ID, and a **team** App Store Connect API `.p8` attachment with its
key and issuer IDs in a dedicated 1Password release vault. A 1Password service
account needs **read-only** access to that vault. Built-in Personal, Private,
Employee and default Shared vaults cannot be granted to a service account.

Configure only `OP_SERVICE_ACCOUNT_TOKEN` as a GitHub Actions secret in the
exact personal fork. Configure these GitHub Actions **variables** as 1Password
`op://` references; the references point to credentials in the dedicated
vault and contain no credential values:

| Variable | 1Password target |
| --- | --- |
| `SMR_CERT_P12_REF` | `.p12` file attachment |
| `SMR_CERT_PASSWORD_REF` | Concealed `.p12` export password field |
| `SMR_APPLE_TEAM_ID_REF` | Apple team ID field |
| `SMR_NOTARY_KEY_P8_REF` | Team API `.p8` file attachment |
| `SMR_NOTARY_KEY_ID_REF` | Team API key ID field |
| `SMR_NOTARY_ISSUER_ID_REF` | Team API issuer ID field |

The workflow uses 1Password CLI to load attachments straight into temporary
files on the release runner and reads the four fields at release time. It
imports Apple's public Developer ID G2 intermediate, creates a temporary
signing keychain, and deletes it after packaging. The repository's built-in
`GITHUB_TOKEN` publishes the release; no separate GitHub release credential is
needed. Missing references or credentials stop the release. Signing,
notarization, stapling, and macOS assessment must pass; there is no unsigned
fallback. The release archive also receives a GitHub artifact attestation.
The actual signed updater has replaced an isolated v0.3.0 app with v0.3.8,
retaining the previous signed app and a private save sentinel. A fresh Mac and
fresh-Mac installation and VoiceOver validation remain separate QA steps.

## Development checks

On macOS with Python 3.12:

```sh
python3 -m unittest discover -s tests -v
```

The tests use synthetic profiles and archives. They do not require a Steam
installation or downloaded maps and cannot establish that a custom map plays.
The interface design is documented in [`docs/UI_DESIGN.md`](docs/UI_DESIGN.md).
