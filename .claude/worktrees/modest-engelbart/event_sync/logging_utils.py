"""Logging helpers with a simple, structured-friendly format."""

from __future__ import annotations

import logging
import sys
from typing import Optional


def configure_logging(level: str = "INFO") -> None:
    # Windows consoles often default to cp1252, which can't render the emoji
    # used in log messages; reconfigure stdout to UTF-8 with replacement so
    # logging never raises UnicodeEncodeError.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stdout,
    )

    # httpx logs one INFO line per Notion API request; keep them at WARNING
    # unless the user asked for DEBUG.
    if level.upper() != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or __name__)


