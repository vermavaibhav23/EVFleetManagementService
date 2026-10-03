import asyncio
import json
from types import SimpleNamespace

from fastapi import HTTPException

from app.domain import LoadRequest
from app.services.ingestion import Ingestion
from app.services.seed import seed


def test_poison_old_run_and_duplicate_telemetry_commit_only_processed_offsets():
    async def run():
        doc = seed(LoadRequest(vehicle_count=1))
        v = doc["vehicles"]["SIM-001"]
        event = dict(
            run_id=doc["run_id"],
            vin=v["vin"],
            sequence=1,
            energy_kwh=v["energy_kwh"],
            temperature_c=30,
            health_fault=False,
        )
        values = [
            b"not json",
            json.dumps({**event, "run_id": "old"}).encode(),
            json.dumps(event).encode(),
            json.dumps(event).encode(),
        ]

        class Consumer:
            def __init__(self):
                self.commits = []

            def __aiter__(self):
                async def rows():
                    for offset, value in enumerate(values):
                        yield SimpleNamespace(
                            value=value, offset=offset, topic="telemetry", partition=2
                        )

                return rows()

            async def commit(self, offsets):
                self.commits.append(offsets)

        class Store:
            async def mutate(self, action, run_id):
                if run_id != doc["run_id"]:
                    raise HTTPException(409, "Old run")
                return action(doc)

        ingress = Ingestion(SimpleNamespace(fleet_ledger=None))
        ingress.store = Store()
        ingress.consumer = Consumer()
        await ingress.consume()
        assert v["sequence"] == 1
        assert [list(row.values())[0] for row in ingress.consumer.commits] == [
            1,
            2,
            3,
            4,
        ]
        assert all(list(row)[0].partition == 2 for row in ingress.consumer.commits)

    asyncio.run(run())
