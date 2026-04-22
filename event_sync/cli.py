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
    if command in {
        "sync",
        "test",
        "list",
        "pull-config",
        "clean-synced",
        "pull-categories",
        "push-categories",
    }:
        config.ensure_valid()
        return

    if command in {"generate", "prepare-sheet", "prepare"}:
        if not config.google_sheet_id:
            raise ConfigError("GOOGLE_SHEET_ID is missing")
        if not config.google_credentials_raw or not config.google_credentials:
            raise ConfigError(
                "GOOGLE_CREDENTIALS is missing or invalid JSON (client_email required)"
            )

    if command == "push-config":
        config.ensure_valid()


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
    subparsers.add_parser("publish-drafts", help="Publish all draft events in Wix")

    clean_parser = subparsers.add_parser(
        "clean-synced",
        help="Delete only rope+class events matching the generated sheet",
    )
    clean_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting",
    )

    sync_parser = subparsers.add_parser("sync", help="Sync events from Google Sheets")
    sync_parser.add_argument(
        "--no-tickets",
        action="store_true",
        help="Disable automatic ticket creation",
    )
    sync_parser.add_argument(
        "--draft",
        action="store_true",
        help="Create events as drafts (no tickets until publish-drafts)",
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
        nargs="+",
        help="Filter by month(s) (e.g., -m apr may). Defaults to current + next month.",
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
        nargs="+",
        help="Filter by month(s) (e.g., -m apr may). Defaults to current + next month.",
    )

    subparsers.add_parser(
        "pull-config",
        help="Pull all published Wix events into config_events master tab",
    )

    push_config_parser = subparsers.add_parser(
        "push-config",
        help="Push config_events updates to existing Wix events",
    )
    push_config_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without making any API calls",
    )

    pull_cats_parser = subparsers.add_parser(
        "pull-categories",
        help="Pull category assignments into the category_config tab",
    )
    pull_cats_parser.add_argument(
        "--scope",
        choices=["upcoming", "all"],
        default="upcoming",
        help="upcoming (default) keeps UPCOMING/STARTED; all keeps every non-draft event",
    )

    push_cats_parser = subparsers.add_parser(
        "push-categories",
        help="Push category edits from category_config back to Wix",
    )
    push_cats_parser.add_argument(
        "--scope",
        choices=["upcoming", "all"],
        default="upcoming",
        help="upcoming (default) only acts on UPCOMING/STARTED rows; all acts on every row",
    )
    push_cats_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without making any API calls",
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

        if args.command == "publish-drafts":
            from .orchestrator import publish_all_drafts
            ok = publish_all_drafts(runtime)
            return 0 if ok else 1

        if args.command == "clean-synced":
            from .orchestrator import clean_synced_events
            ok = clean_synced_events(runtime, dry_run=args.dry_run)
            return 0 if ok else 1

        if args.command == "sync":
            auto_tickets = not args.no_tickets
            ok = sync_events(runtime, auto_create_tickets=auto_tickets, draft=args.draft)
            return 0 if ok else 1

        if args.command == "generate":
            from .generator import _default_rolling_months
            months = args.month or _default_rolling_months()
            ok = generate_events(
                runtime,
                output_sheet=args.output_sheet,
                month_filters=months,
            )
            return 0 if ok else 1

        if args.command in {"prepare-sheet", "prepare"}:
            from .generator import _default_rolling_months
            months = args.month or _default_rolling_months()
            ok = generate_events(
                runtime,
                output_sheet=config.generated_events_tab,
                month_filters=months,
            )
            return 0 if ok else 1

        if args.command == "pull-config":
            from .generator import pull_config_events
            ok = pull_config_events(runtime)
            return 0 if ok else 1

        if args.command == "push-config":
            from .orchestrator import push_config_events
            ok = push_config_events(runtime, dry_run=args.dry_run)
            return 0 if ok else 1

        if args.command == "pull-categories":
            from .orchestrator import pull_category_config
            ok = pull_category_config(runtime, scope=args.scope)
            return 0 if ok else 1

        if args.command == "push-categories":
            from .orchestrator import push_category_config
            ok = push_category_config(runtime, scope=args.scope, dry_run=args.dry_run)
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

