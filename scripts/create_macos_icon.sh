#!/bin/zsh
# Convert the inherited Windows icon without substituting new artwork.
set -euo pipefail
cd "${0:A:h:h}"
mkdir -p build/AppIcon.iconset
sips -s format png Definitive-SMR-Launcher/icon/icon.ico --out build/original-icon.png >/dev/null
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" build/original-icon.png --out "build/AppIcon.iconset/icon_${size}x${size}.png" >/dev/null
  twice=$((size * 2))
  sips -z "$twice" "$twice" build/original-icon.png --out "build/AppIcon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns build/AppIcon.iconset -o build/AppIcon.icns
