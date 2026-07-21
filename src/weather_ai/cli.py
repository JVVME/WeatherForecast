"""Command-line interface for M0 project infrastructure."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from weather_ai import __version__
from weather_ai.config import ConfigError, load_config
from weather_ai.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    """Build the WeatherAI argument parser."""

    parser = argparse.ArgumentParser(
        prog="weather-ai",
        description="WeatherAI research project command-line interface.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser(
        "config",
        help="validate a YAML configuration and print its resolved values",
    )
    config_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to the YAML configuration file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    args = build_parser().parse_args(argv)
    if args.command == "config":
        return _show_config(args.config)
    raise AssertionError(f"unhandled command: {args.command}")


def _show_config(path: Path) -> int:
    try:
        config = load_config(path)
    except (ConfigError, OSError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2

    configure_logging(config.logging.level, config.logging.format)
    logging.getLogger(__name__).info(
        "configuration loaded",
        extra={"event": "configuration_loaded", "config_path": str(path.resolve())},
    )
    print(json.dumps(config.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
