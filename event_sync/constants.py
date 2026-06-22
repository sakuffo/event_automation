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
DEFAULT_REGISTRATION_TYPE = "TICKETING"

# Tax applied at checkout (Ontario HST)
DEFAULT_TAX_NAME = "HST"
DEFAULT_TAX_RATE = "13"
DEFAULT_TAX_TYPE = "ADDED_AT_CHECKOUT"

COLUMN_MAPPING = {
    "event_name": ["event_name", "event name", "name", "title"],
    "category": ["catagories", "categories", "category"],
    "event_type": ["event_type", "event type"],
    "start_date": ["start_date", "start date", "date", "event date"],
    "start_time": ["start_time", "start time", "time"],
    "end_date": ["end_date", "end date"],
    "end_time": ["end_time", "end time"],
    "location": ["location", "venue", "place", "address"],
    "ticket_price": ["ticket_price", "ticket price", "price", "cost"],
    "capacity": ["capacity", "max capacity", "max_capacity", "seats"],
    "registration_type": ["registration_type", "registration type", "reg type", "type"],
    "image_url": ["image_url", "image url", "image", "photo", "picture"],
    "teaser": ["short_description", "short description", "teaser", "summary"],
    "description": ["detailed_description", "detailed description", "desc", "details"],
    "ticket_name": ["ticket_name", "ticket name"],
    "ticket_price": ["ticket_price", "ticket price", "price", "cost"],
    "ticket_capacity": ["ticket_capacity", "capacity", "max capacity", "seats"],
    "fee_type": ["fee_type"],
    "sale_start": ["sale_start"],
    "sale_end": ["sale_end"],
    "tax_name": ["tax_name"],
    "tax_rate": ["tax_rate"],
    "tax_type": ["tax_type"],
}

REQUIRED_FIELDS = ["event_name", "start_date", "start_time", "location"]

DEFAULT_FEE_TYPE = "FEE_ADDED_AT_CHECKOUT"

# Column order for the config_events master configurator tab
CONFIG_COLUMNS = [
    "event_name",
    "categories",
    "start_date",
    "start_time",
    "end_date",
    "end_time",
    "location",
    "registration_type",
    "short_description",
    "detailed_description",
    "image_url",
    "ticket_name",
    "ticket_price",
    "ticket_capacity",
    "fee_type",
    "sale_start",
    "sale_end",
    "tax_name",
    "tax_rate",
    "tax_type",
]

# Column order for the site_config tab (bulk site-wide settings).
# Currently holds one row per eCommerce tax location ("tax_location" setting
# type). Only tax_name / tax_type / tax_rate are editable; the rest are
# read-only context + matching keys for the Wix manual-tax-mapping API.
SITE_CONFIG_COLUMNS = [
    "setting_type",
    "jurisdiction",
    "region",
    "tax_name",
    "tax_type",
    "tax_rate",
    "region_id",
    "group_id",
    "mapping_id",
    "revision",
]

SITE_CONFIG_EDITABLE_COLUMNS = frozenset({"tax_name", "tax_type", "tax_rate"})

SITE_CONFIG_READONLY_COLUMNS = frozenset(
    c for c in SITE_CONFIG_COLUMNS if c not in SITE_CONFIG_EDITABLE_COLUMNS
)

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


