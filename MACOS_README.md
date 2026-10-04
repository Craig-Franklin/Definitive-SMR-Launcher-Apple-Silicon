# Apple Silicon Mac launcher

This branch is a GitHub fork of `ageekhere/Definitive-SMR-Launcher`. The inherited
Windows launcher and its history remain upstream work. New Mac services live in
`src/smr_launcher/`; their license is scoped in
[`APPLE_SILICON_LICENSE.md`](APPLE_SILICON_LICENSE.md).

## Current status

The Mac activation and package-preparation services are under development. This
commit does **not** yet provide an installable Mac app or a supported end-user
launch flow. The finished project will include a downloadable arm64 Mac app,
installation instructions, and a fresh-install verification. No Steam game,
custom-map packages, personal saves, or local diagnostics will be added by the
Mac port.

The implementation currently checks custom-map archives without extracting
into the game, creates independent prepared copies, and switches complete
profiles with a recovery journal. Activation against a real game installation
requires verified Steam binding and further end-to-end checks. Static package
checks do not establish that a map plays or that its saves reload.

## Development checks

On macOS with Python 3.9 or newer:

```sh
python3 -m unittest discover -s tests -v
```

The tests use synthetic profiles and archives. They do not require a Steam
installation or downloaded maps.
