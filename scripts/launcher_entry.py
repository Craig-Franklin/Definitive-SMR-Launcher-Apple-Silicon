"""Bundled Python services, legacy tools and signed update-helper entry point."""

from pathlib import Path
import os
import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
        from smr_launcher.update_helper import main as update_main

        update_main(Path(sys.argv[2]))
    elif sys.argv[1:] == ["--native-protocol-check"]:
        # Package verification only: never initialize the launcher or its monitors.
        import json

        request = json.loads(sys.stdin.readline())
        if request != {"id": "package-check", "method": "ping"}:
            raise ValueError("Unexpected package-check request")
        print(json.dumps({"id": "package-check", "ok": True, "data": {"protocol": 1}}), flush=True)
    elif sys.argv[1:] == ["--native-service"]:
        from smr_launcher.native_backend import main as service_main

        service_main()
    elif sys.argv[1:] == ["--legacy-tools"]:
        from smr_launcher.gui import main as gui_main

        gui_main()
    else:
        native = Path(sys.executable).with_name("SMRLauncher")
        if not native.is_file():
            raise RuntimeError("The native launcher is missing. Reinstall the complete app.")
        os.execv(str(native), [str(native), *sys.argv[1:]])
