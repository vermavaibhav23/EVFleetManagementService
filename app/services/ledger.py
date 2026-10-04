"""Transactional PostgreSQL authority, retaining the pure planning snapshot contract."""

import copy
from datetime import UTC, datetime

from fastapi import HTTPException

from app.core.database import dumps

TABLES = {
    "vehicles": "vehicles",
    "plans": "journey_plans",
    "jobs": "planning_jobs",
    "stations": "stations",
    "depots": "depots",
}


class Ledger:
    def __init__(self, db):
        self.pool = db

    async def read_on(self, connection, lock=False):
        row = await connection.fetchrow(
            "SELECT * FROM fleet_run WHERE singleton" + (" FOR UPDATE" if lock else "")
        )
        if row is None:
            raise HTTPException(409, "Choose a scenario and select Load / reset first")
        doc = copy.deepcopy(row["data"])
        doc.update(run_id=row["run_id"], revision=row["revision"])
        for key, table in TABLES.items():
            order = "data->>'created_at', id" if key == "jobs" else "id"
            doc[key] = {
                r["id"]: r["data"]
                for r in await connection.fetch(
                    f"SELECT id, data FROM {table} WHERE run_id=$1 ORDER BY {order}",
                    row["run_id"],
                )
            }
        return doc

    async def read(self):
        async with (
            self.pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read"),
        ):
            return await self.read_on(connection)

    async def replace_run(self, document):
        document = copy.deepcopy(document)
        document.update(schema_version=3, revision=1)
        document.pop("_id", None)
        for vehicle in document["vehicles"].values():
            vehicle.update(control_version=0, sequence=0)
        self.check_size(document)
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(72849102)")
            await connection.execute("DELETE FROM fleet_run WHERE singleton")
            await connection.execute(
                "INSERT INTO fleet_run(singleton,run_id,revision,data) VALUES(true,$1,0,'{}')",
                document["run_id"],
            )
            await self.write_on(connection, None, document)
            from app.services.simulator import initialize_simulator

            await initialize_simulator(connection, document)
        return document

    @staticmethod
    def check_size(doc):
        if len(dumps(doc).encode()) > 12 * 1024 * 1024:
            raise HTTPException(
                413, "Simulation history limit reached; load a fresh run"
            )

    async def write_on(self, connection, previous, updated, sync_simulator=True):
        """Caller holds the fleet row lock; rows, bookings and outbox commit together."""
        self.check_size(updated)
        if previous and sync_simulator:
            from app.services.simulator import sync_commands

            await sync_commands(connection, previous, updated)
        for key, table in TABLES.items():
            for ident in (previous or {}).get(key, {}).keys() - updated[key].keys():
                await connection.execute(f"DELETE FROM {table} WHERE id=$1", ident)
            rows = [
                (ident, updated["run_id"], value)
                for ident, value in updated[key].items()
                if (previous or {}).get(key, {}).get(ident) != value
            ]
            if rows:
                await connection.executemany(
                    f"INSERT INTO {table}(id,run_id,data) VALUES($1,$2,$3) "
                    "ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data",
                    rows,
                )
        for ident, job in updated["jobs"].items():
            if ident not in (previous or {}).get("jobs", {}):
                await connection.execute(
                    "INSERT INTO outbox(id,run_id,kind,message_key,payload) VALUES($1,$2,'PLANNING',$3,$4) ON CONFLICT DO NOTHING",
                    ident,
                    updated["run_id"],
                    job.get("vin") or "fleet",
                    {"job_id": ident, "run_id": updated["run_id"]},
                )
        await connection.execute(
            "DELETE FROM reservations WHERE run_id=$1", updated["run_id"]
        )
        rows = bookings(updated) + updated.get("external_bookings", [])
        if rows:
            from app.domain import dt

            await connection.executemany(
                "INSERT INTO reservations(id,run_id,vehicle_id,charger_id,port,starts_at,ends_at,data) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                [
                    (
                        r.get("stop_id", r["plan_id"]),
                        updated["run_id"],
                        r["vin"],
                        r["charger_id"],
                        r["port"],
                        dt(r["start"]),
                        dt(r["end"]),
                        r,
                    )
                    for r in rows
                ],
            )
        metadata = {k: v for k, v in updated.items() if k not in TABLES and k != "_id"}
        await connection.execute(
            "UPDATE fleet_run SET revision=$1,data=$2 WHERE singleton",
            updated["revision"],
            metadata,
        )

    async def commit(self, previous, updated):
        updated = copy.deepcopy(updated)
        updated["revision"] = previous["revision"] + 1
        updated["updated_at"] = datetime.now(UTC)
        async with self.pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                "SELECT run_id,revision FROM fleet_run WHERE singleton FOR UPDATE"
            )
            if (
                not row
                or row["run_id"] != previous["run_id"]
                or row["revision"] != previous["revision"]
            ):
                raise HTTPException(
                    409,
                    "Fleet changed; refresh and retry. No partial changes were committed.",
                )
            await self.write_on(connection, previous, updated)
        return updated

    async def mutate(self, action, run_id=None):
        # Actions are pure edits to a private document. Recheck on CAS contention,
        # including clock-lease renewals; never retry a failed business validation.
        for attempt in range(4):
            previous = await self.read()
            if run_id is not None and previous["run_id"] != run_id:
                raise HTTPException(409, "This action belongs to an old simulation run")
            updated = copy.deepcopy(previous)
            result = action(updated)
            try:
                await self.commit(previous, updated)
                return result
            except HTTPException as exc:
                if exc.status_code != 409 or attempt == 3:
                    raise


def bookings(doc, exclude_plan=None):
    result = []
    for plan in doc["plans"].values():
        if plan["plan_id"] == exclude_plan or plan["status"] not in {
            "APPROVED",
            "EXECUTING",
            "INTERRUPTED",
        }:
            continue
        for op in plan["operations"]:
            if op["kind"] == "CHARGE" and op["status"] in {
                "PLANNED",
                "ACTIVE",
                "RELEASING",
            }:
                result.append(
                    {
                        **op,
                        "plan_id": plan["plan_id"],
                        "vin": plan["vin"],
                        "run_id": plan["run_id"],
                    }
                )
    return result
