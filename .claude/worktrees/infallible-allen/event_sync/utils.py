"""General helper utilities."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Iterable, List


def normalize_header(header: str) -> str:
    """Normalize a spreadsheet header to lowercase snake_case."""

    return header.strip().lower().replace(" ", "_").replace("-", "_")


def build_column_map(headers: Iterable[str], mapping: Dict[str, List[str]]) -> Dict[str, int]:
    """Return a header → column index map using the flexible mapping definition."""

    normalized_headers = [normalize_header(h) for h in headers]
    column_map: Dict[str, int] = {}

    for field_name, possible_names in mapping.items():
        for possible in possible_names:
            normalized = normalize_header(possible)
            if normalized in normalized_headers:
                column_map[field_name] = normalized_headers.index(normalized)
                break

    return column_map


def convert_date_to_iso(date_str: str) -> str:
    """Convert accepted date formats into ISO-8601 (yyyy-mm-dd)."""

    for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y"]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(
        f"Unable to parse date: {date_str}. Expected format: MM/DD/YYYY or YYYY-MM-DD"
    )


def extract_google_drive_file_id(url: str) -> str | None:
    """Extract Google Drive file id from URL or raw id if present."""

    patterns = [r"/file/d/([a-zA-Z0-9_-]+)", r"id=([a-zA-Z0-9_-]+)", r"^([a-zA-Z0-9_-]+)$"]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


