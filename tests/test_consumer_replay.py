import asyncio
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

    async def test_consumer_commits_once_after_whole_batch(self):
        consumer = AlertConsumer()
        committed = asyncio.Event()
        delivered = False

        async def getmany(**kwargs):
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"partition": [1, 2, 3]}
            await asyncio.Event().wait()

        async def commit():
            committed.set()

        consumer._consumer = SimpleNamespace(
            getmany=getmany, commit=AsyncMock(side_effect=commit)
        )
        consumer._process_message = AsyncMock()
        task = asyncio.create_task(consumer._consume())
        await asyncio.wait_for(committed.wait(), 2)
        self.assertEqual(3, consumer._process_message.await_count)
        self.assertEqual(3, consumer.processed)
        self.assertEqual(0, consumer._inflight)
        consumer._consumer.commit.assert_awaited_once()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
