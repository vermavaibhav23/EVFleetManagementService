import asyncio
import json
from contextlib import suppress
from pathlib import Path
from uuid import UUID
from typing import Literal, Annotated

import httpx
from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from fastapi import Depends, APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from .agent import generate, tool
from . import city
from .retention import retention_settings, run_retention

ROOT = Path(__file__).parent
router = APIRouter(prefix="/cab-watch")


from .auth import owner, router as auth_router
from .fleet import list_depots, router as fleet_router

router.include_router(auth_router)
router.include_router(fleet_router)

Node = Annotated[str, Field(pattern="^[A-Z]$")]

class Motion(BaseModel):
    index: int = Field(ge=0, le=25)
    mode: Literal["route","turn requested","detour","returning","arrived"]
    source: Node | None = None
    target: Node | None = None
    recovery: list[Node] = Field(default_factory=list, max_length=26)


class Point(BaseModel):
    trip_id: UUID
    motion: Motion | None = None
    sequence: int = Field(ge=1, le=2**31-1)
    leg: int = Field(default=0, ge=0, le=2)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Batch(BaseModel):
    readings: list[Point] = Field(min_length=1, max_length=city.MAX_FLEET)


async def record(db, reading):
    async with db.acquire() as conn, conn.transaction():
        trip = await conn.fetchrow("SELECT * FROM cab_watch_accounts.trips WHERE id=$1 FOR UPDATE", reading.trip_id)
        if not trip or reading.sequence <= trip["last_sequence"] or trip["phase"] == 3:
            return
        legs = json.loads(trip["planned_legs"]) if isinstance(trip["planned_legs"], str) else trip["planned_legs"]
        if reading.leg < trip["phase"] or reading.leg > trip["phase"] + 1:
            return
        if reading.leg == trip["phase"] + 1:
            empty_start = trip["phase"] == 0 and trip["last_sequence"] == 0 and len(legs["to_pickup"]) == 1
            previous_end = city.NODES[legs[city.LEG_NAMES[trip["phase"]]][-1]]
            if not empty_start and city.distance_from_path([previous_end], reading.lat, reading.lon) >= 1:
                return
        line = [city.NODES[n] for n in legs[city.LEG_NAMES[reading.leg]]]
        distance = city.distance_from_path(line, reading.lat, reading.lon)
        await conn.execute("""INSERT INTO cab_watch_accounts.gps_readings(trip_id,sequence,lat,lon,distance_m,leg)
            VALUES($1,$2,$3,$4,$5,$6)""", reading.trip_id, reading.sequence, reading.lat,
            reading.lon, distance, reading.leg)
        await conn.execute("""UPDATE cab_watch_accounts.trips SET last_sequence=$2,motion=$3::jsonb,distance_m=$4,phase=$5
            WHERE id=$1""", reading.trip_id, reading.sequence, reading.motion.model_dump_json() if reading.motion else None, distance, reading.leg)
        if distance >= city.LIMIT:
            await conn.execute("""INSERT INTO cab_watch_accounts.alerts
                (trip_id,opened_sequence,peak_metres)
                VALUES($1,$2,$3) ON CONFLICT(trip_id) WHERE closed_sequence IS NULL
                DO UPDATE SET peak_metres=greatest(cab_watch_accounts.alerts.peak_metres,excluded.peak_metres)""",
                reading.trip_id, reading.sequence, distance)
        else:
            await conn.execute("""UPDATE cab_watch_accounts.alerts SET closed_sequence=$2,resolved_at=now()
                WHERE trip_id=$1 AND closed_sequence IS NULL""", reading.trip_id, reading.sequence)
        if trip["vehicle_id"] and reading.leg == 2:
            depot = city.NODES[legs["to_depot"][-1]]
            if city.distance_from_path([depot], reading.lat, reading.lon) < 1:
                await conn.execute("UPDATE cab_watch_accounts.trips SET phase=3,completed_at=now() WHERE id=$1", reading.trip_id)
                await conn.execute("""UPDATE cab_watch_accounts.vehicles SET status='available',parked_depot_id=(SELECT id FROM cab_watch_accounts.depots WHERE node=$2)
                    WHERE id=$1""", trip["vehicle_id"], legs["to_depot"][-1])


class CabStream:
    """Start this demo's independent Kafka consumer on its first simulation request."""
    def __init__(self, db, options):
        self.db, self.options = db, options
        self.lock = asyncio.Lock()
        self.worker = None
        self.retention_task = None

    async def start(self):
        async with self.lock:
            if self.worker and not self.worker.done():
                return
            retention = retention_settings()
            if self.worker:
                with suppress(Exception):
                    self.worker.result()
            async with self.db.acquire() as conn, conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(73949203)")
                await conn.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
            admin = AIOKafkaAdminClient(**self.options)
            try:
                await admin.start()
                if city.TOPIC not in await admin.list_topics():
                    try:
                        await admin.create_topics([NewTopic(city.TOPIC, 2, 1)])
                    except TopicAlreadyExistsError:
                        pass
            finally:
                await admin.close()
            consumer = AIOKafkaConsumer(city.TOPIC, **self.options, group_id="cab-watch.accounts.v1",
                                        auto_offset_reset="earliest", enable_auto_commit=False)
            try:
                await consumer.start()
            except BaseException:
                await consumer.stop()
                raise
            self.worker = asyncio.create_task(self.consume(consumer))
            if self.retention_task is None or self.retention_task.done():
                self.retention_task = asyncio.create_task(run_retention(self.db, *retention))

    async def consume(self, consumer):
        try:
            async for message in consumer:
                try:
                    reading = Point.model_validate_json(message.value)
                except ValidationError:
                    reading = None
                if reading and message.key == str(reading.trip_id).encode():
                    await record(self.db, reading)
                await consumer.commit({TopicPartition(city.TOPIC, message.partition): message.offset + 1})
        finally:
            await consumer.stop()

    async def stop(self):
        if self.retention_task:
            self.retention_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.retention_task
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.worker


