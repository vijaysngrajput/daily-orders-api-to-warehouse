"""Settings. Everything a person might change is here."""

import os
from pathlib import Path

# Where the order system runs. Start it with:
#   uv run python -m order_system
API_URL = os.environ.get("ARDEN_API_URL", "http://127.0.0.1:8010")

# The clock in Arden's three cities. It decides which day an order belongs to.
BUSINESS_TZ = "America/Chicago"

MILESTONE_DIR = Path(__file__).resolve().parent.parent
WAREHOUSE_PATH = MILESTONE_DIR / "data" / "warehouse" / "arden.duckdb"
