"""Scenario labels and seed metadata only; runtime decisions use live conditions."""

SCENARIOS = [
    (
        "NORMAL_DAY",
        "Normal Operations",
        "Smooth Deliveries",
        "Enough energy for the supplied timetable.",
    ),
    (
        "NORMAL_LATER",
        "Normal Operations",
        "Charging Needed Later",
        "Complete initial stops, then charge for the next leg.",
    ),
    (
        "CHARGER_RELAXED",
        "Charger Scenarios",
        "Busy Chargers - Save Money",
        "Waiting at the cheaper station fits the timetable.",
    ),
    (
        "CHARGER_CONGESTION",
        "Charger Scenarios",
        "Busy Chargers - Protect Deadline",
        "The cheaper queue would miss the deadline.",
    ),
    (
        "CHARGER_FAILURE",
        "Charger Scenarios",
        "Charger Unavailable / Offline / Faulty / Incompatible Connector",
        "Exclude unsuitable stations; declare an emergency if none is reachable.",
    ),
    (
        "CHARGER_INTERRUPTION",
        "Charger Scenarios",
        "Charger Fails During Journey",
        "A station fault interrupts an approved charging journey.",
    ),
    (
        "QUEUE_OVERRUN",
        "Charger Scenarios",
        "Queue Takes Longer",
        "An occupied port runs beyond its booking.",
    ),
    (
        "NONFINAL_RELAXED",
        "Non-final Deliveries",
        "Charge Ahead for Later Stops",
        "Use available time now to protect tighter deadlines later.",
    ),
    (
        "NONFINAL_TIGHT",
        "Non-final Deliveries",
        "Tight Next Deadline",
        "A partial charge now and a planned charging stop later.",
    ),
    (
        "NONFINAL_PRIORITY",
        "Non-final Deliveries",
        "Priority Delivery - Reserve Exception",
        "Choose charging delay or direct delivery with recovery.",
    ),
    (
        "NONFINAL_CONTINUATION",
        "Non-final Deliveries",
        "Priority Delivery - No Safe Continuation",
        "The customer is reachable, but the vehicle cannot safely continue.",
    ),
    (
        "NONFINAL_CONFLICT",
        "Non-final Deliveries",
        "Timetable Conflict",
        "A later deadline makes the full timetable infeasible.",
    ),
    (
        "FINAL_RELAXED",
        "Final Delivery",
        "Time to Top Up",
        "Charge toward full within the final deadline.",
    ),
    (
        "FINAL_TIGHT",
        "Final Delivery",
        "Deadline First",
        "Take the maximum safe charge that fits.",
    ),
    (
        "FINAL_PRIORITY",
        "Final Delivery",
        "Priority Final Stop",
        "Choose charging delay or direct delivery with recovery.",
    ),
]
