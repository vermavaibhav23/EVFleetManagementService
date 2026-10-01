import unittest
from datetime import UTC, datetime, timedelta

from app.services.journey import advance_journey


class JourneyTrackerTests(unittest.TestCase):
    def test_observed_steps_only_and_interrupted_attempt_retained(self):
        now = datetime.now(UTC)
        p = advance_journey(
            None, run_id="r", trip_id="one", state="PARKED", phase="DELIVERY", now=now
        )
        p = advance_journey(
            p,
            run_id="r",
            trip_id="one",
            state="EN_ROUTE_TO_CHARGER",
            phase="TO_CHARGER",
            now=now,
            plan_id="a",
            decision="Charging approved",
        )
        self.assertEqual(
            ["parked", "decision", "to_charger"], [x.stage for x in p.steps]
        )
        p = advance_journey(
            p,
            run_id="r",
            trip_id="one",
            state="AWAITING_DECISION",
            phase="AWAITING_DECISION",
            now=now,
            interruption="Station failed",
        )
        self.assertEqual(
            ["parked", "decision", "to_charger", "interrupted", "decision"],
            [x.stage for x in p.steps],
        )
        self.assertNotIn("charging", [x.stage for x in p.steps])
        p = advance_journey(
            p,
            run_id="r",
            trip_id="one",
            state="CHARGING",
            phase="CHARGING",
            now=now,
            plan_id="b",
            decision="Charging approved",
        )
        self.assertEqual("a", p.steps[1].plan_id)
        self.assertEqual("b", p.steps[-1].plan_id)

    def test_new_leg_history_restart_and_reset(self):
        now = datetime.now(UTC)
        p = advance_journey(
            None,
            run_id="r",
            trip_id="one",
            state="AT_CUSTOMER",
            phase="ARRIVED",
            now=now,
        )
        p = advance_journey(
            p,
            run_id="r",
            trip_id="one",
            state="AT_CUSTOMER",
            phase="SERVICE",
            now=now + timedelta(minutes=1),
        )
        self.assertEqual(1, len(p.steps))
        p = advance_journey(
            p,
            run_id="r",
            trip_id="two",
            state="DRIVING",
            phase="DELIVERY",
            now=now + timedelta(minutes=5),
        )
        self.assertTrue(p.previous_legs[0].completed)
        self.assertEqual(["delivering"], [x.stage for x in p.steps])
        p = advance_journey(
            p, run_id="new", trip_id="one", state="PARKED", phase="DELIVERY", now=now
        )
        self.assertEqual([], p.previous_legs)
