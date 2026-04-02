"""Logging helpers with a simple, structured-friendly format."""

from __future__ import annotations

import logging
import sys
from typing import Optional


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stdout,
    )


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or __name__)


