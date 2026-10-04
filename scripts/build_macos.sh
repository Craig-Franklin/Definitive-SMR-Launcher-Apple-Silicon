#!/bin/zsh
set -euo pipefail

cd "${0:A:h:h}"
python=/opt/homebrew/bin/python3.12
if [[ ! -x "$python" ]]; then
  print -u2 "Install Homebrew python@3.12 and python-tk@3.12 first."
  exit 1
fi
"$python" -c 'import platform, tkinter; assert platform.machine() == "arm64"; assert tkinter.TkVersion >= 8.6' || {
  print -u2 "An arm64 Python with Tk 8.6 or newer is required."
  exit 1
}

if [[ ! -d .venv ]]; then
  "$python" -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-build.txt
.venv/bin/python -m pip install --disable-pip-version-check --no-deps --no-build-isolation -e .
.venv/bin/python -m PyInstaller \
  --noconfirm --clean --onedir --windowed --target-architecture arm64 \
  --name "Definitive SMR Launcher Apple Silicon" \
  --osx-bundle-identifier com.craigfranklin.smrlauncher.applesilicon \
  --add-data "$PWD/APPLE_SILICON_LICENSE.md:." \
  --distpath dist --workpath build/pyinstaller --specpath build/spec \
  scripts/launcher_entry.py

bundle="dist/Definitive SMR Launcher Apple Silicon.app"
test -d "$bundle"
version=$("$python" -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')
plutil -replace CFBundleShortVersionString -string "$version" "$bundle/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$version" "$bundle/Contents/Info.plist"
codesign --force --sign - "$bundle"
lipo -archs "$bundle/Contents/MacOS/Definitive SMR Launcher Apple Silicon" | grep -qx arm64
codesign --verify --deep --strict "$bundle"
print "Built: $bundle"
