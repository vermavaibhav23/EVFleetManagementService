import asyncio
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from agent import generate, tool
from . import city

ROOT = Path(__file__).parent
router = APIRouter(prefix="/cab-watch")


class Setup(BaseModel):
    count: int = Field(ge=1, le=len(city.ROUTES))


class TripRequest(BaseModel):
    pickup: str = Field(pattern="^[A-Z]$")
    dropoff: str = Field(pattern="^[A-Z]$")


class Point(BaseModel):
    trip_id: UUID
    sequence: int = Field(ge=1, le=2000)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Batch(BaseModel):
    readings: list[Point] = Field(min_length=1, max_length=len(city.COLORS))


async def record(db, reading):
    async with db.acquire() as conn, conn.transaction():
        trip = await conn.fetchrow("SELECT * FROM cab_watch.trips WHERE id=$1 FOR UPDATE", reading.trip_id)
        if not trip or reading.sequence <= trip["last_sequence"]:
            return
        line = ([city.NODES[n] for n in trip["planned_nodes"]] if trip["planned_nodes"]
                else city.ROUTES[trip["route_index"]][3])
        distance = city.distance_from_path(line, reading.lat, reading.lon)
        count = city.next_streak(distance, trip["off_count"], reading.sequence == trip["last_sequence"] + 1)
        await conn.execute("""INSERT INTO cab_watch.gps_readings(trip_id,sequence,lat,lon,distance_m)
            VALUES($1,$2,$3,$4,$5)""", reading.trip_id, reading.sequence, reading.lat, reading.lon, distance)
        await conn.execute("""UPDATE cab_watch.trips SET last_sequence=$2,off_count=$3,distance_m=$4
            WHERE id=$1""", reading.trip_id, reading.sequence, count, distance)
        if count >= city.REQUIRED:
            await conn.execute("""INSERT INTO cab_watch.alerts(trip_id,opened_sequence,peak_metres)
                VALUES($1,$2,$3) ON CONFLICT(trip_id) WHERE closed_sequence IS NULL
                DO UPDATE SET peak_metres=greatest(cab_watch.alerts.peak_metres,excluded.peak_metres)""",
                reading.trip_id, reading.sequence, distance)
        elif count == 0:
            await conn.execute("""UPDATE cab_watch.alerts SET closed_sequence=$2,resolved_at=now()
                WHERE trip_id=$1 AND closed_sequence IS NULL""", reading.trip_id, reading.sequence)


class CabStream:
    """Start this demo's independent Kafka consumer on its first simulation request."""
    def __init__(self, db, options):
        self.db, self.options = db, options
        self.lock = asyncio.Lock()
        self.worker = None

    async def start(self):
        async with self.lock:
            if self.worker and not self.worker.done():
                return
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
            consumer = AIOKafkaConsumer(city.TOPIC, **self.options, group_id="cab-watch.v1",
                                        auto_offset_reset="earliest", enable_auto_commit=False)
            try:
                await consumer.start()
            except BaseException:
                await consumer.stop()
                raise
            self.worker = asyncio.create_task(self.consume(consumer))

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
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.worker


@router.get("")
@router.get("/")
async def page():
    return FileResponse(ROOT / "index.html")


@router.get("/network")
async def network():
    return dict(nodes=city.NODES, roads=city.ROADS, capacity=len(city.COLORS),
                metres=city.METRES, origin=city.ORIGIN,
                lat_scale=city.LAT_SCALE, lon_scale=city.LON_SCALE)


@router.put("/sessions/{session}/trips/{trip_id}")
async def add_trip(session: UUID, trip_id: UUID, setup: TripRequest, request: Request):
    if setup.pickup == setup.dropoff:
        raise HTTPException(422, "Choose two different intersections")
    nodes, metres = city.shortest_route(setup.pickup, setup.dropoff)
    app = request.app.state
    await app.cab_stream.start()
    async with app.db.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", str(session))
        previous = await conn.fetchrow("SELECT * FROM cab_watch.trips WHERE id=$1", trip_id)
        if previous:
            if previous["session_id"] != session or previous["planned_nodes"] != nodes:
                raise HTTPException(409, "Trip ID already in use")
            index = previous["route_index"]
        else:
            index = await conn.fetchval("SELECT coalesce(max(route_index)+1,0) FROM cab_watch.trips WHERE session_id=$1", session)
            if index >= len(city.COLORS):
                raise HTTPException(409, "This fleet already has 20 trips")
            if index == 0:
                await conn.execute("DELETE FROM cab_watch.trips WHERE created_at < now()-interval '1 day'")
            await conn.execute("""INSERT INTO cab_watch.trips(id,session_id,route_index,planned_nodes)
                VALUES ($1,$2,$3,$4)""", trip_id, session, index, nodes)
    return dict(id=trip_id, cab=index+1, origin=setup.pickup, destination=setup.dropoff,
                color=city.COLORS[index], stops=nodes, path=[city.NODES[n] for n in nodes], metres=metres)


