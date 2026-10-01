import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.telemetry import TelemetryEvent
from app.services.alert_consumer import AlertConsumer


class ConsumerReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_processed_and_reseeded_events_do_not_wait_for_mutation_lock(self):
        event = TelemetryEvent(
            vin="SIM00000000000001",
            ts=datetime.now(UTC),
            lat=0,
            lon=0,
            speed_kmh=0,
            soc_pct=50,
            odo_km=0,
            seq=1,
        )
        for stored in ({"operations_processed": True}, None):
            consumer = AlertConsumer()
            consumer._process_unprocessed = AsyncMock()
            db = SimpleNamespace(
                telemetry=SimpleNamespace(find_one=AsyncMock(return_value=stored))
            )
            with patch("app.services.alert_consumer.get_database", return_value=db):
                await consumer._process(
                    SimpleNamespace(value=event.model_dump(mode="json"))
                )
            consumer._process_unprocessed.assert_not_awaited()
