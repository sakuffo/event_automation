"""Pure Wix mapping helpers: timestamps, description HTML, payloads, diffs.

Everything here converts between our row/record shapes and Wix API shapes
without performing I/O. The mutation flows that call the Wix API live in
``wix_flows``; Notion I/O lives in ``notion_store``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import escape
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

try:  # pragma: no cover - standard library on Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - fallback for older runtimes
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

try:  # pragma: no cover - optional dependency
    import pytz  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    pytz = None  # type: ignore

from .constants import (
    DEFAULT_FEE_TYPE,
    DEFAULT_TAX_NAME,
    DEFAULT_TAX_RATE,
    DEFAULT_TAX_TYPE,
    TAX_LOCATION_SETTING,
    tax_rate_decimal_to_percent,
)
from .logging_utils import get_logger
from .models import (
    CHECKOUT_FORM_PER_ORDER,
    CHECKOUT_FORM_PER_TICKET,
    EventRecord,
)
from .utils import convert_date_to_iso

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .runtime import SyncRuntime


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Timestamps and timezone conversion
# ---------------------------------------------------------------------------


def wix_timestamp(date_iso: str, time_24h: str, tz_name: str) -> str:
    """Return a UTC timestamp string for Wix while respecting the site timezone."""

    naive = datetime.strptime(f"{date_iso} {time_24h}", "%Y-%m-%d %H:%M")

    if ZoneInfo is not None:
        try:
            local_tz = ZoneInfo(tz_name)
            utc_tz = ZoneInfo("UTC")
            localized = naive.replace(tzinfo=local_tz)
            return localized.astimezone(utc_tz).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ZoneInfoNotFoundError as exc:
            logger.warning("⚠️  Unknown timezone '%s' via zoneinfo: %s", tz_name, exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("⚠️  Failed zoneinfo conversion for %s: %s", tz_name, exc)

    if pytz is not None:
        try:
            local_tz = pytz.timezone(tz_name)
            localized = local_tz.localize(naive)
            return localized.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("⚠️  Failed pytz conversion for %s: %s", tz_name, exc)

    logger.warning(
        "⚠️  Falling back to naive UTC timestamp for timezone '%s'", tz_name
    )
    return f"{date_iso}T{time_24h}:00Z"


def localize_wix_start(start_datetime: str, tz_name: str) -> Optional[Tuple[str, str]]:
    """Return (date_iso, time_24h) converted to the requested timezone."""

    if not start_datetime:
        return None

    try:
        normalized = start_datetime.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            trimmed = start_datetime
            if "." in trimmed:
                trimmed = trimmed.split(".")[0]
            trimmed = trimmed.replace("Z", "")
            dt_utc = datetime.strptime(trimmed, "%Y-%m-%dT%H:%M:%S")
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        except Exception:
            logger.warning("⚠️  Could not parse Wix startDate '%s'", start_datetime)
            return None
    else:
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    if ZoneInfo is not None:
        try:
            local_tz = ZoneInfo(tz_name)
            local_dt = dt_utc.astimezone(local_tz)
            return local_dt.strftime("%Y-%m-%d"), local_dt.strftime("%H:%M")
        except ZoneInfoNotFoundError as exc:
            logger.warning("⚠️  Unknown timezone '%s' via zoneinfo: %s", tz_name, exc)
        except Exception as exc:
            logger.warning("⚠️  Failed zoneinfo localization for %s: %s", tz_name, exc)

    if pytz is not None:
        try:
            local_tz = pytz.timezone(tz_name)
            local_dt = dt_utc.astimezone(local_tz)
            return local_dt.strftime("%Y-%m-%d"), local_dt.strftime("%H:%M")
        except Exception as exc:
            logger.warning("⚠️  Failed pytz localization for %s: %s", tz_name, exc)

    fallback = dt_utc.astimezone(timezone.utc)
    logger.warning(
        "⚠️  Falling back to UTC startDate for timezone '%s'", tz_name
    )
    return fallback.strftime("%Y-%m-%d"), fallback.strftime("%H:%M")


def normalize_wix_timestamp(timestamp: str) -> Optional[str]:
    """Return a canonical UTC timestamp string for comparison against expected values."""

    if not timestamp:
        return None

    try:
        normalized = timestamp.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(normalized)
    except ValueError:
        trimmed = timestamp
        if "." in trimmed:
            trimmed = trimmed.split(".")[0]
        trimmed = trimmed.replace("Z", "")
        try:
            dt_utc = datetime.strptime(trimmed, "%Y-%m-%dT%H:%M:%S")
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        except Exception:
            logger.warning("⚠️  Could not normalize Wix timestamp '%s'", timestamp)
            return None
    else:
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    return dt_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Event matching keys
# ---------------------------------------------------------------------------


def event_match_key(title: str, date_iso: str, time_hhmm: str) -> str:
    """Fallback match key when a row has no Wix Event ID.

    Every producer and consumer of ``title|YYYY-MM-DD|HH:MM`` keys must go
    through this helper — matching is the never-duplicate-a-Wix-event
    invariant, and byte-compatible keys are what enforce it.
    """
    return f"{(title or '').strip()}|{date_iso}|{time_hhmm}"


def wix_event_match_key(wix_event: Dict[str, Any], tz_name: str) -> Optional[str]:
    """Match key for a live Wix event, or None when it can't be keyed."""
    title = (wix_event.get("title") or "").strip()
    start_settings = wix_event.get("dateAndTimeSettings", {}) or {}
    start_raw = start_settings.get("startDate", "")
    if not title or not start_raw:
        return None
    localized = localize_wix_start(start_raw, tz_name)
    if localized is None:
        return None
    date_part, time_part = localized
    return event_match_key(title, date_part, time_part)


