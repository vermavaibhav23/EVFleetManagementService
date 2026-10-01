"""Release a disrupted booking and its unstarted dependent bookings."""


async def release_plan_chain(db, plan_id, now, reason):
    current = await db.charging_plans.find_one({"plan_id": plan_id})
    plan_id = (current or {}).get("parent_plan_id") or plan_id
    query = {
        "$or": [{"plan_id": plan_id}, {"plan_id": {"$regex": f"^{plan_id}-next-"}}],
        "status": {"$in": ["PROPOSED", "APPROVED", "CHARGING", "SCHEDULED"]},
    }
    affected = [p["plan_id"] async for p in db.charging_plans.find(query)]
    await db.charging_plans.update_many(
        query,
        {
            "$set": {
                "status": "CANCELLED",
                "active": False,
                "updated_at": now,
                "interruption_reason": reason,
            }
        },
    )
    await db.reservations.update_many(
        {
            "plan_id": {"$in": affected},
            "status": {"$in": ["CONFIRMED", "VEHICLE_EN_ROUTE", "OCCUPIED"]},
        },
        {"$set": {"status": "CANCELLED", "updated_at": now}},
    )
