"""Command-line interface for the event sync toolkit."""

from __future__ import annotations

import argparse
import sys
from typing import Iterable, Optional

from .config import ConfigError, load_config
from .logging_utils import configure_logging, get_logger
from .orchestrator import (
    list_wix_events,
    sync_events,
    test_wix_connection,
    validate_credentials,
)
from .runtime import SyncRuntime


logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python sync_events.py",
        description="Wix Events + Google Sheets integration",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set log verbosity (default: INFO)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="Validate credentials and configuration")
    subparsers.add_parser("test", help="Test Wix API connectivity")
    subparsers.add_parser("list", help="List existing events in Wix")

    sync_parser = subparsers.add_parser("sync", help="Sync events from Google Sheets")
    sync_parser.add_argument(
        "--no-tickets",
        action="store_true",
        help="Disable automatic ticket creation",
    )

    return parser


def _build_runtime():  # pragma: no cover - glue logic
    config = load_config()
    return config, SyncRuntime(config)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    configure_logging(args.log_level)
    config, runtime = _build_runtime()

    try:
        if args.command == "validate":
            ok = validate_credentials(config)
            return 0 if ok else 1

        try:
            config.ensure_valid()
        except ConfigError as exc:
            logger.error("Configuration error: %s", exc)
            return 1

        if args.command == "test":
            ok = test_wix_connection(runtime)
            return 0 if ok else 1

        if args.command == "list":
            list_wix_events(runtime)
            return 0

        if args.command == "sync":
            auto_tickets = not args.no_tickets
            ok = sync_events(runtime, auto_create_tickets=auto_tickets)
            return 0 if ok else 1

        parser.print_help()
        return 1
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user")
        return 1
    except Exception as exc:
        print(f"\n❌ Error: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

