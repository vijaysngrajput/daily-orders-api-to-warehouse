# Proposal: the order system, the planted cases, and the answer key

**Status:** approved by the owner on 2026-11-21, with every recommendation in section 6 accepted. Built in `order_system/`.

This was the first stop that `Project_context.md` asks for. It proposed three things:

1. The data model of the fake order API (the source).
2. The eight planted cases, each with its date.
3. The answer key, worked by hand.

Two details changed while building, and the code is the truth for both:

- The rate limit is 12 calls in 10 seconds. A full load passes until the morning of 06-10 and is refused on 06-11.
- The JSON example in section 2.2 shows invented ids. The real midnight order is 1196, with products `P-016`, `P-017` and `P-018`.

---

## 1. The calendar

| Thing | Proposal |
|---|---|
| Business days with orders | 14 days: Monday **2026-06-01** to Sunday **2026-06-14** |
| Business time zone | `America/Chicago`. In June it is 5 hours behind UTC, with no clock change in the period |
| Daily pipeline run | 07:00 local time (12:00 UTC). It loads for the manager's 08:00 report |
| Last run | The morning of 2026-06-18, so every late change has a run that can see it |
| Cities | Three fictional cities, all in the one time zone: Northgate (`NGT`), Riverbend (`RVB`), Lakeshore (`LKS`) |
| Volume | 24 to 36 orders a day across the three cities. About 420 orders in all |
| Money | Whole cents, US dollars |
| Seed | One fixed seed. Every learner gets the same orders |

A **business day** is the calendar date of `placed_at` in the business time zone. The time zone must be behind UTC. Only then is "23:58 local" already the next day in UTC.

## 2. The data model of the source

### 2.1 One event log is the truth

The generator writes one ordered list of **events**. Every change to every order is one event.

| Field | Meaning |
|---|---|
| `pos` | Position in the list: 1, 2, 3 … It gives the true order |
| `op` | `c` create, `u` update, `d` delete. The same letters as the CDC chapter |
| `order_id` | The order that changed |
| `event_time` | When the change happened. It becomes the order's `updated_at` |
| `visible_at` | When the API starts to show the change. Normally equal to `event_time` |
| `after` | The whole order after the change. Empty for a delete |

Everything the API serves is worked out from this list and the API's clock:

- `GET /orders` shows the **latest state** of each order, using only events with `visible_at` at or before the clock.
- The change feed (a lab) shows the events themselves.
- The webhook (a lab) sends each event when the clock passes it.

Two planted cases need nothing more than this. A late update is one event whose `visible_at` is later than its `event_time`. A deleted order is one `d` event, after which `GET /orders` has no row for it.

### 2.2 An order, as the API returns it

```json
{
  "order_id": 1187,
  "customer_id": 4032,
  "city": "RVB",
  "status": "delivered",
  "placed_at": "2026-06-07T04:58:12Z",
  "updated_at": "2026-06-07T14:20:05Z",
  "delivered_at": "2026-06-07T14:20:05Z",
  "cancelled_at": null,
  "total_cents": 1796,
  "refunded_cents": 0,
  "refunded_at": null,
  "items": [
    {"line_no": 1, "product_id": "P-031", "product_name": "Ice cream 500ml",
     "quantity": 1, "unit_price_cents": 599, "item_status": "ok"},
    {"line_no": 2, "product_id": "P-004", "product_name": "Bananas, bunch",
     "quantity": 2, "unit_price_cents": 149, "item_status": "ok"},
    {"line_no": 3, "product_id": "P-018", "product_name": "Coffee beans 250g",
     "quantity": 1, "unit_price_cents": 899, "item_status": "ok"}
  ]
}
```

