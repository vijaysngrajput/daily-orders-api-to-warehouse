"""Settings. Everything a person might change is here."""

import os
from pathlib import Path

# Where the order system runs. Start it with:  uv run python -m order_system
API_URL = os.environ.get("ARDEN_API_URL", "http://127.0.0.1:8010")

# The warehouse: Postgres in Docker. Start it with:  docker compose up -d warehouse
PG_HOST = os.environ.get("ARDEN_PG_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("ARDEN_PG_PORT", "5440"))
PG_USER = "arden"
PG_PASSWORD = "arden"
DATABASE = "arden_m4"  # each milestone has its own database

# The clock in Arden's three cities. It decides which day an order belongs to.
BUSINESS_TZ = "America/Chicago"

# Late rows: each run re-reads this many days before the watermark.
# The latest update seen in the data arrived 12 hours and 40 minutes late.
LOOKBACK_DAYS = 2

# Retry: how often to try one page, and how long to wait after a server error.
MAX_TRIES = 5
BACKOFF_SECONDS = (1, 2, 4, 8)

MILESTONE_DIR = Path(__file__).resolve().parent.parent
DBT_PROJECT_DIR = MILESTONE_DIR / "dbt_project"  # every transformation, as SQL models
