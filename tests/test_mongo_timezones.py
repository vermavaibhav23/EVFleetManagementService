from datetime import UTC, datetime

from bson import BSON
from bson.codec_options import CodecOptions

from app.domain import LoadRequest


def test_mongo_utc_round_trip_remains_timezone_aware():
    encoded = BSON.encode({"ts": datetime(2026, 10, 1, 2, 32, tzinfo=UTC)})
    decoded = BSON(encoded).decode(
        codec_options=CodecOptions(tz_aware=True, tzinfo=UTC)
    )
    request = LoadRequest(start_time=decoded["ts"])
    assert request.start_time.tzinfo is not None
    assert request.start_time.utcoffset() is not None