| Field | Rule |
|---|---|
| `order_id` | Whole number from 1001, in the order the orders were placed. The key of an order |
| `status` | One of `placed`, `packed`, `out_for_delivery`, `delivered`, `cancelled` |
| `placed_at` | Event time. UTC. Never changes |
| `updated_at` | UTC. The API sets it on every change. This is the incremental key |
| `total_cents` | Sum of `quantity × unit_price_cents` over items with `item_status = "ok"` |
| `item_status` | `ok` or `out_of_stock`. An out-of-stock item stays in the list but adds nothing to the total |
| `refunded_cents` | 0 until a refund. A refund does not change `status` or `total_cents` |
| Item key | `order_id` + `line_no` |

All timestamps are UTC with a `Z`. The API never returns local time. The learner must convert.

### 2.3 The endpoints

| Endpoint | Used for |
|---|---|
| `GET /orders?updated_since=&cursor=&limit=` | The main read. Sorted by `updated_at`, then `order_id`. Returns `data` and `next_cursor`. The last page has `next_cursor: null` |
| `GET /orders/{order_id}` | One order. 404 when it was deleted |
| `GET /admin/clock`, `POST /admin/clock` | Read the clock, set it, or move it one day |
| `POST /admin/faults` | Make the next calls fail: a 500 error, a 429 rate limit, or a repeated page |
| `POST /admin/reset` | Back to the first morning |
| `GET /changes?after_pos=` | Lab only: the change feed, for the log-based CDC demonstration |
| `POST /webhooks` | Lab only: register an address that receives each change, for the push demonstration |

- **Pagination** uses a cursor, not an offset. The extraction chapter shows why offsets lose rows.
- **`updated_since`** returns rows with `updated_at` at or after the value, so a tie on the boundary is re-read and never lost.
- **Rate limit.** The API answers 429 with a `Retry-After` header. I will tune the limit so a full load passes in the first week and fails in the second, and an incremental load never fails. The exact numbers go in milestone 1's LLD, from a measurement.

### 2.4 Ordinary orders

Most orders are ordinary. Their rules:

- Placed between 07:00 and 23:30 local time.
- They move `placed` → `packed` → `out_for_delivery` → `delivered` in one to three hours.
- An order placed after 21:00 is delivered the next morning. So some status changes cross midnight every day. This is normal for Arden.
- **Their money never changes.** No ordinary order is cancelled, refunded, short of an item, or deleted.

The last rule is what makes the answer key possible. Every change to an earlier day's number comes from exactly one planted case.

## 3. The planted cases

Times are local. Each planted order has hand-chosen items, so its total can be checked by hand.

| # | Case | Order placed | What happens later | Total (cents) |
|---|---|---|---|---|
| P1 | Status changes on a later day | Mon 06-01 21:40, NGT | Delivered Tue 06-02 09:15. Money unchanged | 2,249 |
| P2 | Cancelled the next day | Wed 06-03 20:15, RVB | Cancelled Thu 06-04 08:30, after that morning's run | 2,056 |
| P3 | Total drops, item out of stock | Thu 06-04 22:30, LKS | Packed Fri 06-05 08:10. Strawberries are out of stock | 3,694 → 3,235 |
| P4 | Refund three days after delivery | Fri 06-05 10:20, NGT | Delivered 06-05 15:45. Refund of 578 on Mon 06-08 11:00 | 1,475, refund 578 |
| P5 | Just before midnight | Sat 06-06 23:58:12, RVB | `placed_at` is `2026-06-07T04:58:12Z`. Sunday in UTC | 1,796 |
| P6 | Update that arrives late | Mon 06-08 20:10, LKS | Refused at the door 06-08 21:35. The driver's phone was offline. The API shows it only at Tue 06-09 10:15 | 1,826 |
| P7 | One page served twice | The run of Thu 06-11 | The API serves page 2 twice in that run | Depends on the page |
| P8 | Deleted from the source | Fri 06-12 14:00, NGT | A test order. Loaded Sat 06-13 07:00. Deleted Sat 06-13 11:00 | 1,424 |

