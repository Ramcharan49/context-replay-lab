"""Run the lab using only the Python standard library."""

import argparse
import json
from pathlib import Path
import sys

from .core import ValidationError, replay
from .demo import run_demo, write_fixture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run the four checked-in controls")
    demo.add_argument("--fixtures", type=Path, default=Path("examples"))
    replay_parser = commands.add_parser("replay", help="validate and replay one proposal")
    for name in ("events", "baseline", "proposal"):
        replay_parser.add_argument(f"--{name}", required=True, type=Path)
    fixture = commands.add_parser("init-fixture", help="create a new synthetic capture directory")
    fixture.add_argument("directory", type=Path)
    for subparser in (demo, replay_parser):
        subparser.add_argument("--out", type=Path, help="create a new report, refusing to overwrite any file")
    args = parser.parse_args(argv)
    try:
        if args.command == "init-fixture":
            write_fixture(args.directory)
            print(f"Created synthetic fixture: {args.directory}")
            return 0
        if args.command == "demo":
            report = run_demo(args.fixtures)
        else:
            report = replay(args.events.read_bytes(), args.baseline.read_bytes(), args.proposal.read_bytes())
        output = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.out:
            with args.out.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(output)
        else:
            sys.stdout.write(output)
        return 0 if report.get("all_controls_passed", True) else 1
    except (ValidationError, OSError) as error:
        print(f"Rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
