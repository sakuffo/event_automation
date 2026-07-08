"""Command-line interface for the Notion → Wix event sync toolkit."""

from __future__ import annotations

import argparse
import sys
from typing import Iterable, Optional

from .config import ConfigError, load_config
from .logging_utils import configure_logging, get_logger


logger = get_logger(__name__)


# Commands that talk to the Wix API and are therefore subject to the
# production-site guard (_enforce_site_guard).
WIX_COMMANDS = {
    "validate",
    "test",
    "list",
    "pull",
    "sync",
    "pull-site-config",
    "push-site-config",
}


def _enforce_site_guard(command: str, args, config) -> Optional[str]:
    """Production-site guard for every Wix-touching command.

    ``WIX_PROD_SITE_ID`` in ``.env`` declares which site is production.
    Without the explicit ``--production`` flag, a run whose ``WIX_SITE_ID``
    matches it is refused. With the flag, the run is retargeted onto the
    production site id — the flag is the *only* supported way to hit
    production, and it is human-only: automation and AI agents must never
    pass it.

    Returns an error message to print, or None when the run may proceed.
    """
    if command not in WIX_COMMANDS:
        return None
    prod_site_id = (config.wix_prod_site_id or "").strip()
    if getattr(args, "production", False):
        if not prod_site_id:
            return (
                "--production requires WIX_PROD_SITE_ID in .env "
                "(the declared production site id)"
            )
        config.wix_site_id = prod_site_id
        logger.warning(
            "🚨 PRODUCTION RUN — targeting the production Wix site (%s…)",
            prod_site_id[:8],
        )
        return None
    if prod_site_id and (config.wix_site_id or "").strip() == prod_site_id:
        return (
            "WIX_SITE_ID points at the declared production site "
            "(WIX_PROD_SITE_ID). Refusing to run without --production. "
            "Point WIX_SITE_ID back at the dev site, or re-run with "
            "--production if you really mean production."
        )
    return None


def _ensure_command_config(command: str, config) -> None:
    """Validate only the settings required for a given command."""
    if command == "setup-notion":
        config.ensure_notion_valid(require_databases=False)
        return

    if command in {"enrich", "import-event-templates"}:
        config.ensure_notion_valid()
        return

    if command in {"sync", "pull", "pull-site-config", "push-site-config"}:
        config.ensure_notion_valid()
        config.ensure_wix_valid()
        return

    if command in {"test", "list"}:
        config.ensure_wix_valid()
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python sync_events.py",
        description="Wix Events automation with a Notion backend",
    )

    # Shared parent so --log-level works both before and after the subcommand.
    # SUPPRESS ensures the subparser only sets log_level when explicitly given,
    # so the top-level default ("INFO") survives if neither side provides it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--log-level",
        default=argparse.SUPPRESS,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set log verbosity (default: INFO). Accepted before or after the subcommand.",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set log verbosity (default: INFO). Accepted before or after the subcommand.",
    )

    # Second shared parent for commands that talk to Wix. --production is
    # human-only: it retargets the run onto WIX_PROD_SITE_ID and is the only
    # way to touch the production site (see _enforce_site_guard).
    wix_common = argparse.ArgumentParser(add_help=False)
    wix_common.add_argument(
        "--production",
        action="store_true",
        help=(
            "Target the production Wix site (WIX_PROD_SITE_ID) instead of "
            "WIX_SITE_ID. Human-only — never passed by automation or agents."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "validate",
        parents=[common, wix_common],
        help="Validate credentials and configuration",
    )
    subparsers.add_parser(
        "test", parents=[common, wix_common], help="Test Wix API connectivity"
    )
    subparsers.add_parser(
        "list", parents=[common, wix_common], help="List existing events in Wix"
    )

    subparsers.add_parser(
        "setup-notion",
        parents=[common],
        help="Create the Notion databases (Event Scheduling/Catalog/Settings/Site Config)",
    )

    import_templates_parser = subparsers.add_parser(
        "import-event-templates",
        parents=[common],
        help=(
            "One-time: seed recurring event templates (Type=event) in the "
            "catalog from the events export CSV"
        ),
    )
    import_templates_parser.add_argument(
        "--csv",
        default="wix_events_export_de.csv",
        metavar="PATH",
        help=(
            "Events export CSV with default_event/include_in_feed columns "
            "(default: %(default)s)"
        ),
    )
    import_templates_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which source instance each family would use without writing",
    )
    import_templates_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite catalog rows that already exist under the same name",
    )

    pull_parser = subparsers.add_parser(
        "pull",
        parents=[common, wix_common],
        help="Pull Wix events into the Notion Event Scheduling DB (backfill/refresh)",
    )
    pull_parser.add_argument(
        "--scope",
        choices=["upcoming", "all"],
        default="upcoming",
        help="upcoming (default) pulls UPCOMING/STARTED; all pulls every non-draft event",
    )

    enrich_parser = subparsers.add_parser(
        "enrich",
        parents=[common],
        help="Fill blanks on Idea/Draft Notion rows from the Catalog + Settings + pricing",
    )
    enrich_parser.add_argument(
        "-m",
        "--month",
        metavar="MONTH",
        nargs="+",
        help="Only touch rows in these months (e.g., -m apr may). Default: all rows.",
    )

    sync_parser = subparsers.add_parser(
        "sync",
        parents=[common, wix_common],
        help=(
            "Sync Notion with Wix: push Ready/Update rows, refresh Published "
            "rows from Wix (runs an enrich pass first)"
        ),
    )
    sync_parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Skip the enrich pass that normally runs before syncing",
    )
    sync_parser.add_argument(
        "--no-tickets",
        action="store_true",
        help="Disable automatic ticket creation",
    )
    sync_parser.add_argument(
        "--draft",
        action="store_true",
        help="Create new events as Wix drafts (publish by re-running sync without --draft)",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing to Wix or Notion",
    )
    sync_parser.add_argument(
        "-m",
        "--month",
        metavar="MONTH",
        nargs="+",
        help="Only sync rows in these months (e.g., -m apr may). Default: all rows.",
    )

    subparsers.add_parser(
        "pull-site-config",
        parents=[common, wix_common],
        help="Pull eCommerce tax-by-location settings into the Notion Site Config DB",
    )

    push_site_parser = subparsers.add_parser(
        "push-site-config",
        parents=[common, wix_common],
        help="Push Site Config tax-location edits (rates) from Notion back to Wix",
    )
    push_site_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without making any API calls",
    )

    return parser


