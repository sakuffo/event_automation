"""Typed models used across the event sync package."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from .utils import convert_date_to_iso


VALID_REGISTRATION_TYPES = {"RSVP", "TICKETING", "EXTERNAL", "NO_REGISTRATION"}


class EventRecord(BaseModel):
    name: str = Field(..., min_length=1)
    event_type: Optional[str] = None
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    location: str = Field(..., min_length=1)
    ticket_price: float = 0.0
    capacity: int = 100
    registration_type: str = "RSVP"
    image_url: Optional[str] = None
    teaser: Optional[str] = None
    description: Optional[str] = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_dates(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Date value is required")
        return convert_date_to_iso(value.strip())

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def validate_times(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Time value is required")
        candidate = value.strip()
        try:
            datetime.strptime(candidate, "%H:%M")
        except ValueError as exc:
            raise ValueError("Time must be HH:MM (24-hour)") from exc
        return candidate

    @field_validator("registration_type", mode="before")
    @classmethod
    def normalize_registration(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            return "RSVP"
        normalized = value.strip().upper()
        if normalized == "TICKETS":
            normalized = "TICKETING"
        if normalized not in VALID_REGISTRATION_TYPES:
            raise ValueError(
                f"registration_type must be one of {', '.join(sorted(VALID_REGISTRATION_TYPES))}"
            )
        return normalized

    @field_validator("ticket_price")
    @classmethod
    def ensure_non_negative_price(cls, value: float) -> float:
        return max(0.0, float(value))

    @field_validator("capacity")
    @classmethod
    def ensure_capacity_positive(cls, value: int) -> int:
        value_int = int(value)
        if value_int <= 0:
            raise ValueError("capacity must be greater than zero")
        return value_int

    @field_validator("image_url", "teaser", "description", "event_type", mode="before")
    @classmethod
    def empty_str_to_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def to_payload(self) -> dict:
        """Return a plain dict suitable for orchestration or logging."""

        return self.model_dump()


__all__ = ["EventRecord", "ValidationError"]


