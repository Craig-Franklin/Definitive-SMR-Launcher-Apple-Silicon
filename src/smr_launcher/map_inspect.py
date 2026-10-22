"""Read-only CLI for exact map inspection; findings are not runtime verdicts."""

from __future__ import annotations

from pathlib import Path
import argparse
import json

from . import inspection_io, map_inspection


def main(argv=None):
    parser = argparse.ArgumentParser(prog="smr-map")
    actions = parser.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser(
        "inspect", help="Inspect an exact map without activating or editing it"
    )
    inspect.add_argument("map", type=Path)
    inspect.add_argument("--game", type=Path, required=True, help="Selected game .app bundle")
    inspect.add_argument(
        "--scenario", help="Exact relative scenario path; default reports all candidates"
    )
    inspect.add_argument("--format", choices=("text", "json"), default="text")
    inspect.add_argument("--report", type=Path, help="New private JSON file; never overwritten")
    args = parser.parse_args(argv)
    code = 0
    report = None
    try:
        protected = [args.map, args.game.parent]
        if args.report:
            inspection_io.check_destination(args.report, protected)
        report = map_inspection.inspect_map(args.map, args.game, scenario=args.scenario)
        if args.report:
            inspection_io.write_report(args.report, report, protected)
    except Exception as exc:
        code = 2
        report = getattr(exc, "report", None) or report or map_inspection.empty_report()
        report["status"] = "refused"
        report["error"] = dict(type=type(exc).__name__, message=str(exc))
        report["observation"]["finished_utc"] = map_inspection._now()
    if args.format == "json":
        print(json.dumps(report, sort_keys=True, allow_nan=False))
    else:
        print(map_inspection.report_text(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
