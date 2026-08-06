#!/usr/bin/env python3
"""PreToolUse guard: --production is human-only.

Defense-in-depth for the rule in AGENTS.md ("Wix safety") that AI agents
must never run any command with --production. The authoritative guard is
cli._enforce_site_guard (pinned by tests/test_cli.py); this hook merely
stops agent shell commands before they reach the CLI at all.

Exit semantics (Claude Code PreToolUse):
  0 -> allow (defer to normal permissions)
  2 -> block; stderr is fed back to the model so it can self-correct
Any other exit is a non-blocking error, so failures here can never
prevent the guard-free commands from running.

Stdlib-only on purpose: runs on any Python >= 3.8, no venv required.
"""

import json
import re
import sys

# Matches --production as its own token (start-of-string or whitespace
# before it, word boundary after). Deliberately broad: a false positive
# on e.g. a grep pattern is acceptable for a safety guard.
BLOCK = re.compile(r"(^|\s)--production\b")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input must never wedge a session

    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0

    command = str((payload.get("tool_input") or {}).get("command", ""))
    if BLOCK.search(command):
        sys.stderr.write(
            "BLOCKED by .claude/hooks/block_production.py: --production is "
            "human-only (AGENTS.md, 'Wix safety'). Test against the dev site "
            "or use --dry-run; if a production run is genuinely needed, ask "
            "the user to run it themselves. If you were only searching text "
            "for the literal string, use the Grep tool instead of a shell "
            "command.\n"
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
