from datetime import UTC, datetime

from bson import BSON
from bson.codec_options import CodecOptions

from app.models.telemetry import TelemetryEvent


def test_mongo_utc_round_trip_remains_timezone_aware() -> None:
    encoded = BSON.encode({"ts": datetime(2026, 10, 1, 2, 32, tzinfo=UTC)})
    decoded = BSON(encoded).decode(
        codec_options=CodecOptions(tz_aware=True, tzinfo=UTC)
    )

    event = TelemetryEvent(
        vin="SIM00000000000001",
        ts=decoded["ts"],
        lat=12.9716,
        lon=77.5946,
        speed_kmh=0,
        soc_pct=20,
        odo_km=10000,
        seq=0,
    )

    assert event.ts.tzinfo is not None
    assert event.ts.utcoffset() is not None
