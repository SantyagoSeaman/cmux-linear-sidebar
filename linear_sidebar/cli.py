"""Command line entry point: `cmux-linear-sidebar [--port N] [--refresh-seconds N] [--check-key | --version]`."""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Optional, Sequence

from . import __version__, config, server
from .linear import LinearClient, LinearError


def check_key() -> int:
    """Reads an API key from stdin and prints whose it is, so scripts never pass it as an argument."""
    key = sys.stdin.read().strip()
    if not key:
        print("no key on stdin", file=sys.stderr)
        return 2
    try:
        viewer = LinearClient(key).viewer()
    except LinearError as error:
        print(error, file=sys.stderr)
        return 1
    if not viewer.get("name"):
        print("Linear did not say whose key this is", file=sys.stderr)
        return 1
    email = f" <{viewer['email']}>" if viewer.get("email") else ""
    print(f"{viewer['name']}{email}")
    return 0


def _number(option: str, maximum: Optional[int] = None) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            return config.whole_number(option, raw, maximum)
        except config.SettingsError as error:
            raise argparse.ArgumentTypeError(str(error)) from None
    return parse


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cmux-linear-sidebar",
        description="Serves the Linear sidebar page for the cmux Dock.",
    )
    parser.add_argument("--port", type=_number("--port", 65535),
                        help=f"port on 127.0.0.1 (default: LINEAR_SIDEBAR_PORT or {config.DEFAULT_PORT})")
    parser.add_argument("--refresh-seconds", type=_number("--refresh-seconds"),
                        help="how often Linear is asked for changes (default: LINEAR_SIDEBAR_REFRESH_SECONDS "
                             f"or {config.DEFAULT_REFRESH_SECONDS})")
    parser.add_argument("--check-key", action="store_true",
                        help="read a Linear API key from stdin and print whose it is")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.check_key:
        return check_key()
    return server.main(port=args.port, refresh_seconds=args.refresh_seconds)
