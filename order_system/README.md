# The order system

This folder is **the source**: Arden's order system. It is a small fake, written for this project. It is the outside world. No pipeline code lives here, and every milestone uses the same one.

## Start it

From the project folder:

```sh
uv run python -m order_system
```

Leave it running. Open http://127.0.0.1:8010/docs to try it in a browser.

It listens on port 8010. To use another port, set it in both terminals:

| System | Start the order system | Tell the other programs |
|---|---|---|
| macOS, Linux | `ARDEN_API_PORT=8020 uv run python -m order_system` | `export ARDEN_API_URL=http://127.0.0.1:8020` |
| Windows (PowerShell) | `$env:ARDEN_API_PORT=8020; uv run python -m order_system` | `$env:ARDEN_API_URL="http://127.0.0.1:8020"` |

## What it serves

| Call | Returns |
|---|---|
| `GET /orders?updated_since=&cursor=` | Up to 25 orders, sorted by `updated_at`, and a `next_cursor` |
| `GET /orders/{order_id}` | One order. 404 when it was deleted |
| `GET /admin/clock` | The time on the order system's clock |
| `POST /admin/clock` | Moves the clock |
| `POST /admin/reset` | Back to the first morning |

It allows 12 calls to `GET /orders` in 10 seconds. The next call is refused with status 429.

Later milestones add what they need: faults you can switch on (milestone 2), a change feed and a webhook (labs in milestones 2 and 3).

## The clock is yours

The order system has its own clock, and it only moves when you move it. So you can go to "the next morning" at once.

```sh
uv run python -m order_system.clock                        # show the time
uv run python -m order_system.clock --morning 2026-06-08   # 07:00 on that day
uv run python -m order_system.clock --advance-days 1       # the next day
uv run python -m order_system.clock --reset                # back to 2026-06-02
```

## The data

```mermaid
flowchart LR
    S["seed 20260601"] --> G["generate.py<br/>every change to every order"]
    P["planted.py<br/>7 hand-chosen orders"] --> G
    G --> L[("the event log<br/>1,730 events")]
    C["the clock"] --> A
    L --> A["app.py<br/>the latest state of each order<br/>that the clock can see"]
```

- 14 business days: Monday 2026-06-01 to Sunday 2026-06-14. 432 orders in three cities.
- Arden's time zone is `America/Chicago`. The API writes every time in UTC.
- Ordinary orders move to `delivered`, and their money never changes.
- **Seven planted orders** do something a pipeline must handle. Their items are chosen by hand, so each total can be checked with a pencil.

| Order | Case | What happens |
|---|---|---|
| 1024 | Status changes on a later day | Placed Mon 06-01 21:40, delivered Tue 09:15 |
| 1091 | Cancelled the next day | Placed Wed 06-03, cancelled Thu 08:30 |
| 1125 | An item is out of stock | Total drops from $36.94 to $32.35 on Fri 06-05 |
| 1130 | Refund three days after delivery | Delivered Fri 06-05, $5.78 refunded Mon 06-08 |
| 1196 | Placed just before midnight | Sat 06-06 23:58:12 in Arden. Sunday in UTC |
| 1244 | An update that arrives late | Cancelled Mon 06-08 21:35, shown by the API Tue 10:15 |
| 1356 | Deleted from the source | A test order. Placed Fri 06-12, deleted Sat 11:00 |

One more case is a fault, not an order: one page served twice in the run of 2026-06-11. It arrives in milestone 2.

The design is in [docs/source_data_proposal.md](../docs/source_data_proposal.md).

## Files

| File | Holds |
|---|---|
| `catalog.py` | The time zone, the cities and the products |
| `planted.py` | The planted orders |
| `generate.py` | Builds the event log from the seed |
| `app.py` | The API |
| `truth.py` | The answer key, worked out from the event log with no pipeline code |
| `clock.py` | Moves the clock from the command line |
| `business_page.py` | Writes the data of the business page |
| `tests/` | Tests for all of it: `uv run pytest` in this folder |
