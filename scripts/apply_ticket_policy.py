"""Backfill the global ticket policy blurb onto existing Wix ticket definitions.

Tickets created by the pipeline get the ``default_ticket_policy`` Settings
value automatically, but tickets that already exist in Wix only converge when
their event is next diffed (Ready-match or an Update flip). This one-off
patches ``policyText`` on every ticket definition of every upcoming event so
the blurb reaches buyers immediately.

Usage:
  python scripts/apply_ticket_policy.py               # dry run (default)
  python scripts/apply_ticket_policy.py --apply       # actually patch Wix
  python scripts/apply_ticket_policy.py --text "..."  # override the Settings value
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_sync.config import load_config
from event_sync.logging_utils import configure_logging, get_logger
from event_sync.runtime import MAX_TICKET_POLICY_CHARS, SyncRuntime


logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply the default_ticket_policy blurb to existing Wix tickets"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="write changes to Wix (default is a dry run)",
    )
    parser.add_argument(
        "--text",
        help="policy text to apply (default: default_ticket_policy from the "
        "Notion Settings DB)",
    )
    args = parser.parse_args()

    configure_logging("INFO")
    config = load_config()
    config.ensure_wix_valid()
    runtime = SyncRuntime(config)

    if args.text is not None:
        text = args.text.strip()
        if len(text) > MAX_TICKET_POLICY_CHARS:
            logger.error(
                "--text is %d chars — Wix caps policyText at %d",
                len(text), MAX_TICKET_POLICY_CHARS,
            )
            return 1
    else:
        config.ensure_notion_valid()
        text = runtime.get_ticket_policy_text()

    if not text:
        logger.error(
            "No policy text — set the default_ticket_policy row in the "
            "Notion Settings DB (or pass --text)."
        )
        return 1

    logger.info("Policy text (%d chars):\n%s\n", len(text), text)
    if not args.apply:
        logger.info("🔍 DRY RUN — pass --apply to write changes\n")

    client = runtime.get_wix_client()
    counts = {"updated": 0, "unchanged": 0, "failed": 0}

    for event in client.iter_events(page_size=100, statuses=["UPCOMING", "STARTED"]):
        event_id = event.get("id")
        title = event.get("title", "(untitled)")
        for td in client.get_ticket_definitions(event_id):
            ticket_label = f"{title} / {td.get('name', '(unnamed ticket)')}"
            if (td.get("policyText") or "").strip() == text:
                counts["unchanged"] += 1
                continue
            if not args.apply:
                logger.info("  UPDATE: %s", ticket_label)
                counts["updated"] += 1
                continue
            try:
                client.update_ticket_definition(
                    td["id"], td["revision"], policy_text=text
                )
                logger.info("  🎫 Updated: %s", ticket_label)
                counts["updated"] += 1
            except Exception as exc:
                logger.error("  ❌ Failed: %s — %s", ticket_label, exc)
                counts["failed"] += 1
            time.sleep(0.3)

    label = "Would update" if not args.apply else "Updated"
    logger.info(
        "\n📈 Done: %s %d ticket(s), %d already current, %d failed",
        label, counts["updated"], counts["unchanged"], counts["failed"],
    )
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
