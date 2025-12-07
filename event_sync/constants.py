"""Shared constants used across the event sync package."""

# Category-based pricing for generated events
CATEGORY_PRICING = {
    "Your First Rope Class": 30,
    "Beginner Rope For Play": 30,
    "Simple harnesses": 30,
    "Beginner self tying": 30,
    "Complex harnesses": 35,
    "Turn up the heat (intermediate play)": 35,
    "Suspension lines": 35,
    "Exploring Self Tying": 30,
    "Exploring Self Tying In the Air": 35,
    "The middle ground": 35,
    "The body in flight": 40,
    "Anatomy lab": 35,
    "Bottoming": 30,
    "Taking to the stage": 40,
    "Adding objects": 35,
    "Tips and Tricks": 30,
    "Mastering Play": 40,
}

# Default values for generated events
DEFAULT_LOCATION = "1233R Queen St W, Toronto, ON M6K 1L5, Canada"
DEFAULT_CAPACITY = 24
DEFAULT_REGISTRATION_TYPE = "TICKETS"
HST_MULTIPLIER = 1.13

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


