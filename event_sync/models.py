"""Typed models used across the event sync package."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, FrozenSet, List, Optional, Tuple

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .utils import convert_date_to_iso


VALID_REGISTRATION_TYPES = {"RSVP", "TICKETING", "EXTERNAL", "NO_REGISTRATION"}

# Checkout Form values: does every ticket need its own registration form
# (Wix `registration.tickets.guestsAssignedSeparately` = true) or one form
# per order (false)? Blank/None = not managed, Wix dashboard setting wins.
CHECKOUT_FORM_PER_TICKET = "PER_TICKET"
CHECKOUT_FORM_PER_ORDER = "PER_ORDER"
VALID_CHECKOUT_FORMS = {CHECKOUT_FORM_PER_TICKET, CHECKOUT_FORM_PER_ORDER}


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
    # Max tickets a buyer can purchase in one checkout — the Wix event-level
    # `registration.tickets.ticketLimitPerOrder` (Wix defaults it to 20 when
    # unset). The per-ticket-definition `limitPerCheckout` is read-only in the
    # Wix API, so this is the only writable knob.
    ticket_limit_per_order: Optional[int] = None
    # PER_TICKET (each ticket needs its own registration form) or PER_ORDER
    # (one form per checkout) — Wix `guestsAssignedSeparately`. None = not
    # managed: the Wix dashboard setting is left alone.
    checkout_form: Optional[str] = None
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

    # Notion-backend bookkeeping (populated when the record comes from Notion).
    notion_page_id: Optional[str] = None
    wix_event_id: Optional[str] = None
    status: Optional[str] = None
    synced_hash: Optional[str] = None
    # Read-only drift indicator (code-owned Notion column): whether the live
    # event's ticket definitions carry the Settings `default_ticket_policy`.
    # Bookkeeping like synced_hash — never hashed, never pushed to Wix.
    ticket_policy_status: Optional[str] = None

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

    @model_validator(mode="after")
    def ensure_positive_duration(self) -> "EventRecord":
        """Wix rejects events whose end is at or before the start."""
        try:
            start = datetime.strptime(
                f"{self.start_date} {self.start_time}", "%Y-%m-%d %H:%M"
            )
            end = datetime.strptime(
                f"{self.end_date} {self.end_time}", "%Y-%m-%d %H:%M"
            )
        except ValueError:  # pragma: no cover - field validators catch these
            return self
        if end <= start:
            raise ValueError(
                "End must be after start — set an End Time on the Date "
                f"(got {self.start_date} {self.start_time} → "
                f"{self.end_date} {self.end_time})"
            )
        return self

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

    @field_validator("ticket_limit_per_order", mode="before")
    @classmethod
    def normalize_ticket_limit(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                value = float(stripped)
            except ValueError as exc:
                raise ValueError(
                    "Ticket Limit Per Order must be a number"
                ) from exc
        return int(value)

    @field_validator("ticket_limit_per_order")
    @classmethod
    def ensure_ticket_limit_in_range(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if not 1 <= value <= 50:
            raise ValueError(
                "Ticket Limit Per Order must be between 1 and 50 (Wix limit)"
            )
        return value

    @field_validator("checkout_form", mode="before")
    @classmethod
    def normalize_checkout_form(cls, value):
        if value is None:
            return None
        normalized = str(value).strip().upper().replace(" ", "_").replace("-", "_")
        if not normalized:
            return None
        if normalized not in VALID_CHECKOUT_FORMS:
            raise ValueError(
                "Checkout Form must be PER_TICKET or PER_ORDER"
            )
        return normalized

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

    # Fields that influence what gets pushed to Wix. Bookkeeping fields
    # (notion_page_id, wix_event_id, status, synced_hash) are excluded so the
    # hash only changes when a human edit would change the Wix payload.
    HASHED_FIELDS: ClassVar[Tuple[str, ...]] = (
        "name",
        "category",
        "start_date",
        "start_time",
        "end_date",
        "end_time",
        "location",
        "ticket_price",
        "capacity",
        "ticket_limit_per_order",
        "checkout_form",
        "registration_type",
        "image_url",
        "teaser",
        "description",
        "ticket_name",
        "ticket_price_raw",
        "ticket_capacity",
        "fee_type",
        "sale_start",
        "sale_end",
        "tax_name",
        "tax_rate",
        "tax_type",
    )

    # Fields holding semicolon-separated lists whose tokens may be numeric.
    _SEMICOLON_FIELDS: ClassVar[FrozenSet[str]] = frozenset(
        {"ticket_name", "ticket_price_raw", "ticket_capacity", "category"}
    )

    @staticmethod
    def _canonical_token(token: str) -> str:
        token = token.strip()
        try:
            number = float(token)
        except ValueError:
            return token
        if number == int(number):
            return str(int(number))
        return str(number)

    @classmethod
    def _canonical_hash_value(cls, field: str, value) -> str:
        """Normalize a field value so formatting drift doesn't change the hash.

        ``None`` and ``""`` collapse together, floats drop trailing zeros
        (``35.00`` == ``35``), and semicolon lists normalize token spacing.
        """
        if value is None:
            return ""
        if isinstance(value, float):
            return cls._canonical_token(str(value))
        text = str(value).strip()
        if not text:
            return ""
        if field in cls._SEMICOLON_FIELDS or field == "tax_rate":
            tokens = [cls._canonical_token(t) for t in text.split(";")]
            return "; ".join(t for t in tokens if t)
        return text

    def content_hash(self) -> str:
        """Stable hash of the sync-relevant fields.

        Stored in Notion as ``Synced Hash`` after a successful push so later
        runs can detect edits to already-published rows without a snapshot tab.
        """
        payload = {
            field: self._canonical_hash_value(field, getattr(self, field))
            for field in self.HASHED_FIELDS
        }
        # ticket_price is derived from ticket_price_raw whenever raw is set,
        # so hash only the raw form to keep round-tripped records stable.
        if payload.get("ticket_price_raw"):
            payload["ticket_price"] = ""
        canonical = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass
class TicketSpec:
    """Parsed ticket definition from the tickets column.

    ``capacity`` is the definition's total sellable inventory (Wix
    ``initialLimit``). The per-order checkout limit is an event-level
    setting (``EventRecord.ticket_limit_per_order``), not a per-ticket one —
    the ticket definition's ``limitPerCheckout`` is read-only in the Wix API.
    """
    name: str
    price: float
    capacity: int = 24


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


__all__ = [
    "EventRecord",
    "TicketSpec",
    "parse_tickets",
    "ValidationError",
    "CHECKOUT_FORM_PER_TICKET",
    "CHECKOUT_FORM_PER_ORDER",
    "VALID_CHECKOUT_FORMS",
]


