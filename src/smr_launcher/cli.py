"""Small JSON command bridge for the native Mac window."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

from .application import LauncherApplication


def _status(app: LauncherApplication) -> dict:
    installation = app.installation
    running = installation.game_running()
    enrolled = app.profiles.state_file.exists()
    active = None
    binding_error = None
    if enrolled:
        try:
            app._verify_installation_binding()
        except Exception as exc:
            binding_error = str(exc)
    if enrolled and binding_error is None and not app.profiles.journal.exists():
        try:
            active = app.profiles._state()["active"]
        except Exception:
            pass
    return dict(
        installed=True, enrolled=enrolled, running=running, active=active,
        binding_error=binding_error,
        recovery_pending=app.profiles.journal.exists(),
        steam_buildid=installation.steam_buildid,
        bundle_version=installation.bundle_version,
        maps=[dict(name=record.name, profile_id=record.profile_id,
                   variant_id=record.variant_id,
                   archive_sha256=record.archive_sha256,
                   scenarios=list(record.scenarios),
                   icon_path=str(app.map_icon(record)) if app.map_icon(record) else None,
                   verification="Not verified") for record in app.catalogue()],
    )


def run(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(prog="smr-launcher-backend")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("status")
    setup = actions.add_parser("setup")
    setup.add_argument("--stock-verified", action="store_true")
    import_action = actions.add_parser("import")
    import_action.add_argument("archive")
    play = actions.add_parser("play")
    play.add_argument("profile_id")
    choice = actions.add_parser("choose-library")
    choice.add_argument("steamapps")
    args = parser.parse_args(argv)
    if args.action == "choose-library":
        app = LauncherApplication.choose_steam_library(Path(args.steamapps))
        return _status(app)
    app = LauncherApplication.discover()
    if args.action == "status":
        return _status(app)
    if args.action == "setup":
        if not args.stock_verified:
            raise ValueError("Confirm stock-game play and save/reload before setup")
        app.setup()
    elif args.action == "import":
        app.import_archive(Path(args.archive))
    elif args.action == "play":
        app.play(args.profile_id)
    return _status(app)


def main(argv: list[str] | None = None) -> int:
    try:
        result = dict(ok=True, data=run(argv))
        code = 0
    except Exception as exc:
        result = dict(ok=False, error=str(exc))
        code = 2
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
