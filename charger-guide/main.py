import asyncio
import logging
import os
import ssl
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path

import asyncpg
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from agent import review
from decision import LOW_FRACTION, ROAD_END, assess, drive_step

ROOT = Path(__file__).parent
TOPIC = "charger-guide.readings.v1"
BROKER = os.getenv("GUIDE_BROKER") or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:19092")
DB_URL = os.getenv("GUIDE_DATABASE_URL") or os.getenv("DATABASE_URL", "postgresql://guide:guide@localhost:55432/guide")
logger = logging.getLogger(__name__)


def kafka_options():
    options = dict(bootstrap_servers=BROKER)
    if os.getenv("KAFKA_SECURITY_PROTOCOL") == "SASL_SSL":
        options.update(security_protocol="SASL_SSL",
                       ssl_context=ssl.create_default_context(cadata=os.getenv("KAFKA_SSL_CA_PEM")),
                       sasl_mechanism=os.environ["KAFKA_SASL_MECHANISM"],
                       sasl_plain_username=os.environ["KAFKA_USERNAME"],
                       sasl_plain_password=os.environ["KAFKA_PASSWORD"])
    return options


class Reading(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=80)
    sequence: int = Field(gt=0, le=2**63-1)
    battery_kwh: float = Field(ge=0, allow_inf_nan=False)
    road_km: float = Field(ge=0, allow_inf_nan=False)


async def apply_reading(db, event):
    async with db.acquire() as conn, conn.transaction():
        van = await conn.fetchrow("SELECT * FROM charger_guide.vehicles WHERE id=$1 FOR UPDATE",
                                  event.vehicle_id)
        if not van or event.sequence <= van["last_sequence"] or event.battery_kwh > van["capacity_kwh"]:
            return
        van = await conn.fetchrow("""UPDATE charger_guide.vehicles
            SET battery_kwh=$2, road_km=$3, last_sequence=$4 WHERE id=$1 RETURNING *""",
            event.vehicle_id, event.battery_kwh, event.road_km, event.sequence)
        if event.battery_kwh > van["capacity_kwh"] * LOW_FRACTION:
            await conn.execute("""UPDATE charger_guide.alerts SET resolved_at=now()
                WHERE vehicle_id=$1 AND resolved_at IS NULL""", event.vehicle_id)
            return
        facts = assess(van, await conn.fetch("SELECT * FROM charger_guide.chargers"))
        await conn.execute("""INSERT INTO charger_guide.alerts(vehicle_id, charger_id, reason)
            VALUES($1,$2,$3) ON CONFLICT(vehicle_id) WHERE resolved_at IS NULL
            DO UPDATE SET charger_id=excluded.charger_id, reason=excluded.reason""",
            event.vehicle_id, facts["charger"], facts["reason"])


async def consume(db, consumer):
    async for message in consumer:
        try:
            event = Reading.model_validate_json(message.value)
        except ValidationError:
            logger.warning("Skipping invalid reading at %s/%s", message.partition, message.offset)
        else:
            if message.key == event.vehicle_id.encode():
                await apply_reading(db, event)
        await consumer.commit({TopicPartition(TOPIC, message.partition): message.offset + 1})


@asynccontextmanager
async def lifespan(app):
    async with AsyncExitStack() as stack:
        pg_ca = os.getenv("DATABASE_SSL_CA_PEM")
        pg_options = {"ssl": ssl.create_default_context(cadata=pg_ca)} if pg_ca else {}
        db = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4, **pg_options)
        stack.push_async_callback(db.close)
        async with db.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(73949202)")
            await conn.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        options = kafka_options()
        admin = AIOKafkaAdminClient(**options)
        try:
            await admin.start()
            if TOPIC not in await admin.list_topics():
                try:
                    await admin.create_topics([NewTopic(TOPIC, 2, 1)])
                except TopicAlreadyExistsError:
                    pass
        finally:
            await admin.close()
        producer = AIOKafkaProducer(**options, enable_idempotence=True)
        stack.push_async_callback(producer.stop)
        await producer.start()
        consumer = AIOKafkaConsumer(TOPIC, **options, group_id="charger-guide.v1",
                                    enable_auto_commit=False, auto_offset_reset="earliest")
        stack.push_async_callback(consumer.stop)
        await consumer.start()
        worker = asyncio.create_task(consume(db, consumer))
        app.state.db, app.state.producer, app.state.worker = db, producer, worker
        app.state.drive_task = None
        app.state.drive_lock = asyncio.Lock()
        app.state.drive_stop = asyncio.Event()
        app.state.drive_note = "Paused"
        try:
            yield
        finally:
            await pause_drive()
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker


