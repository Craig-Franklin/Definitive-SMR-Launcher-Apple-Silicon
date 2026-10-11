"""PyInstaller entry point for the window or its bundled update helper mode."""
from pathlib import Path
import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
        from smr_launcher.update_helper import main as update_main
        update_main(Path(sys.argv[2]))
    else:
        from smr_launcher.gui import main as gui_main
        gui_main()
