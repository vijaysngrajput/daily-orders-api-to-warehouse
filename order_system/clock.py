"""Move the order system's clock from the command line.

uv run python -m order_system.clock                      show the time
uv run python -m order_system.clock --morning 2026-06-08 07:00 on that day
uv run python -m order_system.clock --advance-days 1     the next day
uv run python -m order_system.clock --reset              back to the first morning
"""

import argparse
import os
import sys

import httpx

API_URL = os.environ.get("ARDEN_API_URL", "http://127.0.0.1:8010")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read or move the order system's clock.")
    parser.add_argument("--morning", help="a date, like 2026-06-08")
    parser.add_argument("--advance-days", type=int)
    parser.add_argument("--advance-minutes", type=int)
    parser.add_argument("--now", help="a UTC moment, like 2026-06-12T17:00:00Z")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    try:
        with httpx.Client(base_url=API_URL, timeout=10) as client:
            if args.reset:
                answer = client.post("/admin/reset")
            elif args.morning:
                answer = client.post("/admin/clock", json={"morning": args.morning})
            elif args.advance_days is not None:
                answer = client.post("/admin/clock", json={"advance_days": args.advance_days})
            elif args.advance_minutes is not None:
                answer = client.post("/admin/clock", json={"advance_minutes": args.advance_minutes})
            elif args.now:
                answer = client.post("/admin/clock", json={"now": args.now})
            else:
                answer = client.get("/admin/clock")
    except httpx.ConnectError:
        print(f"Cannot reach the order system at {API_URL}. Start it first:")
        print("  uv run python -m order_system")
        return 1
    answer.raise_for_status()
    print(
        f"The order system's clock: {answer.json()['now_local']} ({answer.json()['business_time_zone']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
