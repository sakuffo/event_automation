"""Shared constants used across the event sync package."""

# Category-based pricing for generated events
CATEGORY_PRICING = {
    "Your First Rope Class": 30,
    "Beginner Rope For Play": 30,
    "Simple harnesses": 30,
    "Simple Harnesses": 30,
    "Beginner self tying": 30,
    "Beginner Self Tying": 30,
    "Beginner Self tying": 30,
    "Complex harnesses": 35,
    "Complex Harness": 35,
    "Complex Harnesses": 35,
    "Turn up the heat (intermediate play)": 35,
    "Turn up the Heat": 35,
    "Suspension lines": 35,
    "Suspension Lines": 35,
    "Exploring Self Tying": 30,
    "Exploring Self tying": 30,
    "Exploring Self Tying In the Air": 35,
    "Exploring Self Tying Air": 35,
    "The middle ground": 35,
    "The Middle Ground": 35,
    "The body in flight": 40,
    "The Body in Flight": 40,
    "The Body In Flight": 40,
    "Anatomy lab": 35,
    "Anatomy Lab": 35,
    "Bottoming": 30,
    "Taking to the stage": 40,
    "Taking to the Stage": 40,
    "Adding objects": 35,
    "Tips and Tricks": 30,
    "Tip and Tricks": 30,
    "Mastering Play": 40,
    "Special": 30,  # Default price for special events
}

# Default values for generated events
DEFAULT_LOCATION = "1233R Queen St W, Toronto, ON M6K 1L5, Canada"
DEFAULT_CAPACITY = 24

# Last-resort ticket price for ticketed rows whose price is still blank after
# the template / category-pricing fills — a TICKETING event published without
# a price gets no ticket definition and shows "Tickets are not on sale".
DEFAULT_TICKET_PRICE = 30.0

# Max tickets per checkout order (event-level Wix ticketLimitPerOrder).
# Wix defaults to 20 when the field is left unset — far more than one buyer
# ever needs here — so ticketed rows are filled with this instead.
DEFAULT_TICKET_LIMIT_PER_ORDER = 4

# Tax applied at checkout (Ontario HST)
DEFAULT_TAX_NAME = "HST"
DEFAULT_TAX_RATE = "13"
DEFAULT_TAX_TYPE = "ADDED_AT_CHECKOUT"

DEFAULT_FEE_TYPE = "FEE_ADDED_AT_CHECKOUT"

# Value of the setting_type column for tax-location rows in site_config.
TAX_LOCATION_SETTING = "tax_location"


def tax_rate_percent_to_decimal(value: str) -> str:
    """Convert a human percent (``"13"`` or ``"13%"``) to a Wix decimal string.

    ``"13"`` -> ``"0.13"``. Returns ``""`` for blank/invalid input so callers
    can treat it as "no rate specified".
    """
    text = str(value or "").strip().rstrip("%").strip()
    if not text:
        return ""
    try:
        return str(float(text) / 100)
    except ValueError:
        return ""


def tax_rate_decimal_to_percent(value: str) -> str:
    """Convert a Wix decimal rate (``"0.130000"``) to a human percent (``"13"``).

    Returns ``""`` for blank/invalid input. Whole percents drop the trailing
    ``.0`` so ``"0.13"`` displays as ``"13"`` rather than ``"13.0"``.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        pct = float(text) * 100
    except ValueError:
        return ""
    if pct == int(pct):
        return str(int(pct))
    return str(round(pct, 6))


MAX_WIX_IMAGE_BYTES = 25 * 1024 * 1024