@router.post("/sessions", status_code=201)
async def create_session(setup: Setup, request: Request):
    app = request.app.state
    await app.cab_stream.start()
    session = uuid4()
    trips = []
    async with app.db.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM cab_watch.trips WHERE created_at < now()-interval '1 day'")
        for index, (origin, dest, color, path) in enumerate(city.ROUTES[:setup.count]):
            trip_id = uuid4()
            await conn.execute("INSERT INTO cab_watch.trips(id,session_id,route_index) VALUES($1,$2,$3)",
                               trip_id, session, index)
            trips.append(dict(id=trip_id, cab=index+1, origin=origin, destination=dest, color=color, path=path))
    return dict(id=session, trips=trips, xs=city.XS, ys=city.YS, metres=city.METRES,
                origin=city.ORIGIN, lat_scale=city.LAT_SCALE, lon_scale=city.LON_SCALE,
                limit=city.LIMIT, required=city.REQUIRED)


@router.post("/sessions/{session}/gps", status_code=202)
async def ingest(session: UUID, batch: Batch, request: Request):
    app = request.app.state
    if app.cab_stream.worker is None or app.cab_stream.worker.done():
        await app.cab_stream.start()
    ids = [r.trip_id for r in batch.readings]
    known = await app.db.fetchval("SELECT count(*) FROM cab_watch.trips WHERE session_id=$1 AND id=ANY($2::uuid[])",
                                  session, ids)
    if known != len(ids):
        raise HTTPException(422, "Send one reading per cab in this simulation")
    await asyncio.gather(*(app.producer.send_and_wait(city.TOPIC, r.model_dump_json().encode(),
                           key=str(r.trip_id).encode()) for r in batch.readings))
    return {"accepted":len(ids)}


@router.get("/sessions/{session}")
async def status(session: UUID, request: Request):
    db = request.app.state.db
    rows = await db.fetch("""SELECT t.id,t.last_sequence,t.off_count,t.distance_m,g.lat,g.lon
        FROM cab_watch.trips t LEFT JOIN cab_watch.gps_readings g
        ON (g.trip_id,g.sequence)=(t.id,t.last_sequence) WHERE t.session_id=$1 ORDER BY t.route_index""", session)
    if not rows:
        raise HTTPException(404, "Simulation expired. Create a new fleet.")
    alerts = await db.fetch("""SELECT a.*,t.route_index+1 AS cab FROM cab_watch.alerts a JOIN cab_watch.trips t
        ON t.id=a.trip_id WHERE t.session_id=$1 ORDER BY a.id DESC LIMIT 50""",session)
    return dict(trips=[dict(r) for r in rows], alerts=[dict(a) for a in alerts])


@router.post("/sessions/{session}/alerts/{alert_id}/explain")
async def explain(session: UUID, alert_id: int, request: Request):
    db = request.app.state.db
    alert = await db.fetchrow("""SELECT a.*,t.route_index FROM cab_watch.alerts a JOIN cab_watch.trips t
        ON t.id=a.trip_id WHERE t.session_id=$1 AND a.id=$2""",session,alert_id)
    if not alert:
        raise HTTPException(404, "Unknown alert for this simulation")
    samples = await db.fetch("""SELECT sequence,round(distance_m::numeric) AS metres
        FROM cab_watch.gps_readings WHERE trip_id=$1 AND sequence BETWEEN $2 AND $3 ORDER BY sequence""",
        alert["trip_id"], alert["opened_sequence"]-2, alert["opened_sequence"])
    facts = dict(cab=alert["route_index"]+1, threshold_m=city.LIMIT,
                 opening_samples=[dict(sequence=r["sequence"],metres=int(r["metres"])) for r in samples],
                 status="resolved" if alert["closed_sequence"] else "open", peak_m=round(alert["peak_metres"]))
    baseline = (f"Cab {facts['cab']} crossed the {city.LIMIT} m limit for three consecutive readings "
                f"({', '.join(str(r['metres'])+' m' for r in facts['opening_samples'])}). "
                + ("It returned to the route." if facts["status"]=="resolved" else "Check the trip with the driver."))
    declarations = [tool("read_alert_evidence", "Read the measured offsets and status for this simulated alert.")]
    contents = [{"role":"user","parts":[{"text":"Call read_alert_evidence then explain the result to a dispatcher "
                "in two short sentences. Use the evidence only. Never infer danger or a reason for the detour."}]}]
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

