"""Environment + runtime configuration helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv


load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


@dataclass
class AppConfig:
    """Holds the environment-driven settings for the sync process."""

    wix_api_key: Optional[str]
    wix_account_id: Optional[str]
    wix_site_id: Optional[str]
    google_sheet_id: Optional[str]
    google_credentials_raw: Optional[str]
    sheet_range: str = "Sheet1!A1:Z100"
    timezone: str = "America/Toronto"
    _google_credentials_cache: Optional[Dict[str, Any]] = field(
        default=None, init=False, repr=False
    )

    def validation_errors(self) -> List[str]:
        errors: List[str] = []
        if not self.wix_api_key:
            errors.append("WIX_API_KEY is missing")
        if not self.wix_site_id:
            errors.append("WIX_SITE_ID is missing")
        if not self.google_sheet_id:
            errors.append("GOOGLE_SHEET_ID is missing")
        if not self.google_credentials_raw:
            errors.append("GOOGLE_CREDENTIALS is missing")
        else:
            creds = self.google_credentials
            if not creds:
                errors.append("GOOGLE_CREDENTIALS is not valid JSON or missing client_email")
        return errors

    def ensure_valid(self) -> None:
        errors = self.validation_errors()
        if errors:
            raise ConfigError("; ".join(errors))

    @property
    def google_credentials(self) -> Optional[Dict[str, Any]]:
        if self._google_credentials_cache is not None:
            return self._google_credentials_cache
        if not self.google_credentials_raw:
            return None
        try:
            parsed = json.loads(self.google_credentials_raw)
            if "client_email" not in parsed:
                return None
            self._google_credentials_cache = parsed
            return parsed
        except json.JSONDecodeError:
            return None


def load_config() -> AppConfig:
    """Read settings from the environment and return an ``AppConfig`` instance."""

    return AppConfig(
        wix_api_key=os.getenv("WIX_API_KEY"),
        wix_account_id=os.getenv("WIX_ACCOUNT_ID"),
        wix_site_id=os.getenv("WIX_SITE_ID"),
        google_sheet_id=os.getenv("GOOGLE_SHEET_ID"),
        google_credentials_raw=os.getenv("GOOGLE_CREDENTIALS"),
    )


