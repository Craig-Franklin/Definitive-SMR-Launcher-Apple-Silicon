"""Small JSON command bridge for the native Mac window."""

from __future__ import annotations

from dataclasses import asdict
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
    maps = []
    for record in app.catalogue():
        verification = app.verification_for(record)
        maps.append(
            dict(
                name=record.name,
                profile_id=record.profile_id,
                variant_id=record.variant_id,
                archive_sha256=record.archive_sha256,
                scenarios=list(record.scenarios),
                icon_path=str(app.map_icon(record)) if app.map_icon(record) else None,
                verification=verification.status if verification else "Not verified",
            )
        )
    return dict(
        installed=True,
        enrolled=enrolled,
        running=running,
        active=active,
        binding_error=binding_error,
        recovery_pending=app.profiles.journal.exists(),
        steam_buildid=installation.steam_buildid,
        bundle_version=installation.bundle_version,
        maps=maps,
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
    bank = actions.add_parser("inspect-bank", help="Read-only bank source applicability")
    bank.add_argument("profile_id")
    for name in ("scenario", "industry", "model"):
        bank.add_argument("--" + name, required=True)
    choice = actions.add_parser("choose-library")
    choice.add_argument("steamapps")
    for action in ("plan-recipes", "prepare-recipes"):
        batch = actions.add_parser(action)
        batch.add_argument(
            "--request",
            action="append",
            nargs=2,
            required=True,
            metavar=("PROFILE_ID", "RECIPE_ID"),
        )
    args = parser.parse_args(argv)
    if args.action == "choose-library":
        app = LauncherApplication.choose_steam_library(Path(args.steamapps))
        return _status(app)
    app = LauncherApplication.discover()
    if args.action == "inspect-bank":
        return asdict(
            app.inspect_bank_candidate(
                args.profile_id,
                scenario_path=args.scenario,
                industry_path=args.industry,
                model_path=args.model,
            )
        )
    if args.action in ("plan-recipes", "prepare-recipes"):
        operation = (
            app.plan_recipe_batch if args.action == "plan-recipes" else app.prepare_recipe_batch
        )
        return operation(
            [dict(profile_id=profile, recipe_id=recipe) for profile, recipe in args.request]
        )
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