app = FastAPI(title="EV Charger Guide", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")


@app.get("/")
async def dashboard():
    return FileResponse(ROOT / "index.html")


@app.get("/fleet")
async def fleet():
    if app.state.worker.done():
        raise HTTPException(503, "Telemetry consumer stopped")
    rows = await app.state.db.fetch("""SELECT v.*, a.reason, a.charger_id, a.id AS alert_id FROM charger_guide.vehicles v
        LEFT JOIN charger_guide.alerts a ON a.vehicle_id=v.id AND a.resolved_at IS NULL ORDER BY v.id""")
    return [dict(r) for r in rows]


@app.get("/overview")
async def overview():
    vans = await fleet()
    stations = await app.state.db.fetch("SELECT * FROM charger_guide.chargers ORDER BY road_km")
    task = app.state.drive_task
    return dict(vehicles=vans, chargers=[dict(r) for r in stations], road_end=ROAD_END,
                running=bool(task and not task.done()), drive_note=app.state.drive_note)


async def send_reading(vehicle, battery, road_km):
    seq = await app.state.db.fetchval("SELECT nextval('charger_guide.demo_reading')")
    reading = Reading(vehicle_id=vehicle, sequence=seq, battery_kwh=battery, road_km=road_km)
    await app.state.producer.send_and_wait(TOPIC, reading.model_dump_json().encode(), key=vehicle.encode())
    return seq


async def drive():
    directions = {}
    pending = {}
    try:
        for _ in range(120):
            if app.state.drive_stop.is_set():
                return
            vans = await fleet()
            stations = await app.state.db.fetch("SELECT * FROM charger_guide.chargers")
            moving = False
            for van in vans:
                key = van["id"]
                if van["last_sequence"] < pending.get(key, 0):
                    moving = True
                    continue
                position, battery, directions[key] = drive_step(van, stations, directions.get(key, 1))
                if position != van["road_km"]:
                    pending[key] = await send_reading(key, battery, position)
                    moving = True
            if not moving:
                app.state.drive_note = "Vans stopped · check alerts or send a recovered reading"
                return
            try:
                await asyncio.wait_for(app.state.drive_stop.wait(), timeout=1.5)
            except TimeoutError:
                pass
        app.state.drive_note = "Demo complete"
    except Exception:
        logger.exception("Drive paused")
        app.state.drive_note = "Drive interrupted · try again"


async def pause_drive():
    task = app.state.drive_task
    if task and not task.done():
        app.state.drive_stop.set()
        await task
    app.state.drive_note = "Paused"


@app.post("/drive/{action}")
async def control_drive(action: str):
    async with app.state.drive_lock:
        if action == "start":
            if not app.state.drive_task or app.state.drive_task.done():
                app.state.drive_stop.clear()
                app.state.drive_note = "Driving"
                app.state.drive_task = asyncio.create_task(drive())
        elif action in ("pause", "reset"):
            await pause_drive()
            if action == "reset":
                await send_reading("Van-A", 26, 0)
                await send_reading("Van-B", 24, 12)
        else:
            raise HTTPException(404, "Unknown drive control")
    return {"status": app.state.drive_note}


@app.post("/demo/{vehicle}/{battery}", status_code=202)
async def publish(vehicle: str, battery: int):
    if app.state.worker.done():
        raise HTTPException(503, "Telemetry consumer stopped")
    van = await app.state.db.fetchrow("SELECT * FROM charger_guide.vehicles WHERE id=$1", vehicle)
    if not van:
        raise HTTPException(404, "Unknown van")
    if not 0 <= battery <= van["capacity_kwh"]:
        raise HTTPException(422, "Battery outside vehicle capacity")
    async with app.state.drive_lock:
        await pause_drive()
        position = await app.state.db.fetchval("SELECT road_km FROM charger_guide.vehicles WHERE id=$1", vehicle)
        seq = await send_reading(vehicle, battery, position)
    return {"sequence": seq}


@app.post("/alerts/{alert_id}/review")
async def inspect_alert(alert_id: int):
    db = app.state.db
    van = await db.fetchrow("""SELECT v.* FROM charger_guide.alerts a JOIN charger_guide.vehicles v
        ON v.id=a.vehicle_id WHERE a.id=$1 AND a.resolved_at IS NULL""", alert_id)
    if not van:
        raise HTTPException(404, "Alert resolved")
    facts = assess(van, await db.fetch("SELECT * FROM charger_guide.chargers"))
    result = await review(van, facts)
    current = await db.fetchval("SELECT last_sequence FROM charger_guide.vehicles WHERE id=$1", van["id"])
    if current != van["last_sequence"]:
        raise HTTPException(409, "Battery changed. Review the latest reading.")
    return {**result, "facts": facts}

