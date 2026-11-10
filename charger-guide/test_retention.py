import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cab_watch import city
from cab_watch.api import Point, explain, record
from cab_watch.fleet import list_trips


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

    async def fetchrow(self, sql, *args):
        self.writes.append((sql, args))
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
    def test_alert_records_opening_sequence_and_peak(self):
        trip_id = uuid4()
        trip = {
            "last_sequence": 2, "motion": None, "completed_at": None,
            "planned_route": json.dumps(["A","B"]),
            "vehicle_id": None,
        }
        conn = RecordingConnection(trip)
        lat, lon = city.gps(city.NODES["Z"])
        run_immediate(record(conn, trip, Point(trip_id=trip_id, sequence=3, leg=0, lat=lat, lon=lon)))
        alert_write = next(args for sql, args in conn.writes if "INSERT INTO cab_watch_accounts.alerts" in sql)
        self.assertEqual(alert_write[:2], (trip_id, 3))
        self.assertGreater(alert_write[2], 200)

    def test_explanation_uses_alert_fields_without_gps_rows(self):
        trip_id = uuid4()

        class SnapshotDb:
            async def fetchrow(self, *_):
                return {"trip_id": trip_id, "vehicle_id":"KA01AB1234", "cab_number": 1,
                        "opened_sequence": 15, "closed_sequence": 30,
                        "peak_metres": 797}

            async def fetch(self, *_):
                raise AssertionError("Old GPS rows should not be required")

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=SnapshotDb())))
        async def unavailable(*_):
            raise ValueError("unavailable")
        with patch("cab_watch.api.generate", new=unavailable):
            result = run_immediate(explain(1, request, uuid4()))
        self.assertEqual(result["source"], "Rule-based explanation")
        self.assertEqual(result["facts"]["opened_sequence"], 15)
        self.assertEqual(result["facts"]["peak_m"], 797)
        self.assertEqual(result["facts"]["closed_sequence"], 30)
        self.assertEqual(result["facts"]["status"], "resolved")

    def test_trip_and_alert_reads_share_one_snapshot(self):
        trip_id = uuid4()

        class SnapshotConnection:
            def transaction(self, **options):
                self_options = options
                assert self_options == {"isolation": "repeatable_read", "readonly": True}
                return Context(self)

            async def fetch(self, sql, *_):
                if "FROM cab_watch_accounts.trips" in sql:
                    return [dict(id=trip_id,vehicle_id="KA01AB1234",cab_number=1,
                                 planned_route=json.dumps(["G","H"]),last_sequence=19,
                                 completed_at=None,phase=0,distance_m=0,motion=None,
                                 last_lat=None,last_lon=None,last_captured_at=None)]
                return []

        class Pool:
            def acquire(self):
                return Context(SnapshotConnection())

            async def fetch(self, *_):
                raise AssertionError("Separate pool queries could mix transaction states")

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=Pool())))
        snapshot = run_immediate(list_trips(request, uuid4()))
        self.assertEqual(snapshot["trips"][0]["route"], ["G","H"])
        self.assertEqual(snapshot["trips"][0]["last_sequence"], 19)


if __name__ == "__main__":
    unittest.main()