Days 06-13 and 06-14 have no planted order. They stay clean for milestone 5's backfill and its "bad morning" drill.

### The items of each planted order

| # | Items | Sum |
|---|---|---|
| P1 | 2 × Oat milk 1L @ 349 = 698 · 1 × Sourdough loaf @ 525 · 3 × Avocado @ 179 = 537 · 1 × Eggs, 12 @ 489 | 2,249 |
| P2 | 1 × Chicken breast 500g @ 799 · 2 × Basmati rice 1kg @ 429 = 858 · 1 × Greek yogurt 500g @ 399 | 2,056 |
| P3 | 2 × Salmon fillet @ 1,195 = 2,390 · 1 × Baby spinach @ 329 · 1 × Strawberries 400g @ 459 · 4 × Sparkling water @ 129 = 516 | 3,694. Without strawberries: 3,235 |
| P4 | 2 × Whole milk 2L @ 289 = 578 · 1 × Cheddar 200g @ 449 · 1 × Pasta 500g @ 189 · 1 × Tomato sauce @ 259 | 1,475. The milk is refunded: 578 |
| P5 | 1 × Ice cream 500ml @ 599 · 2 × Bananas, bunch @ 149 = 298 · 1 × Coffee beans 250g @ 899 | 1,796 |
| P6 | 1 × Olive oil 500ml @ 949 · 2 × Tomatoes 500g @ 249 = 498 · 1 × Mozzarella @ 379 | 1,826 |
| P8 | 1 × Coffee beans 250g @ 899 · 1 × Sourdough loaf @ 525 | 1,424 |

### Why P6 is missed

1. The run of 06-09 07:00 sees P6 as `out_for_delivery`. It saves a watermark near 06-09 07:00.
2. At 10:15 the API starts to show the cancellation. Its `updated_at` is 06-08 21:35. That is before the watermark.
3. The run of 06-10 asks for rows changed since the watermark. P6 is not one of them. No error shows.

A test will check that at least one other order has an `updated_at` between 21:35 and the 07:00 run. Without that, the watermark would not pass P6.

## 4. The answer key

### 4.1 The rules of the report

`daily_orders` has one row per business day, per city.

| Column | Rule |
|---|---|
| `order_date` | The business day: the date of `placed_at` in `America/Chicago` |
| `city` | The order's city |
| `orders` | Orders that are not cancelled and not deleted |
| `cancelled_orders` | Orders with status `cancelled` |
| `revenue_cents` | Sum of `total_cents − refunded_cents`, over the orders counted in `orders` |
| `refunded_cents` | Sum of `refunded_cents`, over the same orders |

### 4.2 What changes, and on which morning

This table is the hand-worked key. It follows from section 3 and the rules above, with no generator.

| Day | First reported | Changes on the run of | Change to that day | Because of |
|---|---|---|---|---|
| 06-01 | 06-02 | 06-03 | Nothing. One status moves | P1 |
| 06-03 | 06-04 | 06-05 | `orders` −1, `cancelled_orders` +1, `revenue_cents` −2,056 | P2 |
| 06-04 | 06-05 | 06-06 | `revenue_cents` −459 | P3 |
| 06-05 | 06-06 | 06-09 | `revenue_cents` −578, `refunded_cents` +578 | P4 |
| 06-08 | 06-09 | 06-10 | `orders` −1, `cancelled_orders` +1, `revenue_cents` −1,826 | P6 |
| 06-12 | 06-13 | 06-14 | `orders` −1, `revenue_cents` −1,424 | P8 |

Every other day keeps the number it was first reported with.

### 4.3 What each wrong pipeline gets wrong

