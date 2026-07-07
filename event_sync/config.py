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
    sheet_range: str = "generated_events!A1:Z500"
    timezone: str = "America/Toronto"
    rolling_schedule_tab: str = "rolling_schedule"
    class_info_tab: str = "class_info"
    defaults_tab: str = "defaults"
    generated_events_tab: str = "generated_events"
    config_events_tab: str = "config_events"
    category_config_tab: str = "category_config"
    site_config_tab: str = "site_config"
    # Separate source sheet for generate command (defaults to google_sheet_id if not set)
    source_sheet_id: Optional[str] = None
    # Notion backend
    notion_token: Optional[str] = None
    notion_parent_page_id: Optional[str] = None
    notion_event_scheduling_db_id: Optional[str] = None
    notion_catalog_db_id: Optional[str] = None
    notion_settings_db_id: Optional[str] = None
    notion_site_config_db_id: Optional[str] = None
    _google_credentials_cache: Optional[Dict[str, Any]] = field(
        default=None, init=False, repr=False
    )

    @property
    def generator_sheet_id(self) -> Optional[str]:
        """Sheet ID for generate command (source data)."""
        return self.source_sheet_id or self.google_sheet_id

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

    def notion_validation_errors(self, require_databases: bool = True) -> List[str]:
        """Validation for Notion-backed commands.

        ``require_databases=False`` is used by ``setup-notion``, which only
        needs the token — it guides the user through picking a parent page.
        """
        errors: List[str] = []
        if not self.notion_token:
            errors.append("NOTION_ACCESS_TOKEN is missing")
        if require_databases:
            for env_name, value in (
                ("NOTION_EVENT_SCHEDULING_DB_ID", self.notion_event_scheduling_db_id),
                ("NOTION_CATALOG_DB_ID", self.notion_catalog_db_id),
                ("NOTION_SETTINGS_DB_ID", self.notion_settings_db_id),
                ("NOTION_SITE_CONFIG_DB_ID", self.notion_site_config_db_id),
            ):
                if not value:
                    errors.append(f"{env_name} is missing (run setup-notion first)")
        return errors

    def ensure_notion_valid(self, require_databases: bool = True) -> None:
        errors = self.notion_validation_errors(require_databases=require_databases)
        if errors:
            raise ConfigError("; ".join(errors))

    def ensure_wix_valid(self) -> None:
        errors: List[str] = []
        if not self.wix_api_key:
            errors.append("WIX_API_KEY is missing")
        if not self.wix_site_id:
            errors.append("WIX_SITE_ID is missing")
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
        rolling_schedule_tab=os.getenv("ROLLING_SCHEDULE_TAB", "rolling_schedule"),
        class_info_tab=os.getenv("CLASS_INFO_TAB", "class_info"),
        defaults_tab=os.getenv("DEFAULTS_TAB", "defaults"),
        generated_events_tab=os.getenv("GENERATED_EVENTS_TAB", "generated_events"),
        category_config_tab=os.getenv("CATEGORY_CONFIG_TAB", "category_config"),
        site_config_tab=os.getenv("SITE_CONFIG_TAB", "site_config"),
        source_sheet_id=os.getenv("SOURCE_SHEET_ID"),
        notion_token=os.getenv("NOTION_ACCESS_TOKEN") or os.getenv("NOTION_TOKEN"),
        notion_parent_page_id=os.getenv("NOTION_PARENT_PAGE_ID"),
        # NOTION_EVENTS_DB_ID / NOTION_CLASSES_DB_ID are the pre-redesign
        # names, kept as fallbacks so old .env files and CI secrets keep
        # working.
        notion_event_scheduling_db_id=os.getenv("NOTION_EVENT_SCHEDULING_DB_ID")
        or os.getenv("NOTION_EVENTS_DB_ID"),
        notion_catalog_db_id=os.getenv("NOTION_CATALOG_DB_ID")
        or os.getenv("NOTION_CLASSES_DB_ID"),
        notion_settings_db_id=os.getenv("NOTION_SETTINGS_DB_ID"),
        notion_site_config_db_id=os.getenv("NOTION_SITE_CONFIG_DB_ID"),
    )