# ---------------------------------------------------------------------------
# Month filters
# ---------------------------------------------------------------------------


def parse_month_value(value: str) -> int:
    """Parse month value from names/abbreviations into month number."""
    token = value.strip().lower()
    if not token:
        raise ValueError("Month filter cannot be empty")

    month_map = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    if token in month_map:
        return month_map[token]

    raise ValueError(
        "Invalid month filter. Use values like 'mar', 'MAR', or 'March'."
    )


# ---------------------------------------------------------------------------
# Description formatting (plain text / markdown-ish -> Wix HTML)
# ---------------------------------------------------------------------------


_BULLET_MARKERS = ("- ", "* ", "• ", "– ", "— ")

# HTML tags that are preserved (not escaped) when found in description text.
SAFE_HTML_TAGS = frozenset({
    "b", "i", "u", "em", "strong", "a", "br", "p", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "span", "div", "blockquote",
})

_TAG_RE = re.compile(r'(<[^>]+>)')
_TAG_NAME_RE = re.compile(r'^</?([a-zA-Z][a-zA-Z0-9]*)')
_BLOCK_TAG_RE = re.compile(r'<(?:p|div|ul|ol|h[1-6]|blockquote)[\s>/]', re.IGNORECASE)


def _escape_preserving_html(text: str) -> str:
    """Escape plain text but preserve whitelisted HTML tags."""
    parts = _TAG_RE.split(text)
    result: List[str] = []
    for part in parts:
        m = _TAG_NAME_RE.match(part)
        if m and m.group(1).lower() in SAFE_HTML_TAGS:
            result.append(part)
        else:
            result.append(escape(part))
    return "".join(result)


def _is_complete_html(text: str) -> bool:
    """Return True if text is already fully-formed HTML with block tags."""
    stripped = text.strip()
    return (
        stripped.startswith("<")
        and stripped.endswith(">")
        and _BLOCK_TAG_RE.search(stripped) is not None
    )


def _extract_bullet_text(line: str) -> Optional[str]:
    stripped = line.lstrip()
    for marker in _BULLET_MARKERS:
        if stripped.startswith(marker):
            return stripped[len(marker) :].strip()
    return None


def _is_bullet(line: str) -> bool:
    return _extract_bullet_text(line) is not None


def _inline_markdown(text: str) -> str:
    """Apply inline markdown formatting to already-escaped HTML text.

    Supported: **bold**, *italic*, [text](url)
    """
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<a href="\2" target="_blank">\1</a>',
        text,
    )
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    return text


def _format_line(text: str) -> str:
    """Escape non-tag text, preserve safe HTML tags, apply inline markdown."""
    return _inline_markdown(_escape_preserving_html(text.strip()))


