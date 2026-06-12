from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .core import ListingMonitorError, run_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notify-me",
        description="Monitor property rental search pages and notify when new listings appear.",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the JSON config file. Defaults to config.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and compare listings without saving state or sending external notifications.",
    )
    parser.add_argument(
        "--list-current",
        action="store_true",
        help="Print currently detected listings without comparing or updating state.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=0,
        help="Run continuously with this many seconds between checks. Defaults to one check.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)

    while True:
        try:
            exit_code = run_once(
                config_path,
                dry_run=args.dry_run,
                list_current=args.list_current,
            )
        except ListingMonitorError as exc:
            print(f"notify-me: {exc}", file=sys.stderr)
            exit_code = 1

        if args.interval_seconds <= 0:
            return exit_code

        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
