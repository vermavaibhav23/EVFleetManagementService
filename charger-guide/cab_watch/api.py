import asyncio
import json
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

from .agent import generate, tool
from . import city
from .retention import retention_settings, run_retention

ROOT = Path(__file__).parent
router = APIRouter(prefix="/cab-watch")


class Setup(BaseModel):
    count: int = Field(ge=1, le=city.MAX_FLEET)


class TripRequest(BaseModel):
    pickup: str = Field(pattern="^[A-Z]$")
    dropoff: str = Field(pattern="^[A-Z]$")


class Point(BaseModel):
    trip_id: UUID
    sequence: int = Field(ge=1, le=2**31-1)
    leg: int = Field(default=0, ge=0, le=2)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Batch(BaseModel):
    readings: list[Point] = Field(min_length=1, max_length=city.MAX_FLEET)


async def record(db, reading):
    async with db.acquire() as conn, conn.transaction():
        trip = await conn.fetchrow("SELECT * FROM cab_watch.trips WHERE id=$1 FOR UPDATE", reading.trip_id)
        if not trip or reading.sequence <= trip["last_sequence"] or trip["phase"] == 3:
            return
        if trip["planned_legs"]:
            legs = json.loads(trip["planned_legs"]) if isinstance(trip["planned_legs"], str) else trip["planned_legs"]
            if reading.leg < trip["phase"] or reading.leg > trip["phase"] + 1:
                return
            if reading.leg == trip["phase"] + 1:
                empty_start = trip["phase"] == 0 and trip["last_sequence"] == 0 and len(legs[0]) == 1
                previous_end = city.NODES[legs[trip["phase"]][-1]]
                if not empty_start and city.distance_from_path([previous_end], reading.lat, reading.lon) >= 1:
                    return
            line = [city.NODES[n] for n in legs[reading.leg]]
        else:
            line = ([city.NODES[n] for n in trip["planned_nodes"]] if trip["planned_nodes"]
                    else city.ROUTES[trip["route_index"]][3])
        distance = city.distance_from_path(line, reading.lat, reading.lon)
        consecutive = reading.sequence == trip["last_sequence"] + 1 and reading.leg == trip["phase"]
        count = city.next_streak(distance, trip["off_count"], consecutive)
        await conn.execute("""INSERT INTO cab_watch.gps_readings(trip_id,sequence,lat,lon,distance_m,leg)
            VALUES($1,$2,$3,$4,$5,$6)""", reading.trip_id, reading.sequence, reading.lat,
            reading.lon, distance, reading.leg)
        await conn.execute("""UPDATE cab_watch.trips SET last_sequence=$2,off_count=$3,distance_m=$4,phase=$5
            WHERE id=$1""", reading.trip_id, reading.sequence, count, distance, reading.leg)
        if count >= city.REQUIRED:
            earlier = await conn.fetch("""SELECT sequence,distance_m FROM cab_watch.gps_readings
                WHERE trip_id=$1 AND sequence BETWEEN $2 AND $3 ORDER BY sequence""",
                reading.trip_id, reading.sequence-2, reading.sequence-1)
            evidence = [dict(sequence=r["sequence"], metres=round(r["distance_m"])) for r in earlier]
            evidence.append(dict(sequence=reading.sequence, metres=round(distance)))
            await conn.execute("""INSERT INTO cab_watch.alerts
                (trip_id,opened_sequence,peak_metres,opening_samples)
                VALUES($1,$2,$3,$4::jsonb) ON CONFLICT(trip_id) WHERE closed_sequence IS NULL
                DO UPDATE SET peak_metres=greatest(cab_watch.alerts.peak_metres,excluded.peak_metres)""",
                reading.trip_id, reading.sequence, distance, json.dumps(evidence))
        elif count == 0:
            await conn.execute("""UPDATE cab_watch.alerts SET closed_sequence=$2,resolved_at=now()
                WHERE trip_id=$1 AND closed_sequence IS NULL""", reading.trip_id, reading.sequence)
        if trip["vehicle_id"] and reading.leg == 2:
            depot = city.NODES[legs[2][-1]]
            if city.distance_from_path([depot], reading.lat, reading.lon) < 1:
                await conn.execute("UPDATE cab_watch.trips SET phase=3,completed_at=now() WHERE id=$1", reading.trip_id)
                await conn.execute("""UPDATE cab_watch.vehicles SET status='available',current_node=$2
                    WHERE id=$1""", trip["vehicle_id"], legs[2][-1])


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
            consumer = AIOKafkaConsumer(city.TOPIC, **self.options, group_id="cab-watch.v1",
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
async def network():
    return dict(nodes=city.NODES, roads=city.ROADS, capacity=city.MAX_FLEET,
                depots=city.DEPOTS,
                metres=city.METRES, origin=city.ORIGIN,
                lat_scale=city.LAT_SCALE, lon_scale=city.LON_SCALE)


@router.put("/sessions/{session}/trips/{trip_id}")
async def add_trip(session: UUID, trip_id: UUID, setup: TripRequest, request: Request):
    if setup.pickup == setup.dropoff:
        raise HTTPException(422, "Choose two different intersections")
    _, metres = city.shortest_route(setup.pickup, setup.dropoff)
    app = request.app.state
    await app.cab_stream.start()
    async with app.db.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", str(session))
        previous = await conn.fetchrow("SELECT * FROM cab_watch.trips WHERE id=$1", trip_id)
        if previous:
            if (previous["session_id"] != session or previous["pickup_node"] != setup.pickup
                    or previous["dropoff_node"] != setup.dropoff):
                raise HTTPException(409, "Trip ID already in use")
            vehicle = await conn.fetchrow("SELECT * FROM cab_watch.vehicles WHERE id=$1", previous["vehicle_id"])
            legs = (json.loads(previous["planned_legs"]) if isinstance(previous["planned_legs"], str)
                    else previous["planned_legs"])
        else:
            available = await conn.fetch("""SELECT * FROM cab_watch.vehicles
                WHERE session_id=$1 AND status='available' ORDER BY vehicle_index FOR UPDATE""", session)
            vehicle = city.nearest_available(available, setup.pickup)
            if not vehicle:
                exists = await conn.fetchval("SELECT 1 FROM cab_watch.vehicles WHERE session_id=$1 LIMIT 1", session)
                raise HTTPException(409 if exists else 404,
                                    "All cars are busy" if exists else "Unknown fleet. Create a new fleet.")
            depot = next(d for d in city.DEPOTS if d["id"] == vehicle["depot_id"])
            legs = [city.shortest_route(a, b)[0] for a, b in
                    [(vehicle["current_node"], setup.pickup),
                     (setup.pickup, setup.dropoff), (setup.dropoff, depot["node"])]]
            full = legs[0] + legs[1][1:] + legs[2][1:]
            index = await conn.fetchval("SELECT coalesce(max(route_index)+1,0) FROM cab_watch.trips WHERE session_id=$1", session)
            await conn.execute("""INSERT INTO cab_watch.trips
                (id,session_id,route_index,vehicle_id,pickup_node,dropoff_node,planned_nodes,planned_legs)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb)""",
                trip_id, session, index, vehicle["id"], setup.pickup, setup.dropoff, full, json.dumps(legs))
            await conn.execute("UPDATE cab_watch.vehicles SET status='busy' WHERE id=$1", vehicle["id"])
    depot = next(d for d in city.DEPOTS if d["id"] == vehicle["depot_id"])
    full = legs[0] + legs[1][1:] + legs[2][1:]
    leg_data = [dict(stops=part, path=[city.NODES[n] for n in part],
                     metres=sum(city.GRAPH[a][b] for a, b in zip(part, part[1:]))) for part in legs]
    return dict(id=trip_id, vehicle_id=vehicle["id"], cab=vehicle["vehicle_index"]+1,
                depot_id=depot["id"], depot=depot["name"], depot_node=depot["node"],
                origin=setup.pickup, destination=setup.dropoff,
                color=city.COLORS[vehicle["vehicle_index"] % len(city.COLORS)], legs=leg_data,
                stops=full, path=[city.NODES[n] for n in full], metres=metres,
                total_metres=sum(leg["metres"] for leg in leg_data))


@router.post("/sessions", status_code=201)
async def create_session(setup: Setup, request: Request):
    app = request.app.state
    await app.cab_stream.start()
    session = uuid4()
    vehicles = []
    async with app.db.acquire() as conn, conn.transaction():
        index = 0
        for depot, count in zip(city.DEPOTS, city.depot_allocation(setup.count)):
            for _ in range(count):
                vehicle_id = uuid4()
                vehicles.append(dict(id=vehicle_id, cab=index+1, depot_id=depot["id"],
                                     current_node=depot["node"], status="available"))
                index += 1
        await conn.executemany("""INSERT INTO cab_watch.vehicles
            (id,session_id,vehicle_index,depot_id,current_node) VALUES($1,$2,$3,$4,$5)""",
            [(v["id"],session,v["cab"]-1,v["depot_id"],v["current_node"]) for v in vehicles])
    return dict(id=session, vehicles=vehicles, depots=city.DEPOTS,
                capacity=city.MAX_FLEET, xs=city.XS, ys=city.YS, metres=city.METRES,
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
    # A GPS commit can occur between these reads. Keep the vehicle and its
    # confirmed sequence in one snapshot so the UI never stops on mixed state.
    async with db.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        vehicles = await conn.fetch("""SELECT id,vehicle_index+1 AS cab,depot_id,current_node,status
            FROM cab_watch.vehicles WHERE session_id=$1 ORDER BY vehicle_index""", session)
        if not vehicles:
            raise HTTPException(404, "Simulation expired. Create a new fleet.")
        rows = await conn.fetch("""SELECT t.id,t.last_sequence,t.off_count,t.distance_m,g.lat,g.lon
            FROM cab_watch.trips t LEFT JOIN cab_watch.gps_readings g
            ON (g.trip_id,g.sequence)=(t.id,t.last_sequence) WHERE t.session_id=$1 ORDER BY t.route_index""", session)
        alerts = await conn.fetch("""SELECT a.*,v.vehicle_index+1 AS cab FROM cab_watch.alerts a
            JOIN cab_watch.trips t ON t.id=a.trip_id JOIN cab_watch.vehicles v ON v.id=t.vehicle_id
            WHERE t.session_id=$1 ORDER BY a.id DESC LIMIT 50""",session)
    return dict(vehicles=[dict(v) for v in vehicles], trips=[dict(r) for r in rows],
                alerts=[dict(a) for a in alerts])


@router.post("/sessions/{session}/alerts/{alert_id}/explain")
async def explain(session: UUID, alert_id: int, request: Request):
    db = request.app.state.db
    alert = await db.fetchrow("""SELECT a.*,v.vehicle_index FROM cab_watch.alerts a
        JOIN cab_watch.trips t ON t.id=a.trip_id JOIN cab_watch.vehicles v ON v.id=t.vehicle_id
        WHERE t.session_id=$1 AND a.id=$2""",session,alert_id)
    if not alert:
        raise HTTPException(404, "Unknown alert for this simulation")
    if alert["opening_samples"]:
        samples = (json.loads(alert["opening_samples"]) if isinstance(alert["opening_samples"], str)
                   else alert["opening_samples"])
    else:
        rows = await db.fetch("""SELECT sequence,round(distance_m::numeric) AS metres
            FROM cab_watch.gps_readings WHERE trip_id=$1 AND sequence BETWEEN $2 AND $3 ORDER BY sequence""",
            alert["trip_id"], alert["opened_sequence"]-2, alert["opened_sequence"])
        samples = [dict(sequence=r["sequence"], metres=int(r["metres"])) for r in rows]
    facts = dict(cab=alert["vehicle_index"]+1, threshold_m=city.LIMIT,
                 opening_samples=samples,
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


