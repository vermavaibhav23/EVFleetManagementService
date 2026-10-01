"""Apply scripted environmental changes, never scripted decisions."""

from datetime import timedelta


async def apply_events(db, now):
    async for event in db.simulation_events.find({"applied": False}):
        latest = await db.telemetry.find_one(
            {"vin": event["vin"], "simulation_run_id": event["simulation_run_id"]},
            sort=[("ts", -1), ("seq", -1)],
        )
        if not latest or latest.get("operating_state") != event["trigger_state"]:
            continue
        plan = await db.charging_plans.find_one(
            {"vin": event["vin"], "status": {"$in": ["APPROVED", "CHARGING"]}}
        )
        if not plan:
            continue
        charger_id = plan["charger_id"]
        if event["action"] == "fault":
            await db.chargers.update_one(
                {"charger_id": charger_id}, {"$set": {"status": "FAULTY"}}
            )
        elif event["action"] == "slow_charging":
            # Real occupied sessions charge slowly and occupy their ports longer.
            async for other in db.charging_plans.find(
                {
                    "charger_id": charger_id,
                    "status": "CHARGING",
                    "vin": {"$ne": event["vin"]},
                }
            ):
                end = max(other["end_time"], now) + timedelta(minutes=90)
                await db.charging_plans.update_one(
                    {"plan_id": other["plan_id"]},
                    {"$set": {"allocated_power_kw": 5, "end_time": end}},
                )
                await db.reservations.update_one(
                    {"plan_id": other["plan_id"]},
                    {"$set": {"reserved_power_kw": 5, "end_time": end}},
                )
        await db.simulation_events.update_one(
            {"event_id": event["event_id"]},
            {"$set": {"applied": True, "applied_at": now, "charger_id": charger_id}},
        )
