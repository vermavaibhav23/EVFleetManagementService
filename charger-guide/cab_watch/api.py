import asyncio
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import UUID
from typing import Literal, Annotated

import httpx
from fastapi import Depends, APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel, Field

from .agent import generate, tool
from .admission import TelemetryAdmission
from . import city
from .retention import retention_settings, run_retention

ROOT = Path(__file__).parent
router = APIRouter(prefix="/cab-watch")


from .auth import owner, router as auth_router
from .fleet import plate_number, route_state, router as fleet_router

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
    captured_at: AwareDatetime | None = None
    motion: Motion | None = None
    sequence: int = Field(ge=1, le=2**31-1)
    leg: int = Field(default=0, ge=0, le=2)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Batch(BaseModel):
    readings: list[Point] = Field(min_length=1, max_length=city.MAX_FLEET)


async def latest_alert(conn, trip_id):
    incident = await conn.fetchrow(
        """SELECT * FROM cab_watch_accounts.alerts WHERE trip_id=$1 ORDER BY id DESC LIMIT 1""", trip_id)
    return dict(incident) if incident else None


async def record(conn, trip, reading):
    legs, phase, _ = route_state(trip)
    direct = "to_pickup" not in legs
    completed = trip["completed_at"] is not None
    if reading.sequence <= trip["last_sequence"] or completed:
        path = legs["customer_ride"] if direct else legs[city.LEG_NAMES[min(phase, 2)]]
        distance = city.distance_from_path([city.NODES[n] for n in path], trip["last_lat"], trip["last_lon"]) if trip["last_lat"] is not None else 0
        return dict(trip_id=reading.trip_id, sequence=trip["last_sequence"], applied=False,
                    distance_m=distance, completed=completed, parked=completed, alert=await latest_alert(conn, reading.trip_id))
    if reading.sequence != trip["last_sequence"] + 1:
        raise HTTPException(409, detail=dict(code="sequence_gap", trip_id=str(reading.trip_id),
            expected_sequence=trip["last_sequence"] + 1, received_sequence=reading.sequence))
    if direct:
        if reading.leg != 0:
            raise HTTPException(422, "This ride has one route")
        line = [city.NODES[n] for n in legs["customer_ride"]]
    else:
        if reading.leg < phase or reading.leg > phase + 1:
            raise HTTPException(422, "Invalid trip leg or leg transition")
        if reading.leg == phase + 1:
            empty_start = phase == 0 and trip["last_sequence"] == 0 and len(legs["to_pickup"]) == 1
            previous_end = city.NODES[legs[city.LEG_NAMES[phase]][-1]]
            if not empty_start and city.distance_from_path([previous_end], reading.lat, reading.lon) >= 1:
                raise HTTPException(422, "Invalid trip leg or leg transition")
        line = [city.NODES[n] for n in legs[city.LEG_NAMES[reading.leg]]]
    distance = city.distance_from_path(line, reading.lat, reading.lon)
    motion = reading.motion.model_dump() if reading.motion else {}
    if not direct:
        motion["legacy"] = dict(to_pickup=legs["to_pickup"], to_depot=legs["to_depot"], phase=reading.leg)
    await conn.execute("""UPDATE cab_watch_accounts.trips SET last_sequence=$2,motion=$3::jsonb,
        last_lat=$4,last_lon=$5,last_captured_at=$6
        WHERE id=$1""", reading.trip_id, reading.sequence, json.dumps(motion) if motion else None,
        reading.lat, reading.lon, reading.captured_at)
    incident = None
    parked = False
    if distance >= city.LIMIT:
        incident = await conn.fetchrow("""INSERT INTO cab_watch_accounts.alerts
            (trip_id,opened_sequence,peak_metres)
            VALUES($1,$2,$3) ON CONFLICT(trip_id) WHERE closed_sequence IS NULL
            DO UPDATE SET peak_metres=greatest(cab_watch_accounts.alerts.peak_metres,excluded.peak_metres) RETURNING *""",
            reading.trip_id, reading.sequence, distance)
    else:
        incident = await conn.fetchrow("""UPDATE cab_watch_accounts.alerts SET closed_sequence=$2,resolved_at=now()
            WHERE trip_id=$1 AND closed_sequence IS NULL RETURNING *""", reading.trip_id, reading.sequence)
    final_leg = direct or reading.leg == 2
    if final_leg and city.distance_from_path([line[-1]], reading.lat, reading.lon) < 1:
        # A wandering cab can pass the destination before returning to its route.
        if not direct or reading.motion is None or reading.motion.mode == "arrived":
            parked = True
            await conn.execute("UPDATE cab_watch_accounts.trips SET completed_at=now() WHERE id=$1", reading.trip_id)
            await conn.execute("UPDATE cab_watch_accounts.vehicles SET status='available' WHERE id=$1",
                               trip["vehicle_id"])
    return dict(trip_id=reading.trip_id, sequence=reading.sequence, applied=True,
                distance_m=distance, completed=parked, parked=parked, alert=dict(incident) if incident else None)


