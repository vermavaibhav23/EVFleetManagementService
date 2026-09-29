import asyncio

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings


CHARGERS = [
    {
        "charger_id": "BLR-EV-001",
        "name": "Bangalore Central Fast Charger",
        "lat": 12.9716,
        "lon": 77.5946,
        "available_kw": 150,
        "price_per_kwh": 18.5,
        "status": "available",
    },
    {
        "charger_id": "CHN-EV-001",
        "name": "Chennai Fleet Depot",
        "lat": 13.0827,
        "lon": 80.2707,
        "available_kw": 90,
        "price_per_kwh": 16.0,
        "status": "available",
    },
]


async def main() -> None:
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[settings.mongodb_db]
    for charger in CHARGERS:
        await db.chargers.update_one(
            {"charger_id": charger["charger_id"]},
            {"$set": charger},
            upsert=True,
        )
    client.close()


if __name__ == "__main__":
    asyncio.run(main())

