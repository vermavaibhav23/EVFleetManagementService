"""Presentation metadata only. The planner never branches on scenario names."""

SCENARIOS = [
    (
        "NORMAL_DAY",
        "Normal Operations",
        "Normal Operations",
        "Enough energy for the full timetable.",
    ),
    (
        "CHARGER_CONGESTION",
        "Charger Scenarios",
        "Busy Chargers",
        "The cheaper station has a longer queue.",
    ),
    (
        "CHARGER_FAILURE",
        "Charger Scenarios",
        "Charger Offline",
        "A nearby station is unavailable.",
    ),
    (
        "NONFINAL_RELAXED",
        "Non-final Deliveries",
        "Time to Charge Ahead",
        "Time now can protect the later deliveries.",
    ),
    (
        "NONFINAL_TIGHT",
        "Non-final Deliveries",
        "Tight Next Deadline",
        "A short charge now; another stop later.",
    ),
    (
        "NONFINAL_PRIORITY",
        "Non-final Deliveries",
        "Priority Delivery",
        "Deliver now or charge and accept a delay.",
    ),
    (
        "NONFINAL_CONFLICT",
        "Non-final Deliveries",
        "Timetable Conflict",
        "The current deadline needs a manager decision.",
    ),
    (
        "FINAL_RELAXED",
        "Final Delivery",
        "Time to Top Up",
        "Top up while the final deadline allows it.",
    ),
    (
        "FINAL_TIGHT",
        "Final Delivery",
        "Deadline First",
        "Keep enough time for the final delivery.",
    ),
    (
        "FINAL_PRIORITY",
        "Final Delivery",
        "Priority Final Stop",
        "Review the low-reserve arrival before dispatch.",
    ),
]
