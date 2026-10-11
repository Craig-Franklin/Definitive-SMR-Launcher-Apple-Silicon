# Mac build and release operations

Run commands from the repository root. [CONTRIBUTING](../CONTRIBUTING.md) defines
when checks or builds are required. This guide supplies the build prerequisites
and existing signed-release configuration; ordinary docs changes need no app build.

## Build from source

Building and synthetic checks do not require a game installation. To use the app,
follow the separate [game setup instructions](../MACOS_README.md#install-the-mac-app).

1. Install [Homebrew](https://brew.sh/) if necessary, then install the arm64
   Python and Tk packages:

   ```sh
   brew install python@3.12 python-tk@3.12
   ```

2. Install Xcode 26 or newer and select its developer tools with `xcode-select`.
   The build compiles an Apple Translation helper; the launcher reports a clear
   limitation when translation is unavailable on an older Mac.
3. Clone this fork on the Mac and build the app:

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

## Automatic releases

The Mac workflow selects checks from the actual changes. Code changes run the
existing full test suite; internal documentation uses lightweight checks. App
builds run only when packaging verification or a qualifying release needs them.
See the [verification policy](../CONTRIBUTING.md#verification-matrix).

A successful main push releases only when an app-affecting Conventional Commit
in the full unreleased range requires one:

- `fix:` and `perf:` increment the patch version.
- `feat:` increments the minor version.
- `!` or a `BREAKING CHANGE:` footer increments the major version, including
  while the project is below 1.0.
- Internal documentation, tests, tooling and other non-release commits receive
  relevant CI checks and do not publish a release by themselves.

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
Historical updater validation replaced an isolated v0.3.0 app with v0.3.8,
retaining the previous signed app and a private save sentinel. Fresh-Mac
installation and VoiceOver validation remain separate QA steps.
