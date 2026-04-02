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
    # For multiple tickets, separate with ; (e.g. "Regular; Student")
    ticket_name: Optional[str] = None
    ticket_price_raw: Optional[str] = None
    ticket_capacity: Optional[str] = None
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
        "ticket_name", "ticket_price_raw", "ticket_capacity",
        "fee_type", "sale_start", "sale_end",
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


def parse_tickets(
    ticket_name: Optional[str] = None,
    ticket_price=None,
    ticket_capacity: Optional[str] = None,
    default_capacity: int = 24,
) -> List[TicketSpec]:
    """Build ticket specs from separate name/price/capacity fields.

    Each field can hold multiple values separated by ``;`` for multi-ticket events.
    ``ticket_price`` can be a float, int, or semicolon-separated string.
    """
    if not ticket_name or ticket_price is None:
        return []

    names = [n.strip() for n in ticket_name.split(";") if n.strip()]
    if not names:
        return []

    price_str = str(ticket_price)
    price_parts = [p.strip() for p in price_str.split(";")]
    prices: List[float] = []
    for p in price_parts:
        try:
            prices.append(float(p))
        except ValueError:
            prices.append(0.0)

    # Parse capacities
    cap_parts = [c.strip() for c in (ticket_capacity or "").split(";")] if ticket_capacity else []
    capacities: List[int] = []
    for c in cap_parts:
        try:
            capacities.append(int(c)) if c else capacities.append(default_capacity)
        except ValueError:
            capacities.append(default_capacity)

    specs: List[TicketSpec] = []
    for i, name in enumerate(names):
        price = prices[i] if i < len(prices) else prices[-1] if prices else 0.0
        capacity = capacities[i] if i < len(capacities) else default_capacity
        specs.append(TicketSpec(name=name, price=price, capacity=capacity))

    return specs


__all__ = ["EventRecord", "TicketSpec", "parse_tickets", "ValidationError"]


