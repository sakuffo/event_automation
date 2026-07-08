"""Runtime coordinator: holds service clients and caches during a sync run."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from .config import AppConfig, ConfigError
from .wix_client import WixClient


CacheEntry = Tuple[Optional[bytes], Optional[str], Optional[str]]


class SyncRuntime:
    """Lazily instantiates Google Drive + Wix + Notion clients and tracks caches."""

    DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

    def __init__(self, config: AppConfig):
        self.config = config
        self._wix_client: Optional[WixClient] = None
        self._drive_service = None
        self._notion_store = None
        self._credentials_info: Optional[Dict[str, Any]] = None
        self._drive_download_cache: Dict[str, CacheEntry] = {}
        self._wix_upload_cache: Dict[str, Dict[str, Any]] = {}
        # Set by create_wix_event when an image upload fails (the event is
        # still created); sync surfaces it as a Sync Error note on the row.
        self.last_image_failure: Optional[str] = None
        self.cache_stats = {
            "drive_hits": 0,
            "drive_misses": 0,
            "wix_hits": 0,
            "wix_uploads": 0,
        }

    # -------------------------
    # Client factories
    # -------------------------
    def _load_credentials_info(self) -> Dict[str, Any]:
        if self._credentials_info is None:
            creds_json = self.config.google_credentials
            if not creds_json:
                raise ConfigError("GOOGLE_CREDENTIALS is missing or invalid")
            self._credentials_info = json.loads(json.dumps(creds_json))
        return self._credentials_info

    def get_wix_client(self) -> WixClient:
        if self._wix_client is None:
            self._wix_client = WixClient(
                api_key=self.config.wix_api_key,
                site_id=self.config.wix_site_id,
                account_id=self.config.wix_account_id,
            )
        return self._wix_client

    def get_drive_service(self):
        """Google Drive client for downloading event images.

        The Google SDK import lives here so Notion-only commands never pay
        its ~1s import cost (or need it installed at all).
        """
        if self._drive_service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds_dict = self._load_credentials_info()
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=[self.DRIVE_SCOPE]
            )
            self._drive_service = build("drive", "v3", credentials=credentials)
        return self._drive_service

    def get_notion_store(self):
        if self._notion_store is None:
            from .notion_store import NotionStore

            if not self.config.notion_token:
                raise ConfigError("NOTION_ACCESS_TOKEN is missing")
            self._notion_store = NotionStore(self.config)
        return self._notion_store

    # -------------------------
    # Caching helpers
    # -------------------------
    def get_cached_drive_file(self, file_id: str) -> Optional[CacheEntry]:
        return self._drive_download_cache.get(file_id)

    def cache_drive_file(self, file_id: str, payload: CacheEntry) -> None:
        self._drive_download_cache[file_id] = payload

    def get_cached_wix_media(self, file_id: str) -> Optional[Dict[str, Any]]:
        return self._wix_upload_cache.get(file_id)

    def cache_wix_media(self, file_id: str, descriptor: Dict[str, Any]) -> None:
        self._wix_upload_cache[file_id] = descriptor

    def record_drive_hit(self) -> None:
        self.cache_stats["drive_hits"] += 1

    def record_drive_miss(self) -> None:
        self.cache_stats["drive_misses"] += 1

    def record_wix_hit(self) -> None:
        self.cache_stats["wix_hits"] += 1

    def record_wix_upload(self) -> None:
        self.cache_stats["wix_uploads"] += 1
