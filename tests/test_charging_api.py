import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi import Response

from app.api.v1.charging import create_plan


class FakePlansCollection:
    def __init__(self, plan: dict) -> None:
        self.plan = plan

    async def find_one(self, query: dict) -> dict:
        return {**self.plan, "_id": "mongo-id"}


class FakeDatabase:
    def __init__(self, plan: dict) -> None:
        self.charging_plans = FakePlansCollection(plan)


class ChargingPlanApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_plan_returns_existing_active_plan(self) -> None:
        now = datetime.now(UTC)
        plan = {
            "plan_id": "PLAN-1",
            "vin": "SIM00000000000001",
            "trip_id": "TRIP-1",
            "charger_id": "CHARGER-1",
            "port_number": 1,
            "start_time": now,
            "end_time": now + timedelta(minutes=30),
            "starting_soc_pct": 20,
            "target_soc_pct": 60,
            "energy_required_kwh": 28,
            "allocated_power_kw": 60,
            "estimated_cost": 250,
            "predicted_ready_time": now + timedelta(minutes=30),
            "next_departure_time": now + timedelta(hours=1),
            "status": "PROPOSED",
            "reason": "Existing lowest-cost plan",
            "alternatives": [],
            "created_at": now,
        }
        response = Response()

        with patch(
            "app.api.v1.charging.get_database", return_value=FakeDatabase(plan)
        ):
            result = await create_plan(plan["vin"], response)

        self.assertEqual(200, response.status_code)
        self.assertEqual("PLAN-1", result.plan_id)
        self.assertEqual("PROPOSED", result.status.value)


if __name__ == "__main__":
    unittest.main()