| Mistake | Wrong result | Size of the error |
|---|---|---|
| Group by the UTC date | P5 moves from 06-06 to 06-07 | 1 order and 1,796 cents, for P5 alone |
| Append changed rows (milestone 2) | P1 is in the table twice | 06-01 counts 2,249 twice |
| Append, with P2 | 06-03 counts the order once as live and once as cancelled | +2,056 against the truth |
| Watermark with no lookback | P6 stays `out_for_delivery` for ever | 06-08 is 1 order and 1,826 too high |
| Trust `updated_at` for deletes | P8 stays in the warehouse | 06-12 is 1 order and 1,424 too high |
| Repeated page with append | Every order on that page is doubled | Known once the generator exists |

A note on the UTC mistake. It does not move P5 alone. Every order placed after 19:00 local time moves to the next UTC day. P5 is the clearest example, and the notebook will measure the full effect.

### 4.4 What the key cannot hold yet

The **absolute** totals of each day (for example "06-07 had 31 orders and 84,310 cents") depend on the generated ordinary orders. They do not exist until the generator does. I will not invent them.

My plan: the generator is the first code of milestone 1. It writes the full key to a file. A test then checks two things: the key matches the data, and every row of section 4.2 holds.

Milestone 1's notebook asks the manager's question on the morning of **Monday 06-08**. "Yesterday" is 06-07 and "the day before" is 06-06. P5 sits exactly between them, so the time zone trap changes both numbers the manager asked for.

## 5. What this leaves for the milestone documents

- The exact rate limit, page size and cursor format: milestone 1 LLD.
- The watermark table and the raw landing table: milestone 2 LLD.
- The lookback length, MERGE, and the soft-delete flag for P8: milestone 3 LLD.

## 6. Decisions I need from you

| # | Question | My recommendation |
|---|---|---|
| 1 | Is the calendar right: 14 days in June 2026, `America/Chicago`, three invented cities? | Yes |
| 2 | Is a refund a number (`refunded_cents`) and not a status? | Yes. It allows a partial refund, and the status list stays a clean lifecycle |
| 3 | Should ordinary orders never change in money? | Yes. It keeps the answer key small enough to hold in your head |
| 4 | Where does the fake API live? | One shared folder at the root, `order_system/`. It is the outside world, not a milestone. Each milestone still copies its own pipeline code forward |
| 5 | Which database does milestone 1 use, so it needs no Docker? | DuckDB in one local file, as Retail Profit Stack does. Postgres arrives in milestone 2. The tools list does not name DuckDB, so this needs your yes |
| 6 | Is there a products endpoint, as a small full-load example beside the incremental orders? | No. The product name is on the item. It keeps the scope to orders |
| 7 | Folder names | `m1_report_by_hand`, `m2_load_only_what_changed`, `m3_safe_to_run_twice`, `m4_models_and_tests`, `m5_on_a_schedule`. Package name `daily_orders` |

## 7. Where the project document and the knowledge base differ

`Project_context.md` says to use the chapters' own words. I read all eight chapters behind milestones 2 to 5. I have not yet read the four chapters that only milestone 1 uses; I will before its design documents.

| The project document says | The chapters say | My recommendation |
|---|---|---|
| Staging, core and mart layers | The transformation chapter says raw, silver and gold. The loading chapter says raw layer and curated layer, and uses "staging table" for a short-lived holding table | Use staging, core and mart, as dbt projects do. Give the silver and gold names once, in one sentence |
| Look-back window | "Lookback" | Write "lookback", and say "also called a look-back window" once |
| Late-arriving data | "Late row" in the text. "Late-arriving data" only in a subtitle | Teach "late row" on Arden's rows, and name "late-arriving data" as the general term |
| Data interval | "Logical date". "Data interval" is not in the chapters I read | Teach "logical date" first. Introduce "data interval" as Airflow's name, from the Airflow documentation |
| Catch-up | "Catchup", Airflow's own spelling | Write "catchup" |
| Table test, freshness, incremental model, natural key, dedupe key, replay | Not defined in the eight chapters I read | Define them in this project, in the chapters' style. I will check the four remaining chapters first |

The gallery card also still says "Planned". That is for the website, not for this repository.
