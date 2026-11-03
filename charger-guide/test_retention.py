import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cab_watch import city
from cab_watch.api import Point, explain, record


class Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_):
        return False


class RecordingConnection:
    def __init__(self, trip):
        self.trip = trip
        self.writes = []

    def transaction(self):
        return Context(self)

    async def fetchrow(self, *_):
        return self.trip

    async def fetch(self, *_):
        return [
            {"sequence": 1, "distance_m": 250.4},
            {"sequence": 2, "distance_m": 300.2},
        ]

    async def execute(self, sql, *args):
        self.writes.append((sql, args))
        return "INSERT 0 1"


class RecordingPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return Context(self.conn)


def run_immediate(coroutine):
    """Drive fake-DB coroutines, which must not wait on external I/O."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("The fake database unexpectedly yielded to an event loop")


class RetentionEvidenceTests(unittest.TestCase):
    def test_alert_snapshots_opening_readings(self):
        trip_id = uuid4()
        trip = {
            "last_sequence": 2, "off_count": 2, "phase": 0,
            "planned_legs": json.dumps([["A", "B"], ["B", "C"], ["C", "A"]]),
            "vehicle_id": None,
        }
        conn = RecordingConnection(trip)
        lat, lon = city.gps(city.NODES["Z"])
        run_immediate(record(RecordingPool(conn), Point(trip_id=trip_id, sequence=3, leg=0, lat=lat, lon=lon)))
        alert_write = next(args for sql, args in conn.writes if "INSERT INTO cab_watch.alerts" in sql)
        samples = json.loads(alert_write[3])
        self.assertEqual([sample["sequence"] for sample in samples], [1, 2, 3])
        self.assertGreater(samples[-1]["metres"], 200)

    def test_explanation_uses_alert_snapshot_without_gps_rows(self):
        trip_id = uuid4()
        samples = [{"sequence": 13, "metres": 256},
                   {"sequence": 14, "metres": 320},
                   {"sequence": 15, "metres": 385}]

        class SnapshotDb:
            async def fetchrow(self, *_):
                return {"trip_id": trip_id, "vehicle_index": 0,
                        "opened_sequence": 15, "closed_sequence": 30,
                        "peak_metres": 797, "opening_samples": json.dumps(samples)}

            async def fetch(self, *_):
                raise AssertionError("Old GPS rows should not be required")

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=SnapshotDb())))
        async def unavailable(*_):
            raise ValueError("unavailable")
        with patch("cab_watch.api.generate", new=unavailable):
            result = run_immediate(explain(uuid4(), 1, request))
        self.assertEqual(result["source"], "Rule-based explanation")
        self.assertEqual(result["facts"]["opening_samples"], samples)
        self.assertEqual(result["facts"]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
