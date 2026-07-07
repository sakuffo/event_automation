"""Command-line interface for the event sync toolkit."""

from __future__ import annotations

import argparse
import sys
from typing import Iterable, Optional

from .config import ConfigError, load_config
from .generator import generate_events
from .logging_utils import configure_logging, get_logger
from .orchestrator import (
    list_wix_events,
    sync_events,
    test_wix_connection,
    validate_credentials,
)
from .runtime import SyncRuntime


logger = get_logger(__name__)


def _ensure_command_config(command: str, config) -> None:
    """Validate only the settings required for a given command."""
    if command in {"sync", "test", "list"}:
        config.ensure_valid()
        return

    if command in {"generate", "prepare-sheet", "prepare"}:
        if not config.google_sheet_id:
            raise ConfigError("GOOGLE_SHEET_ID is missing")
        if not config.google_credentials_raw or not config.google_credentials:
            raise ConfigError(
                "GOOGLE_CREDENTIALS is missing or invalid JSON (client_email required)"
            )


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
    sync_parser.add_argument(
        "--publish",
        action="store_true",
        help="Automatically publish created events (default: leave as draft)",
    )

    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate event data from rolling_schedule + class_info tabs",
    )
    generate_parser.add_argument(
        "--output-sheet",
        metavar="TAB_NAME",
        help="Write output to a new sheet tab instead of stdout",
    )
    generate_parser.add_argument(
        "-m",
        "--month",
        metavar="MONTH",
        help="Filter prepared events by month (e.g., mar, MAR, March)",
    )
    prepare_parser = subparsers.add_parser(
        "prepare-sheet",
        aliases=["prepare"],
        help="Step 1: Rebuild destination tab in GOOGLE_SHEET_ID from SOURCE_SHEET_ID",
    )
    prepare_parser.add_argument(
        "-m",
        "--month",
        metavar="MONTH",
        help="Filter prepared events by month (e.g., mar, MAR, March)",
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
            _ensure_command_config(args.command, config)
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
            ok = sync_events(runtime, auto_create_tickets=auto_tickets, auto_publish=args.publish)
            return 0 if ok else 1

        if args.command == "generate":
            ok = generate_events(
                runtime,
                output_sheet=args.output_sheet,
                month_filter=args.month,
            )
            return 0 if ok else 1

        if args.command in {"prepare-sheet", "prepare"}:
            ok = generate_events(
                runtime,
                output_sheet=config.generated_events_tab,
                month_filter=args.month,
            )
            return 0 if ok else 1

        parser.print_help()
        return 1
    except KeyboardInterrupt:
        logger.warning("Operation cancelled by user")
        return 1
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unhandled error: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