def format_description_as_html(raw: str) -> str:
    """Convert description text into HTML for Wix.

    Supports paragraphs, bullet lists, **bold**, *italic*, [links](url),
    and inline HTML tags (<b>, <i>, <a>, etc.).

    Paragraph breaks occur on blank lines AND at transitions between
    regular text and bullet-list lines.
    """
    if not raw:
        return ""

    normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""

    # Already fully-formed HTML — sanitize unsafe tags and return as-is.
    if _is_complete_html(normalized):
        return _escape_preserving_html(normalized)

    lines = normalized.split("\n")

    # Split lines into blocks, breaking on blank lines AND on transitions
    # between bullet and non-bullet lines.
    blocks: List[List[str]] = []
    current: List[str] = []
    current_is_bullet: Optional[bool] = None

    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
                current_is_bullet = None
            continue

        line_is_bullet = _is_bullet(line)

        # Transition between text and bullets (or vice versa) → new block
        if current and current_is_bullet is not None and line_is_bullet != current_is_bullet:
            blocks.append(current)
            current = []

        current.append(line.rstrip())
        current_is_bullet = line_is_bullet

    if current:
        blocks.append(current)

    html_blocks: List[str] = []

    for block in blocks:
        # Check if all lines in this block are bullets
        if all(_is_bullet(entry) for entry in block):
            items = [_format_line(_extract_bullet_text(entry)) for entry in block]
            items_html = "".join(f"<li>{item}</li>" for item in items)
            html_blocks.append(f"<ul>{items_html}</ul>")
        else:
            joined = "<br/>".join(_format_line(item) for item in block)
            html_blocks.append(f"<p>{joined}</p>")

    return "".join(html_blocks)


# ---------------------------------------------------------------------------
# EventRecord -> Wix payload, and field diffing
# ---------------------------------------------------------------------------


