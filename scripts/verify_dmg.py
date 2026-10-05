"""Check the actual app and Applications link inside the read-only disk image."""
from pathlib import Path
import os
import plistlib
import subprocess
import sys
import tempfile


def verify(image: Path, source: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="smr-dmg-check-") as directory:
        mount = Path(directory) / "mounted"
        subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint",
                        str(mount), str(image.resolve())], check=True, stdout=subprocess.DEVNULL)
        try:
            bundle = mount / source.name
            if os.readlink(mount / "Applications") != "/Applications":
                raise RuntimeError("Disk image has an unexpected Applications destination")
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
            for relative in ("Contents/Info.plist", "Contents/MacOS/Definitive SMR Launcher Apple Silicon"):
                if (bundle / relative).read_bytes() != (source / relative).read_bytes():
                    raise RuntimeError("Disk image payload differs from the built app")
            info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
            if info["CFBundleIdentifier"] != "com.craigfranklin.smrlauncher.applesilicon":
                raise RuntimeError("Unexpected disk image app identity")
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
