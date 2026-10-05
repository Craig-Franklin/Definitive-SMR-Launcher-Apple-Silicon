#!/bin/zsh
set -euo pipefail
cd "${0:A:h:h}"
bundle="${1:?Pass the built app bundle}"
destination="${2:?Pass the destination DMG}"
[[ ! -e "$destination" ]] || { print -u2 'Refusing to replace an existing disk image'; exit 1; }
codesign --verify --deep --strict "$bundle"
.venv/bin/dmgbuild -s scripts/dmg_settings.py -D "app=$bundle" \
  'Definitive SMR — Drag to Applications' "$destination"
hdiutil verify "$destination"
.venv/bin/python scripts/verify_dmg.py "$destination" "$bundle"
