"""Typed models used across the event sync package."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from .utils import convert_date_to_iso


VALID_REGISTRATION_TYPES = {"RSVP", "TICKETING", "EXTERNAL", "NO_REGISTRATION"}


class EventRecord(BaseModel):
    name: str = Field(..., min_length=1)
    category: Optional[str] = None
    event_type: Optional[str] = None
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    location: str = Field(..., min_length=1)
    ticket_price: float = 0.0
    capacity: int = 24
    registration_type: str = "RSVP"
    image_url: Optional[str] = None
    teaser: Optional[str] = None
    description: Optional[str] = None

    # Extended fields for config_events (optional, used by push-config)
    tickets: Optional[str] = None
    fee_type: Optional[str] = None
    sale_start: Optional[str] = None
    sale_end: Optional[str] = None
    tax_name: Optional[str] = None
    tax_rate: Optional[str] = None
    tax_type: Optional[str] = None

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

    @field_validator(
        "image_url", "teaser", "description", "event_type", "category",
        "tickets", "fee_type", "sale_start", "sale_end",
        "tax_name", "tax_rate", "tax_type",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def to_payload(self) -> dict:
        """Return a plain dict suitable for orchestration or logging."""
        return self.model_dump()


@dataclass
class TicketSpec:
    """Parsed ticket definition from the tickets column."""
    name: str
    price: float
    capacity: int = 24
    limit_per_checkout: int = 4


def parse_tickets(raw: Optional[str], default_capacity: int = 24) -> List[TicketSpec]:
    """Parse a tickets column value into a list of TicketSpec objects.

    Format per ticket: ``Name:Price[:Capacity[:LimitPerCheckout]]``
    Multiple tickets separated by ``;``.
    """
    if not raw or not raw.strip():
        return []

    specs: List[TicketSpec] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        pieces = [p.strip() for p in part.split(":")]
        if len(pieces) < 2:
            continue
        name = pieces[0]
        try:
            price = float(pieces[1])
        except ValueError:
            continue
        capacity = default_capacity
        limit = 4
        if len(pieces) >= 3 and pieces[2]:
            try:
                capacity = int(pieces[2])
            except ValueError:
                pass
        if len(pieces) >= 4 and pieces[3]:
            try:
                limit = int(pieces[3])
            except ValueError:
                pass
        specs.append(TicketSpec(name=name, price=price, capacity=capacity, limit_per_checkout=limit))
    return specs


__all__ = ["EventRecord", "TicketSpec", "parse_tickets", "ValidationError"]


