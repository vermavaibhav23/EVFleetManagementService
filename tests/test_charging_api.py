import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi import Response
from pymongo.errors import DuplicateKeyError

from app.api.v1.charging import create_plan
from app.models.charging import ChargingPlan


class FakePlansCollection:
    def __init__(self, plan: dict) -> None:
        self.plan = plan

    async def find_one(self, query: dict) -> dict:
        return {**self.plan, "_id": "mongo-id"}


class FakeDatabase:
    def __init__(self, plan: dict) -> None:
        self.charging_plans = FakePlansCollection(plan)


class RacingPlansCollection:
    def __init__(self, plan: dict) -> None:
        self.plan = plan
        self.lookups = 0

    async def find_one(self, query: dict, sort: list | None = None) -> dict | None:
        self.lookups += 1
        if self.lookups == 1:
            return None
        return {**self.plan, "_id": "mongo-id"}

    async def insert_one(self, document: dict) -> None:
        raise DuplicateKeyError("duplicate active vehicle plan")


class RacingDatabase:
    def __init__(self, plan: dict) -> None:
        self.charging_plans = RacingPlansCollection(plan)


class ChargingPlanApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        now = datetime.now(UTC)
        self.plan = {
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
            "active": True,
            "reason": "Existing lowest-cost plan",
            "alternatives": [],
            "created_at": now,
        }

    async def test_create_plan_returns_existing_active_plan(self) -> None:
        response = Response()

        with patch(
            "app.api.v1.charging.get_database", return_value=FakeDatabase(self.plan)
        ):
            result = await create_plan(self.plan["vin"], response)

        self.assertEqual(200, response.status_code)
        self.assertEqual("PLAN-1", result.plan_id)
        self.assertEqual("PROPOSED", result.status.value)

    async def test_create_plan_returns_race_winner(self) -> None:
        response = Response()
        recommendation = type(
            "Recommendation",
            (),
            {"plan": ChargingPlan(**self.plan), "reason": "ready"},
        )()

        with (
            patch(
                "app.api.v1.charging.get_database",
                return_value=RacingDatabase(self.plan),
            ),
            patch(
                "app.api.v1.charging._load_recommendation",
                return_value=recommendation,
            ),
        ):
            result = await create_plan(self.plan["vin"], response)

        self.assertEqual(200, response.status_code)
        self.assertEqual("PLAN-1", result.plan_id)


if __name__ == "__main__":
    unittest.main()
