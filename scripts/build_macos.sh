#!/bin/zsh
set -euo pipefail

cd "${0:A:h:h}"
dist_root="${SMR_DIST_ROOT:-dist}"
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
signing=()
if [[ -n "${SMR_RELEASE_IDENTITY:-}" ]]; then
  if [[ ! "${SMR_APPLE_TEAM_ID:-}" =~ '^[A-Z0-9]{10}$' ]]; then
    print -u2 "A ten-character Apple Team ID is required for a release build."
    exit 1
  fi
  signing=(--codesign-identity "$SMR_RELEASE_IDENTITY")
fi
mkdir -p build
xcrun swiftc -parse-as-library -swift-version 5 -target arm64-apple-macosx13.0 -O src/native/*.swift -o build/SMRLauncher
xcrun swiftc -parse-as-library -target arm64-apple-macosx13.0 -O scripts/translate_macos.swift -o build/smr-translate
./scripts/create_macos_icon.sh
print -r -- "{\"schema\":1,\"team_id\":\"${SMR_APPLE_TEAM_ID:-}\",\"repository\":\"Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon\",\"bundle_id\":\"com.craigfranklin.smrlauncher.applesilicon\"}" > build/release-policy.json
.venv/bin/python -m PyInstaller \
  --noconfirm --clean --onedir --windowed --target-architecture arm64 \
  --name "Definitive SMR Launcher Apple Silicon" \
  --osx-bundle-identifier com.craigfranklin.smrlauncher.applesilicon \
  --icon "$PWD/build/AppIcon.icns" \
  --add-data "$PWD/APPLE_SILICON_LICENSE.md:." \
  --add-data "$PWD/docs/UPSTREAM_ASSETS.md:." \
  --add-binary "$PWD/build/smr-translate:." \
  --add-data "$PWD/Definitive-SMR-Launcher/DSMRL_data/RRT_Difficulty.xml:Definitive-SMR-Launcher/DSMRL_data" \
  --add-data "$PWD/build/release-policy.json:." \
  --add-data "$PWD/src/smr_launcher/map_inspection.py:smr_launcher" \
  --add-data "$PWD/src/smr_launcher/inspection_io.py:smr_launcher" \
  --add-data "$PWD/src/smr_launcher/map_inspect.py:smr_launcher" \
  --add-data "$PWD/src/smr_launcher/industry_registry.py:smr_launcher" \
  --add-data "$PWD/src/smr_launcher/model_checks.py:smr_launcher" \
  --add-data "$PWD/src/smr_launcher/resource_identity.py:smr_launcher" \
  --add-data "$PWD/src/smr_launcher/packages.py:smr_launcher" \
  --add-data "$PWD/src/smr_launcher/extraction.py:smr_launcher" \
  "${signing[@]}" \
  --distpath "$dist_root" --workpath build/pyinstaller --specpath build/spec \
  scripts/launcher_entry.py

bundle="$dist_root/Definitive SMR Launcher Apple Silicon.app"
test -d "$bundle"
cp build/SMRLauncher "$bundle/Contents/MacOS/SMRLauncher"
plutil -replace CFBundleExecutable -string SMRLauncher "$bundle/Contents/Info.plist"
plutil -replace LSMinimumSystemVersion -string 13.0 "$bundle/Contents/Info.plist"
version=$("$python" -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')
plutil -replace CFBundleShortVersionString -string "$version" "$bundle/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$version" "$bundle/Contents/Info.plist"
if [[ -n "${SMR_RELEASE_IDENTITY:-}" ]]; then
  codesign --force --options runtime --timestamp --sign "$SMR_RELEASE_IDENTITY" "$bundle/Contents/MacOS/Definitive SMR Launcher Apple Silicon"
  codesign --force --options runtime --timestamp --sign "$SMR_RELEASE_IDENTITY" "$bundle/Contents/MacOS/SMRLauncher"
  codesign --force --options runtime --timestamp --sign "$SMR_RELEASE_IDENTITY" "$bundle"
else
  codesign --force --sign - "$bundle/Contents/MacOS/Definitive SMR Launcher Apple Silicon"
  codesign --force --sign - "$bundle/Contents/MacOS/SMRLauncher"
  codesign --force --sign - "$bundle"
fi
lipo -archs "$bundle/Contents/MacOS/SMRLauncher" | grep -qx arm64
lipo -archs "$bundle/Contents/MacOS/Definitive SMR Launcher Apple Silicon" | grep -qx arm64
codesign --verify --deep --strict "$bundle"
.venv/bin/python - "$bundle" <<'PY'
import json
from pathlib import Path
import subprocess
import sys

helper = Path(sys.argv[1]) / "Contents/MacOS/Definitive SMR Launcher Apple Silicon"
probe = subprocess.run(
    [str(helper), "--native-protocol-check"],
    input='{"id":"package-check","method":"ping"}\n',
    text=True, capture_output=True, check=True, timeout=20,
)
if json.loads(probe.stdout) != {"id": "package-check", "ok": True, "data": {"protocol": 1}}:
    raise RuntimeError("The packaged services did not preserve their JSON pipes")
PY
print "Built: $bundle"
