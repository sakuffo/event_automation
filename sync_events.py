#!/usr/bin/env python3
"""Compatibility wrapper that delegates to the modular CLI entrypoint."""

from __future__ import annotations

import sys
from typing import Iterable, Optional

from event_sync.cli import main as package_main


def main(argv: Optional[Iterable[str]] = None) -> int:
    """Forward to the package CLI to maintain backwards compatibility."""

    return package_main(list(argv) if argv is not None else None)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())