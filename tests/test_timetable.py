import unittest
from datetime import UTC, datetime, timedelta

from app.models.simulator import SimulationScenario
from app.services.scenarios import SCENARIOS
from app.services.scheduler import haversine_km
from tests import test_demo_journey as fixtures


class TimetableTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixtures.DemoJourneyTests()
        await self.f.asyncSetUp()

    async def asyncTearDown(self):
        await self.f.asyncTearDown()

    async def seed(self, sid, count=10):
        await self.f.seed(SimulationScenario(sid), count)

    async def get(self, path):
        r = await self.f.client.get(path)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    async def approve(self):
        p = await self.f.plan()
        r = await self.f.client.post(f"/charging/plans/{p['plan_id']}/approve")
        self.assertEqual(200, r.status_code, r.text)
        return p

    async def decision(self, choice):
        d = await self.get(f"/charging/manager-decisions/{self.f.vin}")
        body = {k: d[k] for k in ("simulation_run_id", "trip_id", "telemetry_sequence")}
        r = await self.f.client.post(
            f"/charging/manager-decisions/{self.f.vin}/{choice}", json=body
        )
        self.assertEqual(200, r.status_code, r.text)
        return d

    async def finish(self, expected, vin=None, ticks=700):
        vin = vin or self.f.vin
        seen = set()
        for _ in range(ticks):
            await self.f.manager._tick()
            e = await self.f.latest(vin)
            seen.add(e["operating_state"])
            await self.f.db.telemetry.delete_many(
                {"ts": {"$lt": self.f.manager._simulated_time - timedelta(minutes=1)}}
            )
            completed = await self.f.db.trips.count_documents(
                {"vin": vin, "status": "COMPLETED"}
            )
            if completed >= expected:
                return seen
        trips = await self.f.db.trips.find(
            {"vin": vin}, {"_id": 0, "destination": 1, "status": 1}
        ).to_list(20)
        self.fail(
            f"{vin}: completed {completed}/{expected}; {seen}; {trips}; latest={e}"
        )

    async def test_catalog_and_real_queues(self):
        self.assertEqual(15, len(SCENARIOS))
        self.assertEqual(4, len({s[1] for s in SCENARIOS}))
        await self.seed("CHARGER_CONGESTION")
        s = await self.get("/fleet/manager")
        self.assertEqual(6, sum(c["occupied_ports"] for c in s["chargers"]))
        self.assertEqual(3, sum(c["waiting_count"] for c in s["chargers"]))
        self.assertEqual(
            6, sum(v["operating_state"] == "CHARGING" for v in s["vehicles"])
        )
        self.assertEqual(
            3, sum(v["operating_state"] == "WAITING_FOR_CHARGER" for v in s["vehicles"])
        )
        for c in s["chargers"]:
            others = [o for o in s["chargers"] if o != c]
            self.assertTrue(
                all(
                    haversine_km(c["lat"], c["lon"], o["lat"], o["lon"]) > 10
                    for o in others
                )
            )
        p = await self.approve()
        self.assertEqual("SIM-CHARGER-FAST", p["charger_id"])
        self.assertTrue(
            any(
                x["charger_id"] == "SIM-CHARGER-CHEAP" and "Queue" in x["reason"]
                for x in p["exclusions"]
            )
        )
        waiting = [
            v for v in s["vehicles"] if v["operating_state"] == "WAITING_FOR_CHARGER"
        ][0]
        await self.f.manager._tick()
        e = await self.f.latest(waiting["vin"])
        self.assertEqual(waiting["soc_pct"], e["soc_pct"])

    async def test_targets_and_full_timetables(self):
        for sid in [
            "FINAL_RELAXED",
            "FINAL_TIGHT",
            "NONFINAL_RELAXED",
            "NONFINAL_TIGHT",
            "CHARGER_FAILURE",
            "CHARGER_CONGESTION",
        ]:
            with self.subTest(sid=sid):
                await self.seed(sid, count=10)
                p = await self.approve()
                if "RELAXED" in sid:
                    self.assertEqual(100, p["target_soc_pct"])
                if "TIGHT" in sid:
                    self.assertLess(p["target_soc_pct"], 90)
                if sid.startswith("NONFINAL"):
                    self.assertGreaterEqual(p["covered_stops"], 2)
                    self.assertGreaterEqual(len(p["follow_up_stops"]), 1)
                if sid == "CHARGER_FAILURE":
                    self.assertNotEqual("SIM-CHARGER-CHEAP", p["charger_id"])
                total = await self.f.db.trips.count_documents({"vin": self.f.vin})
                seen = await self.finish(total)
                self.assertIn("CHARGING", seen)
                for t in await self.f.db.trips.find({"vin": self.f.vin}).to_list(20):
                    self.assertLessEqual(
                        t["completed_at"], t["delivery_deadline"], (sid, t)
                    )
                e = await self.f.latest()
                self.assertGreaterEqual(e["remaining_range_km"], 14.9)
                self.assertEqual(
                    0,
                    await self.f.db.reservations.count_documents(
                        {
                            "vin": self.f.vin,
                            "status": {"$in": ["OCCUPIED", "CONFIRMED"]},
                        }
                    ),
                )

    async def test_priority_reassignment_and_recovery(self):
        await self.seed("NONFINAL_PRIORITY")
        await self.f.manager._tick()
        self.assertEqual(
            "AWAITING_DECISION", (await self.f.latest())["operating_state"]
        )
        d = await self.decision("deliver-now")
        self.assertTrue(d["deliver_now_available"])
        receiver = d["replacement"]["vin"]
        self.assertEqual(1, await self.f.db.trips.count_documents({"vin": self.f.vin}))
        self.assertEqual(6, await self.f.db.trips.count_documents({"vin": receiver}))
        await self.finish(6, receiver)
        self.assertEqual(
            "RECOVERY_REQUIRED", (await self.f.latest())["operating_state"]
        )
        source = await self.f.db.trips.find_one({"vin": self.f.vin})
        pickup = await self.f.db.trips.find_one(
            {"vin": receiver, "handover_trip_id": source["trip_id"]}
        )
        self.assertGreater(pickup["completed_at"], source["completed_at"])
        self.assertGreater((await self.f.latest())["soc_pct"], 0)
        self.assertLess((await self.f.latest())["remaining_range_km"], 15)
        for t in await self.f.db.trips.find({"vin": receiver}).to_list(20):
            self.assertLessEqual(t["completed_at"], t["delivery_deadline"])

    async def test_final_priority_and_delay_choices(self):
        await self.seed("FINAL_PRIORITY")
        d = await self.decision("deliver-now")
        self.assertTrue(d["is_final"])
        self.assertIsNone(d["replacement"])
        await self.finish(1)
        await self.f.manager._tick()
        self.assertEqual(
            "RECOVERY_REQUIRED", (await self.f.latest())["operating_state"]
        )
        for sid in ["FINAL_PRIORITY", "NONFINAL_PRIORITY", "NONFINAL_CONFLICT"]:
            with self.subTest(sid=sid):
                await self.seed(sid)
                old = await self.f.db.trips.find_one(
                    {"vin": self.f.vin}, sort=[("departure_time", 1)]
                )
                d = await self.decision("accept-delay")
                self.assertTrue(d["delay_available"])
                if sid == "NONFINAL_CONFLICT":
                    self.assertFalse(d["deliver_now_available"])
                self.assertIsNotNone(
                    await self.f.db.charging_plans.find_one(
                        {"vin": self.f.vin, "status": "APPROVED"}
                    )
                )
                total = await self.f.db.trips.count_documents({"vin": self.f.vin})
                await self.finish(total)
                new = await self.f.db.trips.find_one({"trip_id": old["trip_id"]})
                self.assertEqual(old["delivery_deadline"], new["delivery_deadline"])
                if sid == "NONFINAL_CONFLICT":
                    legs = await self.f.db.trips.find({"vin": self.f.vin}).to_list(20)
                    self.assertTrue(
                        any(
                            leg["completed_at"] > leg["delivery_deadline"]
                            for leg in legs
                        )
                    )
                    self.assertLessEqual(new["completed_at"], new["delivery_deadline"])
                else:
                    self.assertGreater(new["completed_at"], new["delivery_deadline"])

    async def test_normal_and_current_clock(self):
        await self.seed("NORMAL_DAY")
        e = await self.f.latest()
        self.assertLess(abs((datetime.now(UTC) - e["ts"]).total_seconds()), 10)
        self.assertGreater(
            len(
                {
                    (v["lat"], v["lon"])
                    for v in (await self.get("/fleet/manager"))["vehicles"]
                }
            ),
            8,
        )
        data = await self.get(f"/charging/recommendations/{self.f.vin}")
        self.assertIsNone(data["plan"])
        await self.finish(4)

    async def test_large_speed_step_preserves_booking_transitions(self):
        await self.seed("CHARGER_CONGESTION")
        before = self.f.manager._simulated_time
        self.f.manager._tick_seconds = 60
        self.f.manager._time_scale = 3600
        await self.f.manager._tick()
        self.assertEqual(timedelta(minutes=1), self.f.manager._simulated_time - before)
        waiting = await self.f.latest("SIM00000000000008")
        self.assertEqual("WAITING_FOR_CHARGER", waiting["operating_state"])
        self.assertEqual(25, waiting["soc_pct"])

    async def test_decision_stale_and_no_replacement(self):
        await self.seed("NONFINAL_PRIORITY")
        d = await self.get(f"/charging/manager-decisions/{self.f.vin}")
        body = {k: d[k] for k in ("simulation_run_id", "trip_id", "telemetry_sequence")}
        await self.f.manager._tick()
        r = await self.f.client.post(
            f"/charging/manager-decisions/{self.f.vin}/deliver-now", json=body
        )
        self.assertEqual(409, r.status_code)
        await self.f.db.vehicles.update_one(
            {"vin": d["replacement"]["vin"]}, {"$set": {"active": False}}
        )
        d = await self.get(f"/charging/manager-decisions/{self.f.vin}")
        self.assertFalse(d["deliver_now_available"])


if __name__ == "__main__":
    unittest.main()
