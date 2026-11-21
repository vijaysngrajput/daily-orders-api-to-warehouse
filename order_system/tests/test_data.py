"""The generated data: the same every time, with the planted cases where the design says."""

from datetime import date, timedelta

from order_system.catalog import business_day, from_text, local, morning_of
from order_system.generate import build, orders_as_of
from order_system.truth import daily_report, day_totals

EVENTS, PLANTED = build()
END = morning_of(date(2026, 6, 18))


def totals(day_of_run: int) -> dict:
    return day_totals(daily_report(EVENTS, morning_of(date(2026, 6, day_of_run))))


def order(case: str, day: int, hour: int = 7, minute: int = 0) -> dict:
    return orders_as_of(EVENTS, local(date(2026, 6, day), hour, minute))[PLANTED[case]]


def test_same_seed_gives_same_log():
    again, planted_again = build()
    assert again == EVENTS and planted_again == PLANTED


def test_log_positions_are_in_order():
    assert [e.pos for e in EVENTS] == list(range(1, len(EVENTS) + 1))
    assert all(a.visible_at <= b.visible_at for a, b in zip(EVENTS, EVENTS[1:]))


def test_size_is_small_enough_to_hold_in_your_head():
    orders = orders_as_of(EVENTS, END)
    assert 380 <= len(orders) <= 480
    per_day = totals(18)
    assert len(per_day) == 14
    assert all(24 <= t["orders"] + t["cancelled_orders"] <= 38 for t in per_day.values())


def test_order_total_is_the_sum_of_its_items():
    for o in orders_as_of(EVENTS, END).values():
        expected = sum(
            i["quantity"] * i["unit_price_cents"] for i in o["items"] if i["item_status"] == "ok"
        )
        assert o["total_cents"] == expected


def test_planted_totals_match_the_hand_worked_sums():
    assert order("P1", 2)["total_cents"] == 2249
    assert order("P2", 4)["total_cents"] == 2056
    assert order("P3", 5)["total_cents"] == 3694
    assert order("P3", 6)["total_cents"] == 3235
    assert order("P4", 6)["total_cents"] == 1475
    assert order("P4", 9)["refunded_cents"] == 578
    assert order("P5", 7)["total_cents"] == 1796
    assert order("P6", 9)["total_cents"] == 1826
    assert order("P8", 13)["total_cents"] == 1424


def test_p1_status_changes_on_a_later_day():
    assert order("P1", 2)["status"] == "placed"
    assert order("P1", 3)["status"] == "delivered"


def test_p5_is_saturday_locally_and_sunday_in_utc():
    placed = from_text(order("P5", 7)["placed_at"])
    assert order("P5", 7)["placed_at"] == "2026-06-07T04:58:12Z"
    assert business_day(placed) == date(2026, 6, 6)
    assert placed.date() == date(2026, 6, 7)


def test_p6_is_shown_late_and_the_watermark_has_passed_it():
    assert order("P6", 9)["status"] == "out_for_delivery"  # the 06-09 run still sees it live
    late = order("P6", 10)
    assert late["status"] == "cancelled"
    assert late["updated_at"] == "2026-06-09T02:35:00Z"  # 06-08 21:35 local
    seen_on_the_9th = orders_as_of(EVENTS, morning_of(date(2026, 6, 9))).values()
    watermark = max(o["updated_at"] for o in seen_on_the_9th)
    assert watermark > late["updated_at"]  # so "changed since the watermark" misses it


def test_p8_is_gone_from_the_source():
    assert PLANTED["P8"] in orders_as_of(EVENTS, morning_of(date(2026, 6, 13)))
    assert PLANTED["P8"] not in orders_as_of(EVENTS, morning_of(date(2026, 6, 14)))


def test_answer_key_section_4_2():
    """Each earlier day changes on one known morning, by a known amount."""
    key = [  # (business day, run before, run after, d orders, d cancelled, d revenue, d refunded)
        ("2026-06-03", 4, 5, -1, +1, -2056, 0),
        ("2026-06-04", 5, 6, 0, 0, -459, 0),
        ("2026-06-05", 8, 9, 0, 0, -578, +578),
        ("2026-06-08", 9, 10, -1, +1, -1826, 0),
        ("2026-06-12", 13, 14, -1, 0, -1424, 0),
    ]
    for day, before, after, d_orders, d_cancelled, d_revenue, d_refunded in key:
        a, b = totals(before)[day], totals(after)[day]
        assert b["orders"] - a["orders"] == d_orders, day
        assert b["cancelled_orders"] - a["cancelled_orders"] == d_cancelled, day
        assert b["revenue_cents"] - a["revenue_cents"] == d_revenue, day
        assert b["refunded_cents"] - a["refunded_cents"] == d_refunded, day


def test_no_other_day_ever_changes():
    """After a day is first reported, only the planted cases can change it."""
    allowed = {
        ("2026-06-03", 5),
        ("2026-06-04", 6),
        ("2026-06-05", 9),
        ("2026-06-08", 10),
        ("2026-06-12", 14),
    }
    for run in range(3, 19):
        before, after = totals(run - 1), totals(run)
        for day, old in before.items():
            first_reported = date.fromisoformat(day) + timedelta(days=1)
            if date(2026, 6, run - 1) < first_reported:
                continue
            if after[day] != old:
                assert (day, run) in allowed, (day, run)


def test_the_numbers_the_manager_asks_for_are_frozen():
    """Milestone 1 asks on the morning of 2026-06-08. These numbers must never drift."""
    key = totals(8)
    assert (key["2026-06-07"]["orders"], key["2026-06-07"]["revenue_cents"]) == (30, 80711)
    assert (key["2026-06-06"]["orders"], key["2026-06-06"]["revenue_cents"]) == (33, 78671)
