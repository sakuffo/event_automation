"""Runtime coordinator: holds service clients and caches during a sync run."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .config import AppConfig
from wix_client import WixClient
from .config import ConfigError


CacheEntry = Tuple[Optional[bytes], Optional[str], Optional[str]]


class SyncRuntime:
    """Lazily instantiates Google + Wix clients and tracks cross-cutting caches."""

    SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
    DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

    def __init__(self, config: AppConfig):
        self.config = config
        self._wix_client: Optional[WixClient] = None
        self._sheets_service = None
        self._drive_service = None
        self._credentials_info: Optional[Dict[str, Any]] = None
        self._drive_download_cache: Dict[str, CacheEntry] = {}
        self._wix_upload_cache: Dict[str, Dict[str, Any]] = {}
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

    def get_sheets_service(self):
        if self._sheets_service is None:
            creds_dict = self._load_credentials_info()
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=[self.SHEETS_SCOPE]
            )
            self._sheets_service = build("sheets", "v4", credentials=credentials)
        return self._sheets_service

    def get_drive_service(self):
        if self._drive_service is None:
            creds_dict = self._load_credentials_info()
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=[self.DRIVE_SCOPE]
            )
            self._drive_service = build("drive", "v3", credentials=credentials)
        return self._drive_service

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


