"""Shared constants used across the event sync package."""

COLUMN_MAPPING = {
    "event_name": ["event_name", "event name", "name", "title"],
    "event_type": ["event_type", "event type", "type", "category"],
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
}

REQUIRED_FIELDS = ["event_name", "start_date", "start_time", "location"]

MAX_WIX_IMAGE_BYTES = 25 * 1024 * 1024


