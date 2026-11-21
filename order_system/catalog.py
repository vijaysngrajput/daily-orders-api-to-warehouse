"""The fixed facts of Arden: its time zone, its cities and its products."""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

BUSINESS_TZ_NAME = "America/Chicago"
BUSINESS_TZ = ZoneInfo(BUSINESS_TZ_NAME)

FIRST_DAY = date(2026, 6, 1)  # a Monday
DAYS = 14  # business days that have orders: 2026-06-01 to 2026-06-14
RUN_HOUR = 7  # the daily pipeline runs at 07:00 local time

# code -> (name, share of the orders)
CITIES = {
    "NGT": ("Northgate", 0.40),
    "RVB": ("Riverbend", 0.35),
    "LKS": ("Lakeshore", 0.25),
}

# product_id -> (name, unit price in cents)
PRODUCTS = {
    "P-001": ("Oat milk 1L", 349),
    "P-002": ("Sourdough loaf", 525),
    "P-003": ("Avocado", 179),
    "P-004": ("Eggs, 12", 489),
    "P-005": ("Chicken breast 500g", 799),
    "P-006": ("Basmati rice 1kg", 429),
    "P-007": ("Greek yogurt 500g", 399),
    "P-008": ("Salmon fillet", 1195),
    "P-009": ("Baby spinach", 329),
    "P-010": ("Strawberries 400g", 459),
    "P-011": ("Sparkling water", 129),
    "P-012": ("Whole milk 2L", 289),
    "P-013": ("Cheddar 200g", 449),
    "P-014": ("Pasta 500g", 189),
    "P-015": ("Tomato sauce", 259),
    "P-016": ("Ice cream 500ml", 599),
    "P-017": ("Bananas, bunch", 149),
    "P-018": ("Coffee beans 250g", 899),
    "P-019": ("Olive oil 500ml", 949),
    "P-020": ("Tomatoes 500g", 249),
    "P-021": ("Mozzarella", 379),
    "P-022": ("Butter 250g", 419),
    "P-023": ("Orange juice 1L", 379),
    "P-024": ("Apples 1kg", 349),
    "P-025": ("Potatoes 2kg", 299),
    "P-026": ("Onions 1kg", 159),
    "P-027": ("Granola 500g", 549),
    "P-028": ("Dark chocolate 100g", 279),
    "P-029": ("Paper towels", 399),
    "P-030": ("Dish soap", 329),
}

STATUSES = ("placed", "packed", "out_for_delivery", "delivered", "cancelled")


def local(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    """A local Arden time, returned in UTC."""
    naive = datetime.combine(day, time(hour, minute, second))
    return naive.replace(tzinfo=BUSINESS_TZ).astimezone(timezone.utc)


def morning_of(day: date) -> datetime:
    """The moment of the daily run on `day`: 07:00 local, in UTC."""
    return local(day, RUN_HOUR, 0)


def to_text(moment: datetime | None) -> str | None:
    """A UTC timestamp as the API writes it: 2026-06-07T04:58:12Z."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_text(text: str) -> datetime:
    """Read a timestamp the API wrote. A value with no zone is taken as UTC."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def business_day(moment: datetime) -> date:
    """The business day of a moment: its calendar date in Arden's time zone."""
    return moment.astimezone(BUSINESS_TZ).date()
