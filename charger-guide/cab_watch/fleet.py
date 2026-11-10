"""Create simulated rides and restore the signed-in manager's trips."""
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import city
from .auth import owner

router = APIRouter(prefix="/me")


class TripRequest(BaseModel):
    pickup: str = Field(pattern="^[A-Z]$")
    dropoff: str = Field(pattern="^[A-Z]$")


def route_state(row):
    route = json.loads(row["planned_route"])
    motion = json.loads(row["motion"]) if row["motion"] else {}
    legacy = motion.pop("legacy", None)
    plan = {"customer_ride": route}
    if legacy:
        plan.update(to_pickup=legacy["to_pickup"], to_depot=legacy["to_depot"])
    phase = legacy["phase"] if legacy else 0
    return plan, phase, motion or None


def plate_number(plate):
    return int(plate.split("#")[1]) if plate.startswith("CB#") else int(plate[-4:])


def trip_payload(row):
    plan, phase, motion = route_state(row)
    route = plan["customer_ride"]
    cab = plate_number(row["vehicle_id"])
    path = plan[city.LEG_NAMES[min(phase, 2)]] if "to_pickup" in plan else route
    distance = city.distance_from_path([city.NODES[n] for n in path], row["last_lat"], row["last_lon"]) if row["last_lat"] is not None else 0
    result = dict(id=row["id"], vehicle_id=row["vehicle_id"], cab=cab,
                  origin=route[0], destination=route[-1], route=route,
                  metres=sum(city.GRAPH[a][b] for a, b in zip(route, route[1:])),
                  color=city.COLORS[(cab-1) % len(city.COLORS)],
                  phase=3 if row["completed_at"] else phase, completed=row["completed_at"] is not None,
                  last_sequence=row["last_sequence"], distance_m=distance,
                  lat=row["last_lat"], lon=row["last_lon"], captured_at=row["last_captured_at"],
                  motion=motion)
    if "to_pickup" in plan:
        result["planned_legs"] = plan
    return result


TRIP_SELECT = "SELECT t.* FROM cab_watch_accounts.trips t"


async def fleet_alerts(conn, user):
    return await conn.fetch("""SELECT a.*,t.vehicle_id FROM cab_watch_accounts.alerts a
        JOIN cab_watch_accounts.trips t ON t.id=a.trip_id
        WHERE t.user_id=$1 ORDER BY (a.closed_sequence IS NULL) DESC,a.id DESC LIMIT 1000""", user)


@router.get("/trips")
@router.get("/state", include_in_schema=False)
async def list_trips(request: Request, user: UUID = Depends(owner), alerts_only: bool = False):
    async with request.app.state.db.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        alerts = [dict(a, cab=plate_number(a["vehicle_id"])) for a in await fleet_alerts(conn, user)]
        if alerts_only:
            return {"alerts": alerts}
        rows = await conn.fetch(TRIP_SELECT + " WHERE t.user_id=$1 ORDER BY t.created_at", user)
        return dict(trips=[trip_payload(row) for row in rows], alerts=alerts, limit=city.LIMIT)


@router.post("/trips", status_code=201)
async def create_trip(setup: TripRequest, request: Request, response: Response,
                      idempotency_key: UUID = Header(alias="Idempotency-Key"), user: UUID = Depends(owner)):
    if setup.pickup == setup.dropoff:
        raise HTTPException(422, "Choose different pickup and drop-off nodes.")
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await conn.fetchval("SELECT id FROM cab_watch_accounts.users WHERE id=$1 FOR UPDATE", user)
        existing = await conn.fetchrow(TRIP_SELECT + " WHERE t.user_id=$1 AND t.idempotency_key=$2", user, idempotency_key)
        if existing:
            trip = trip_payload(existing)
            if (trip["origin"], trip["destination"]) != (setup.pickup, setup.dropoff):
                raise HTTPException(409, "This request already created a trip with different stops.")
            response.status_code = 200
            return trip

        route, _ = city.shortest_route(setup.pickup, setup.dropoff)
        car = await conn.fetchval("""UPDATE cab_watch_accounts.vehicles SET status='busy'
            WHERE id=(SELECT id FROM cab_watch_accounts.vehicles
                      WHERE user_id=$1 AND status='available'
                      ORDER BY created_at,id LIMIT 1 FOR UPDATE)
            RETURNING id""", user)
        if car is None:
            car = await conn.fetchval("""INSERT INTO cab_watch_accounts.vehicles(user_id,status)
                VALUES($1,'busy') RETURNING id""", user)
        row = await conn.fetchrow("""INSERT INTO cab_watch_accounts.trips
            (id,user_id,idempotency_key,vehicle_id,planned_route) VALUES($1,$2,$3,$4,$5::jsonb) RETURNING *""",
            uuid4(), user, idempotency_key, car, json.dumps(route))
        return trip_payload(row)
