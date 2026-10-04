"""PyInstaller entry point for the window or its bundled update helper mode."""
from pathlib import Path
import sys

from smr_launcher.gui import main as gui_main
from smr_launcher.update_helper import main as update_main


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
        update_main(Path(sys.argv[2]))
    else:
        gui_main()