def build_wix_event_payload(
    event: EventRecord,
    runtime: "SyncRuntime",
    *,
    file_descriptor: Optional[Dict[str, Any]] = None,
    existing_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    start_date_iso = convert_date_to_iso(event.start_date)
    end_date_iso = convert_date_to_iso(event.end_date)

    title = event.name.strip()

    event_data: Dict[str, Any] = {
        "title": title,
        "dateAndTimeSettings": {
            "dateAndTimeTbd": False,
            "startDate": wix_timestamp(
                start_date_iso, event.start_time, runtime.config.timezone
            ),
            "endDate": wix_timestamp(
                end_date_iso, event.end_time, runtime.config.timezone
            ),
            "timeZoneId": runtime.config.timezone,
        },
        "location": {
            "type": "VENUE",
            "address": {"formattedAddress": event.location},
        },
    }

    if existing_event is None:
        event_data["registration"] = {
            "initialType": event.registration_type,
        }
        if event.registration_type == "TICKETING":
            tickets_settings: Dict[str, Any] = {
                "taxSettings": {
                    "type": event.tax_type or DEFAULT_TAX_TYPE,
                    "name": event.tax_name or DEFAULT_TAX_NAME,
                    "rate": event.tax_rate or DEFAULT_TAX_RATE,
                }
            }
            # Max tickets per checkout — without this Wix defaults to 20.
            if event.ticket_limit_per_order:
                tickets_settings["ticketLimitPerOrder"] = (
                    event.ticket_limit_per_order
                )
            # One registration form per ticket vs per order; omitted when
            # blank so the Wix default (per order) applies.
            guests_assigned = checkout_form_to_guests_assigned(
                event.checkout_form
            )
            if guests_assigned is not None:
                tickets_settings["guestsAssignedSeparately"] = guests_assigned
            event_data["registration"]["tickets"] = tickets_settings

    teaser = event.teaser.strip() if event.teaser else ""
    if teaser:
        event_data["shortDescription"] = teaser
    elif existing_event and existing_event.get("shortDescription"):
        event_data["shortDescription"] = ""

    formatted_description = format_description_as_html(event.description or "")
    if formatted_description:
        event_data["detailedDescription"] = formatted_description
    elif existing_event and existing_event.get("detailedDescription"):
        event_data["detailedDescription"] = ""

    if file_descriptor and "id" in file_descriptor:
        width = height = None
        media = file_descriptor.get("media") or {}
        if isinstance(media, dict) and "image" in media:
            media_image = media.get("image") or {}
            if isinstance(media_image, dict):
                image_info = media_image.get("image") or {}
                if isinstance(image_info, dict):
                    width = image_info.get("width")
                    height = image_info.get("height")

        main_image: Dict[str, Any] = {"id": file_descriptor["id"]}
        if width and height:
            main_image["width"] = width
            main_image["height"] = height
        event_data["mainImage"] = main_image
    elif existing_event and existing_event.get("mainImage"):
        event_data["mainImage"] = existing_event["mainImage"]

    return event_data


EventDiff = Tuple[str, Any, Any]


def diff_event_fields(
    event: EventRecord,
    existing_event: Dict[str, Any],
    runtime: "SyncRuntime",
) -> List[EventDiff]:
    """Return ``(field, expected, actual_in_wix)`` for each field that differs.

    An empty list means the record and Wix agree on every field we sync.
    """
    diffs: List[EventDiff] = []

    expected_title = event.name.strip()
    actual_title = existing_event.get("title") or ""
    if expected_title != actual_title:
        diffs.append(("title", expected_title, actual_title))

    expected_start = wix_timestamp(
        convert_date_to_iso(event.start_date),
        event.start_time,
        runtime.config.timezone,
    )
    expected_end = wix_timestamp(
        convert_date_to_iso(event.end_date),
        event.end_time,
        runtime.config.timezone,
    )

    date_settings = existing_event.get("dateAndTimeSettings") or {}
    actual_start = normalize_wix_timestamp(date_settings.get("startDate") or "")
    if actual_start != expected_start:
        diffs.append(("startDate", expected_start, actual_start))

    actual_end = normalize_wix_timestamp(date_settings.get("endDate") or "")
    if actual_end != expected_end:
        diffs.append(("endDate", expected_end, actual_end))

    expected_tz = runtime.config.timezone
    actual_tz = date_settings.get("timeZoneId") or ""
    if expected_tz != actual_tz:
        diffs.append(("timezone", expected_tz, actual_tz))

    location_settings = existing_event.get("location") or {}
    actual_location = (
        (location_settings.get("address") or {}).get("formattedAddress") or ""
    )
    if event.location != actual_location:
        diffs.append(("location", event.location, actual_location))

    expected_teaser = event.teaser.strip() if event.teaser else ""
    actual_teaser = existing_event.get("shortDescription") or ""
    if expected_teaser != actual_teaser:
        diffs.append(("shortDescription", expected_teaser, actual_teaser))

    expected_description = format_description_as_html(event.description or "")
    actual_description = existing_event.get("detailedDescription") or ""
    if expected_description != actual_description:
        diffs.append(("detailedDescription", expected_description, actual_description))

    return diffs


def log_event_diff(event_name: str, diffs: List[EventDiff]) -> None:
    """Log a per-field diff. Field names at INFO, full values at DEBUG."""
    if not diffs:
        return
    changed = ", ".join(name for name, _, _ in diffs)
    logger.info("   📝 Changed fields: %s", changed)
    for name, expected, actual in diffs:
        logger.debug(
            "      %s\n        notion: %r\n        wix   : %r",
            name, expected, actual,
        )


# ---------------------------------------------------------------------------
# Checkout Form <-> Wix guestsAssignedSeparately converters
# ---------------------------------------------------------------------------


def checkout_form_to_guests_assigned(
    checkout_form: Optional[str],
) -> Optional[bool]:
    """``PER_TICKET`` -> True, ``PER_ORDER`` -> False, blank -> None (not managed)."""
    value = (checkout_form or "").strip().upper()
    if not value:
        return None
    return value == CHECKOUT_FORM_PER_TICKET


def guests_assigned_to_checkout_form(
    guests_assigned: Optional[bool],
) -> str:
    """Wix ``guestsAssignedSeparately`` -> the Checkout Form select value."""
    if guests_assigned is None:
        return ""
    return CHECKOUT_FORM_PER_TICKET if guests_assigned else CHECKOUT_FORM_PER_ORDER


# ---------------------------------------------------------------------------
# Wix event -> config row (the read side of "Wix is authoritative")
# ---------------------------------------------------------------------------


def wix_event_to_config_row(
    wix_event: Dict[str, Any],
    ticket_defs: List[Dict[str, Any]],
    tz_name: str = "America/Toronto",
) -> Dict[str, Any]:
    """Convert a Wix event + its ticket definitions into a config-row dict."""
    date_settings = wix_event.get("dateAndTimeSettings", {})
    location = wix_event.get("location", {})
    address = (location.get("address") or {}).get("formattedAddress", "")
    registration = wix_event.get("registration", {})
    reg_type = registration.get("initialType") or registration.get("type", "")
    tickets_reg = registration.get("tickets") or {}
    tax = tickets_reg.get("taxSettings", {})
    ticket_limit = tickets_reg.get("ticketLimitPerOrder")
    # Wix omits false booleans, so any non-empty tickets object implies a
    # known value; an absent/empty one (RSVP events) stays blank.
    checkout_form = (
        guests_assigned_to_checkout_form(
            bool(tickets_reg.get("guestsAssignedSeparately"))
        )
        if tickets_reg
        else ""
    )

    # Extract category names from the CATEGORIES fieldset
    cat_data = wix_event.get("categories", {})
    cat_list = cat_data.get("categories", []) if isinstance(cat_data, dict) else []
    category_names = [c.get("name", "") for c in cat_list if c.get("name")]
    categories_str = "; ".join(category_names)

    start_raw = date_settings.get("startDate", "")
    end_raw = date_settings.get("endDate", "")

    def _localize(iso_str):
        if not iso_str:
            return "", ""
        result = localize_wix_start(iso_str, tz_name)
        if result:
            date_part, time_part = result
            try:
                d = datetime.strptime(date_part, "%Y-%m-%d")
                return d.strftime("%m/%d/%Y"), time_part
            except Exception:
                return date_part, time_part
        return "", ""

    start_date, start_time = _localize(start_raw)
    end_date, end_time = _localize(end_raw)

    # Build image URL from Wix media
    image_url = ""
    main_image = wix_event.get("mainImage") or {}
    if main_image.get("url"):
        image_url = main_image["url"]

    ticket_names = []
    ticket_prices = []
    ticket_capacities = []
    fee_type = DEFAULT_FEE_TYPE
    sale_start = ""
    sale_end = ""
    for td in ticket_defs:
        ticket_names.append(td.get("name", "Ticket"))
        pricing = td.get("pricingMethod", {})
        fixed = pricing.get("fixedPrice", {})
        ticket_prices.append(fixed.get("value", "0"))
        cap = td.get("initialLimit") or td.get("actualLimit") or ""
        ticket_capacities.append(str(cap) if cap else "")
        fee_type = td.get("feeType", fee_type)
        sp = td.get("salePeriod") or {}
        if sp.get("startDate"):
            sale_start = sp["startDate"]
        if sp.get("endDate"):
            sale_end = sp["endDate"]

    return {
        "event_name": wix_event.get("title", ""),
        "categories": categories_str,
        "start_date": start_date,
        "start_time": start_time,
        "end_date": end_date,
        "end_time": end_time,
        "location": address,
        "registration_type": reg_type,
        "ticket_limit_per_order": str(ticket_limit) if ticket_limit else "",
        "checkout_form": checkout_form,
        "short_description": wix_event.get("shortDescription", ""),
        "detailed_description": wix_event.get("detailedDescription", ""),
        "image_url": image_url,
        "ticket_name": "; ".join(ticket_names),
        "ticket_price": "; ".join(ticket_prices),
        "ticket_capacity": "; ".join(ticket_capacities),
        "fee_type": fee_type,
        "sale_start": sale_start,
        "sale_end": sale_end,
        "tax_name": tax.get("name", DEFAULT_TAX_NAME),
        "tax_rate": tax.get("rate", DEFAULT_TAX_RATE),
        "tax_type": tax.get("type", DEFAULT_TAX_TYPE),
    }


# ---------------------------------------------------------------------------
# Ticket policy status (read-only drift indicator for the Notion column)
# ---------------------------------------------------------------------------


def ticket_policy_status(
    ticket_defs: List[Dict[str, Any]], desired_policy: Optional[str]
) -> str:
    """Human-readable policy state of an event's live ticket definitions.

    The single owner of the wording written to the read-only
    ``Ticket Policy Status`` column. Blank when the Settings policy is blank
    (feature off) or the event has no tickets; ``OK (n tickets)`` when every
    definition's ``policyText`` matches; otherwise a drift note like
    ``2 of 3 tickets missing policy``.
    """
    desired = (desired_policy or "").strip()
    if not desired or not ticket_defs:
        return ""

    total = len(ticket_defs)
    plural = "s" if total != 1 else ""
    mismatched = [
        td for td in ticket_defs
        if (td.get("policyText") or "").strip() != desired
    ]
    if not mismatched:
        return f"OK ({total} ticket{plural})"

    missing = sum(
        1 for td in mismatched if not (td.get("policyText") or "").strip()
    )
    if missing == len(mismatched):
        kind = "missing"
    elif missing == 0:
        kind = "different"
    else:
        kind = "missing/different"
    return f"{len(mismatched)} of {total} ticket{plural} {kind} policy"


# ---------------------------------------------------------------------------
# Site-config (tax-by-location) row builders
# ---------------------------------------------------------------------------


def _tax_region_label(region: Dict[str, Any]) -> str:
    """Human-friendly label for a tax region, e.g. ``CA / ON`` or ``US``."""
    country = (region.get("country") or "").strip()
    subdivision = (region.get("subdivision") or "").strip()
    if country and subdivision:
        return f"{country} / {subdivision}"
    return country or subdivision


def rates_equal(a: Any, b: Any) -> bool:
    """Compare two tax rates numerically, tolerating scale differences."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def tax_mapping_to_site_row(
    mapping: Dict[str, Any],
    regions_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a site_config row from an existing manual tax mapping."""
    region = regions_by_id.get(mapping.get("taxRegionId", ""), {})
    region_label = _tax_region_label(region)
    return {
        "setting_type": TAX_LOCATION_SETTING,
        "jurisdiction": (mapping.get("jurisdiction") or "") or region_label,
        "region": region_label,
        "tax_name": mapping.get("taxName", "") or "",
        "tax_type": mapping.get("taxType", "") or "",
        "tax_rate": tax_rate_decimal_to_percent(mapping.get("taxRate", "")),
        "region_id": mapping.get("taxRegionId", "") or "",
        "group_id": mapping.get("taxGroupId", "") or "",
        "mapping_id": mapping.get("id", "") or "",
        "revision": str(mapping.get("revision", "") or ""),
    }


def blank_region_site_row(
    region: Dict[str, Any],
    group_id: str,
) -> Dict[str, Any]:
    """Build a site_config row for a region that has no tax mapping yet.

    ``tax_rate`` is left blank so the operator can fill it in; pushing a blank
    rate is a no-op, while filling in (e.g.) ``13`` makes push create the
    mapping at that rate.
    """
    region_label = _tax_region_label(region)
    return {
        "setting_type": TAX_LOCATION_SETTING,
        "jurisdiction": region_label,
        "region": region_label,
        "tax_name": DEFAULT_TAX_NAME,
        "tax_type": "",
        "tax_rate": "",
        "region_id": region.get("id", "") or "",
        "group_id": group_id,
        "mapping_id": "",
        "revision": "",
    }


def select_default_tax_group_id(groups: List[Dict[str, Any]]) -> str:
    """Pick a sensible default tax group id for regions with no mapping.

    Prefers a group named ``standard``/``default`` (case-insensitive), otherwise
    falls back to the first group. Returns ``""`` if there are no groups.
    """
    if not groups:
        return ""
    for preferred in ("standard", "default"):
        for group in groups:
            if (group.get("name") or "").strip().lower() == preferred:
                return group.get("id", "") or ""
    return groups[0].get("id", "") or ""


def site_config_row_sort_key(row: Dict[str, Any]) -> Tuple[str, int]:
    """Sort site_config rows by region label, mapped rows before blank ones."""
    return (row.get("region", "") or "", 0 if row.get("mapping_id") else 1)
