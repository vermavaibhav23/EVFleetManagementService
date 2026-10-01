import unittest
from datetime import timedelta

from app.services.scenarios import SCENARIOS
from tests import test_demo_journey as fixtures


class ExpandedScenariosTests(unittest.IsolatedAsyncioTestCase):
    async def test_removed_station_releases_plan_and_new_manager_choice_after_cancel(
        self,
    ):
        await self.seed("NONFINAL_PRIORITY")
        await self.choice("accept-delay")
        plan = await self.f.db.charging_plans.find_one(
            {"vin": self.f.vin, "status": "APPROVED"}
        )
        await self.f.db.chargers.delete_one({"charger_id": plan["charger_id"]})
        event = await self.tick()
        self.assertEqual("AWAITING_DECISION", event["operating_state"])
        self.assertTrue(
            any(s["stage"] == "interrupted" for s in event["journey_progress"]["steps"])
        )
        self.assertEqual(
            0,
            await self.f.db.reservations.count_documents(
                {
                    "vin": self.f.vin,
                    "status": {"$in": ["OCCUPIED", "CONFIRMED", "VEHICLE_EN_ROUTE"]},
                }
            ),
        )
        await self.choice("accept-delay")
        self.assertEqual(
            2, await self.f.db.manager_decisions.count_documents({"vin": self.f.vin})
        )

    async def asyncSetUp(self):
        self.f = fixtures.DemoJourneyTests()
        await self.f.asyncSetUp()

    async def asyncTearDown(self):
        await self.f.asyncTearDown()

    async def seed(self, sid, variant="offline"):
        r = await self.f.client.post(
            "/simulator/scenarios",
            json={"scenario": sid, "vehicle_count": 10, "variant": variant},
        )
        self.assertEqual(200, r.status_code, r.text)
        self.f.manager._simulated_time = await self.f.manager._load_states()

    async def tick(self):
        await self.f.manager._tick()
        await self.f.db.telemetry.delete_many(
            {"ts": {"$lt": self.f.manager._simulated_time - timedelta(minutes=1)}}
        )
        return await self.f.latest()

    async def recommendation(self):
        r = await self.f.client.get("/charging/recommendations/" + self.f.vin)
        self.assertEqual(200, r.status_code, r.text)
        return r.json()

    async def approve(self):
        p = await self.f.plan()
        r = await self.f.client.post("/charging/plans/" + p["plan_id"] + "/approve")
        self.assertEqual(200, r.status_code, r.text)
        return p

    async def choice(self, action):
        d = (
            await self.f.client.get("/charging/manager-decisions/" + self.f.vin)
        ).json()
        body = {
            k: d[k]
            for k in [
                "simulation_run_id",
                "trip_id",
                "telemetry_sequence",
                "decision_token",
            ]
        }
        r = await self.f.client.post(
            "/charging/manager-decisions/" + self.f.vin + "/" + action, json=body
        )
        self.assertEqual(200, r.status_code, r.text)
        return d

    async def test_catalog_busy_tradeoff_and_charge_ahead(self):
        self.assertEqual(15, len(SCENARIOS))
        self.assertEqual(4, len({x[1] for x in SCENARIOS}))
        for sid, station in [
            ("CHARGER_RELAXED", "SIM-CHARGER-CHEAP"),
            ("CHARGER_CONGESTION", "SIM-CHARGER-FAST"),
        ]:
            await self.seed(sid)
            p = (await self.recommendation())["plan"]
            self.assertEqual(station, p["charger_id"])
        await self.seed("NONFINAL_RELAXED")
        s = (await self.f.client.get("/fleet/manager")).json()["vehicles"][0]
        self.assertGreater(s["current_range_km"], s["delivery_remaining_km"] + 15)
        p = (await self.recommendation())["plan"]
        self.assertEqual(100, p["target_soc_pct"])
        self.assertEqual("AWAITING_DECISION", (await self.tick())["operating_state"])

    async def test_unavailable_variants_and_emergency(self):
        for variant, reason in [
            ("offline", "offline"),
            ("faulty", "faulty"),
            ("incompatible", "Incompatible connector"),
        ]:
            await self.seed("CHARGER_FAILURE", variant)
            d = await self.recommendation()
            self.assertNotEqual("SIM-CHARGER-CHEAP", d["plan"]["charger_id"])
            self.assertTrue(any(reason in x["reason"] for x in d["exclusions"]))
        await self.seed("CHARGER_FAILURE", "all_unavailable")
        self.assertIsNone((await self.recommendation())["plan"])
        await self.tick()
        v = (await self.f.client.get("/fleet/manager")).json()["vehicles"][0]
        self.assertEqual("EMERGENCY", v["manager_readiness"])
        self.assertEqual("AWAITING_DECISION", v["operating_state"])

    async def test_normal_transitions_to_charge_without_scenario_switch(self):
        await self.seed("NORMAL_LATER")
        self.assertIsNone((await self.recommendation())["plan"])
        seen = []
        for _ in range(100):
            e = await self.tick()
            seen.append(e["operating_state"])
            if e["operating_state"] == "AWAITING_DECISION":
                break
        self.assertIn("DRIVING", seen)
        self.assertIn("AT_CUSTOMER", seen)
        self.assertEqual("AWAITING_DECISION", seen[-1])
        self.assertIsNotNone((await self.recommendation())["plan"])
        self.assertTrue(e["journey_progress"]["previous_legs"][0]["completed"])

    async def test_fault_and_queue_interrupt_real_approved_plans(self):
        for sid, variant in [
            ("CHARGER_INTERRUPTION", "en_route"),
            ("CHARGER_INTERRUPTION", "while_charging"),
            ("QUEUE_OVERRUN", "offline"),
        ]:
            with self.subTest(sid=sid, variant=variant):
                await self.seed(sid, variant)
                p = await self.approve()
                for _ in range(100):
                    e = await self.tick()
                    if e["operating_state"] == "AWAITING_DECISION":
                        break
                doc = await self.f.db.charging_plans.find_one({"plan_id": p["plan_id"]})
                self.assertEqual("CANCELLED", doc["status"])
                self.assertTrue(
                    any(
                        s["stage"] == "interrupted"
                        for s in e["journey_progress"]["steps"]
                    )
                )
                self.assertEqual(
                    0,
                    await self.f.db.reservations.count_documents(
                        {
                            "vin": self.f.vin,
                            "status": {
                                "$in": ["CONFIRMED", "OCCUPIED", "VEHICLE_EN_ROUTE"]
                            },
                        }
                    ),
                )
                before = (await self.f.latest("SIM00000000000002"))["seq"]
                await self.tick()
                self.assertGreater(
                    (await self.f.latest("SIM00000000000002"))["seq"], before
                )

    async def test_both_manager_choices_including_reserve_intact(self):
        for action in ["deliver-now", "accept-delay"]:
            await self.seed("NONFINAL_CONTINUATION")
            await self.tick()
            d = await self.choice(action)
            self.assertTrue(d["deliver_now_available"])
            self.assertTrue(d["delay_available"])
            self.assertGreater(d["arrival_soc_pct"] / 100 * 28.8 / 0.3, 15)
            e = await self.tick()
            self.assertIn(e["operating_state"], ["DRIVING", "EN_ROUTE_TO_CHARGER"])
            if action == "accept-delay":
                self.assertIsNotNone(
                    await self.f.db.charging_plans.find_one(
                        {"vin": self.f.vin, "status": "APPROVED"}
                    )
                )

    async def test_review_does_not_invent_progress_and_live_approval_keeps_reviewed_slot(
        self,
    ):
        await self.seed("FINAL_RELAXED")
        await self.tick()
        p = await self.f.plan()
        await self.tick()
        r = await self.f.client.post("/charging/plans/" + p["plan_id"] + "/approve")
        self.assertEqual(200, r.status_code, r.text)
        e = await self.f.latest()
        self.assertNotIn(
            "charging", [x["stage"] for x in e["journey_progress"]["steps"]]
        )
        e = await self.tick()
        self.assertEqual("EN_ROUTE_TO_CHARGER", e["operating_state"])