@router.get("")
@router.get("/")
async def page():
    return FileResponse(ROOT / "index.html")


@router.get("/network")
async def network(request: Request):
    return dict(nodes=city.NODES, roads=city.ROADS, capacity=city.MAX_FLEET,
                depots=[dict(d) for d in await list_depots(request.app.state.db)],
                metres=city.METRES, origin=city.ORIGIN,
                lat_scale=city.LAT_SCALE, lon_scale=city.LON_SCALE)


@router.post("/me/gps", status_code=202)
async def ingest(batch: Batch, request: Request, user: UUID=Depends(owner)):
    app = request.app.state
    if app.cab_stream.worker is None or app.cab_stream.worker.done():
        await app.cab_stream.start()
    ids = [r.trip_id for r in batch.readings]
    known = await app.db.fetchval("""SELECT count(*) FROM cab_watch_accounts.trips t JOIN cab_watch_accounts.vehicles v ON v.id=t.vehicle_id
        WHERE v.user_id=$1 AND t.id=ANY($2::uuid[])""",user,ids)
    if known != len(ids):
        raise HTTPException(422, "Send one reading per cab in this simulation")
    await asyncio.gather(*(app.producer.send_and_wait(city.TOPIC, r.model_dump_json().encode(),
                           key=str(r.trip_id).encode()) for r in batch.readings))
    return {"accepted":len(ids)}


@router.post("/me/alerts/{alert_id}/explain")
async def explain(alert_id: int, request: Request,user: UUID=Depends(owner)):
    db = request.app.state.db
    alert = await db.fetchrow("""SELECT a.*,v.id AS vehicle_id,v.cab_number FROM cab_watch_accounts.alerts a
        JOIN cab_watch_accounts.trips t ON t.id=a.trip_id JOIN cab_watch_accounts.vehicles v ON v.id=t.vehicle_id
        WHERE v.user_id=$1 AND a.id=$2""",user,alert_id)
    if not alert:
        raise HTTPException(404, "Unknown alert for this simulation")
    facts = dict(vehicle_id=alert["vehicle_id"], cab=alert["cab_number"], threshold_m=city.LIMIT,
                 opened_sequence=alert["opened_sequence"], closed_sequence=alert["closed_sequence"],
                 status="resolved" if alert["closed_sequence"] is not None else "open",
                 peak_m=round(alert["peak_metres"]))
    baseline = (f"{facts['vehicle_id']} triggered the {city.LIMIT} m off-route alert at GPS "
                f"#{facts['opened_sequence']}. The maximum recorded offset was {facts['peak_m']} m.")
    if facts["closed_sequence"] is not None:
        baseline += f" The alert cleared at GPS #{facts['closed_sequence']}."
    declarations = [tool("read_alert_evidence", "Read the measured offsets and status for this simulated alert.")]
    contents = [{"role":"user","parts":[{"text":"Call read_alert_evidence then explain the result to a dispatcher "
                "in two short sentences. The peak is the maximum during the incident. Do not infer the opening distance. Never infer danger or a reason for the detour."}]}]
    try:
        turn = await generate(contents, declarations, "ANY")
        calls = [p["functionCall"] for p in turn["parts"] if "functionCall" in p]
        if len(calls)!=1 or calls[0]["name"]!="read_alert_evidence" or calls[0].get("args"):
            raise ValueError("Invalid tool call")
        reply = {"name":"read_alert_evidence","response":facts}
        if calls[0].get("id"):
            reply["id"] = calls[0]["id"]
        contents.extend([turn, {"role":"user","parts":[{"functionResponse":reply}]}])
        final = await generate(contents, declarations, "NONE")
        summary = " ".join(p["text"] for p in final["parts"] if p.get("text") and not p.get("thought")).strip()
        if summary:
            return dict(source="Gemini · read_alert_evidence",summary=summary[:800],facts=facts)
        reason = "Gemini returned an empty summary."
    except httpx.HTTPStatusError as exc:
        reason = f"Gemini returned HTTP {exc.response.status_code}."
    except httpx.RequestError:
        reason = "The model service could not be reached in time."
    except (KeyError, ValueError, IndexError, TypeError):
        reason = "The model or its evidence tool was unavailable."
    return dict(source="Rule-based explanation",summary=baseline,facts=facts,notice=reason)


