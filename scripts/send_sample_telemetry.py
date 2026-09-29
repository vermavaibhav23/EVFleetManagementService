import asyncio
from datetime import UTC, datetime

import httpx


async def main() -> None:
    payload = {
        "vin": "1HGCM82633A004352",
        "ts": datetime.now(UTC).isoformat(),
        "lat": 12.9716,
        "lon": 77.5946,
        "speed_kmh": 42.5,
        "soc_pct": 18,
        "soh_pct": 94,
        "odo_km": 18234.7,
        "evt": "TELEMETRY",
        "seq": 1,
        "dtc": [],
    }
    async with httpx.AsyncClient() as client:
        response = await client.post("http://localhost:8000/api/v1/telemetry", json=payload)
        response.raise_for_status()
        print(response.json())


if __name__ == "__main__":
    asyncio.run(main())