# Command handlers, imported lazily so each invocation only loads what it
# uses. Each returns True on success.


def _cmd_validate(args, config, runtime) -> bool:
    from .wix_flows import validate_credentials
    return validate_credentials(config)


def _cmd_test(args, config, runtime) -> bool:
    from .wix_flows import test_wix_connection
    return test_wix_connection(runtime)


def _cmd_list(args, config, runtime) -> bool:
    from .wix_flows import list_wix_events
    list_wix_events(runtime)
    return True


def _cmd_setup_notion(args, config, runtime) -> bool:
    from .notion_orchestrator import setup_notion
    return setup_notion(runtime)


def _cmd_import_event_templates(args, config, runtime) -> bool:
    from .notion_orchestrator import import_event_templates
    return import_event_templates(
        runtime, csv_path=args.csv, dry_run=args.dry_run, force=args.force,
    )


def _cmd_pull(args, config, runtime) -> bool:
    from .notion_orchestrator import pull_events
    return pull_events(runtime, scope=args.scope)


def _cmd_enrich(args, config, runtime) -> bool:
    from .notion_orchestrator import enrich_events
    return enrich_events(runtime, month_filters=args.month)


def _cmd_sync(args, config, runtime) -> bool:
    from .notion_orchestrator import notion_sync_events
    return notion_sync_events(
        runtime,
        auto_create_tickets=not args.no_tickets,
        draft=args.draft,
        dry_run=args.dry_run,
        month_filters=args.month,
        run_enrich=not args.no_enrich,
    )


def _cmd_pull_site_config(args, config, runtime) -> bool:
    from .notion_orchestrator import pull_site_config_notion
    return pull_site_config_notion(runtime)


def _cmd_push_site_config(args, config, runtime) -> bool:
    from .notion_orchestrator import push_site_config_notion
    return push_site_config_notion(runtime, dry_run=args.dry_run)


COMMANDS = {
    "validate": _cmd_validate,
    "test": _cmd_test,
    "list": _cmd_list,
    "setup-notion": _cmd_setup_notion,
    "import-event-templates": _cmd_import_event_templates,
    "pull": _cmd_pull,
    "enrich": _cmd_enrich,
    "sync": _cmd_sync,
    "pull-site-config": _cmd_pull_site_config,
    "push-site-config": _cmd_push_site_config,
}


def _build_runtime():  # pragma: no cover - glue logic
    from .runtime import SyncRuntime

    config = load_config()
    return config, SyncRuntime(config)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    configure_logging(args.log_level)
    config, runtime = _build_runtime()

    handler = COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces choices
        parser.print_help()
        return 1

    try:
        # `validate` reports on config instead of requiring it upfront.
        if args.command != "validate":
            try:
                _ensure_command_config(args.command, config)
            except ConfigError as exc:
                logger.error("Configuration error: %s", exc)
                return 1

        # Production-site guard: runs before any handler (including
        # `validate`) so no Wix call can ever hit production without the
        # explicit, human-only --production flag.
        guard_error = _enforce_site_guard(args.command, args, config)
        if guard_error:
            logger.error("%s", guard_error)
            return 1

        return 0 if handler(args, config, runtime) else 1
    except KeyboardInterrupt:
        logger.warning("Operation cancelled by user")
        return 1
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unhandled error: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