async def apply_schema(conn, namespace="cab_watch_accounts"):
    legacy = await conn.fetchval("""SELECT EXISTS(SELECT FROM information_schema.columns
        WHERE table_schema=$1 AND table_name='trips' AND column_name='planned_legs')""", namespace)
    for filename in (["upgrade_legacy.sql", "schema.sql"] if legacy else ["schema.sql"]):
        await conn.execute((ROOT / filename).read_text(encoding="utf-8").replace("cab_watch_accounts", namespace))
    if legacy:
        await conn.execute(f"""ALTER TABLE {namespace}.trips ADD CONSTRAINT valid_planned_route
            CHECK ({namespace}.valid_route(planned_route))""")


@asynccontextmanager
async def cab_runtime(db):
    async with db.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(73949203)")
        await apply_schema(conn)
    task = asyncio.create_task(run_retention(db, *retention_settings()))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@router.get("")
@router.get("/")
async def page():
    return FileResponse(ROOT / "index.html")


@router.get("/network")
async def network():
    return dict(nodes=city.NODES, roads=city.ROADS,
                metres=city.METRES, origin=city.ORIGIN,
                lat_scale=city.LAT_SCALE, lon_scale=city.LON_SCALE)


@router.post("/me/gps")
async def ingest(batch: Batch, request: Request, user: UUID=Depends(owner)):
    if not hasattr(request.app.state, "telemetry_admission"):
        request.app.state.telemetry_admission = TelemetryAdmission()
    async with request.app.state.telemetry_admission.slot(user):
        try:
            async with asyncio.timeout(10):
                return await ingest_batch(batch, request.app.state.db, user)
        except TimeoutError:
            raise HTTPException(503, "Telemetry processing timed out. Retry this batch.",
                                headers={"Retry-After": "1"}) from None


async def ingest_batch(batch, db, user):
    readings = {point.trip_id: point for point in batch.readings}
    async with db.acquire(timeout=2) as conn, conn.transaction():
        # A second worker must also avoid processing this account simultaneously.
        admitted = await conn.fetchval(
            "SELECT pg_try_advisory_xact_lock(hashtextextended($1, 73949204))", str(user))
        if not admitted:
            raise HTTPException(429, "Telemetry already processing. Retry this batch.",
                                headers={"Retry-After": "1"})
        trips = await conn.fetch("""
            SELECT t.* FROM cab_watch_accounts.trips t
            WHERE t.user_id=$1 AND t.id=ANY($2::uuid[])
            ORDER BY t.id FOR UPDATE OF t""", user, list(readings))
        if len(trips) != len(batch.readings):
            raise HTTPException(422, "Send one reading per trip in your fleet")
        results = [await record(conn, trip, readings[trip["id"]]) for trip in trips]
    return {"accepted": len(trips), "results": results}


@router.post("/me/alerts/{alert_id}/explain")
async def explain(alert_id: int, request: Request,user: UUID=Depends(owner)):
    db = request.app.state.db
    alert = await db.fetchrow("""SELECT a.*,t.vehicle_id FROM cab_watch_accounts.alerts a
        JOIN cab_watch_accounts.trips t ON t.id=a.trip_id
        WHERE t.user_id=$1 AND a.id=$2""",user,alert_id)
    if not alert:
        raise HTTPException(404, "Unknown alert for this simulation")
    facts = dict(vehicle_id=alert["vehicle_id"], cab=plate_number(alert["vehicle_id"]), threshold_m=city.LIMIT,
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


