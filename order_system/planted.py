"""The planted orders: hand-chosen, on known dates.

Each one is a case the pipeline must handle. Their items are chosen by hand,
so every total can be checked with a pencil. All times are local Arden time.

A step is (day, hour, minute, second, what, visible), where `visible` is
None when the API shows the change at once, or the local time it shows it.
"""

from datetime import date


def D(day: int) -> date:
    return date(2026, 6, day)


PLANTED = [
    {
        "case": "P1",
        "name": "Status changes on a later day",
        "city": "NGT",
        "customer_id": 1207,
        "items": [("P-001", 2), ("P-002", 1), ("P-003", 3), ("P-004", 1)],
        "steps": [
            (D(1), 21, 40, 0, "placed", None),
            (D(2), 7, 40, 0, "packed", None),
            (D(2), 8, 20, 0, "out_for_delivery", None),
            (D(2), 9, 15, 0, "delivered", None),
        ],
    },
    {
        "case": "P2",
        "name": "Cancelled the day after it was placed",
        "city": "RVB",
        "customer_id": 1088,
        "items": [("P-005", 1), ("P-006", 2), ("P-007", 1)],
        "steps": [
            (D(3), 20, 15, 0, "placed", None),
            (D(3), 20, 50, 0, "packed", None),
            (D(4), 8, 30, 0, "cancelled", None),
        ],
    },
    {
        "case": "P3",
        "name": "Total drops because an item is out of stock",
        "city": "LKS",
        "customer_id": 1342,
        "items": [("P-008", 2), ("P-009", 1), ("P-010", 1), ("P-011", 4)],
        "steps": [
            (D(4), 22, 30, 0, "placed", None),
            (D(5), 8, 10, 0, "packed:out_of_stock:P-010", None),
            (D(5), 8, 50, 0, "out_for_delivery", None),
            (D(5), 9, 40, 0, "delivered", None),
        ],
    },
    {
        "case": "P4",
        "name": "Refund three days after delivery",
        "city": "NGT",
        "customer_id": 1153,
        "items": [("P-012", 2), ("P-013", 1), ("P-014", 1), ("P-015", 1)],
        "steps": [
            (D(5), 10, 20, 0, "placed", None),
            (D(5), 10, 50, 0, "packed", None),
            (D(5), 14, 55, 0, "out_for_delivery", None),
            (D(5), 15, 45, 0, "delivered", None),
            (D(8), 11, 0, 0, "refund:578", None),
        ],
    },
    {
        "case": "P5",
        "name": "Placed just before midnight",
        "city": "RVB",
        "customer_id": 1021,
        "items": [("P-016", 1), ("P-017", 2), ("P-018", 1)],
        "steps": [
            (D(6), 23, 58, 12, "placed", None),
            (D(7), 7, 45, 0, "packed", None),
            (D(7), 8, 30, 0, "out_for_delivery", None),
            (D(7), 9, 20, 5, "delivered", None),
        ],
    },
    {
        "case": "P6",
        "name": "Update that reaches the API late",
        "city": "LKS",
        "customer_id": 1276,
        "items": [("P-019", 1), ("P-020", 2), ("P-021", 1)],
        "steps": [
            (D(8), 20, 10, 0, "placed", None),
            (D(8), 20, 35, 0, "packed", None),
            (D(8), 21, 0, 0, "out_for_delivery", None),
            # Refused at the door. The driver's phone was offline until the next morning.
            (D(8), 21, 35, 0, "cancelled", (D(9), 10, 15, 0)),
        ],
    },
    {
        "case": "P8",
        "name": "Deleted from the source after it was loaded",
        "city": "NGT",
        "customer_id": 9999,  # a test account
        "items": [("P-018", 1), ("P-002", 1)],
        "steps": [
            (D(12), 14, 0, 0, "placed", None),
            (D(13), 11, 0, 0, "deleted", None),
        ],
    },
]

# P7 is not an order. It is a fault: one page served twice in the run of 2026-06-11.
# The API learns to do that in milestone 2, when the pipeline first keeps what it loaded.
