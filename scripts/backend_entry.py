"""PyInstaller entry point for the native Mac app's local service process."""
from smr_launcher.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
