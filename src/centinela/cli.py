"""Command-line entry point.

For now it only prints the version and the status of the project phases.
New subcommands (diagnose, chaos, eval...) are added in later phases.
"""

import argparse
import sys

from centinela import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="centinela",
        description="AI SRE agent that detects, diagnoses and safely fixes incidents.",
    )
    parser.add_argument("--version", action="version", version=f"centinela {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    print(f"centinela {__version__} - phase 0: project skeleton ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
