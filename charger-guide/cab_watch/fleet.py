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


def trip_payload(row):
    plan = row["planned_legs"]
    if isinstance(plan, str):
        plan = json.loads(plan)
    route = plan["customer_ride"]
    cab = row["cab_number"]
    result = dict(id=row["id"], vehicle_id=row["vehicle_id"], cab=cab,
                  origin=route[0], destination=route[-1], route=route,
                  metres=sum(city.GRAPH[a][b] for a, b in zip(route, route[1:])),
                  color=city.COLORS[(cab-1) % len(city.COLORS)],
                  phase=row["phase"], completed=row["completed_at"] is not None,
                  last_sequence=row["last_sequence"], distance_m=row["distance_m"],
                  lat=row["last_lat"], lon=row["last_lon"], captured_at=row["last_captured_at"],
                  motion=json.loads(row["motion"]) if row["motion"] else None)
    if "to_pickup" in plan:
        result["planned_legs"] = plan
    return result


TRIP_SELECT = """SELECT t.*, v.cab_number FROM cab_watch_accounts.trips t
    JOIN cab_watch_accounts.vehicles v ON v.id=t.vehicle_id"""


async def fleet_alerts(conn, user):
    return await conn.fetch("""SELECT a.*,v.id AS vehicle_id,v.cab_number AS cab FROM cab_watch_accounts.alerts a
        JOIN cab_watch_accounts.trips t ON t.id=a.trip_id JOIN cab_watch_accounts.vehicles v ON v.id=t.vehicle_id
        WHERE t.user_id=$1 ORDER BY (a.closed_sequence IS NULL) DESC,a.id DESC LIMIT 1000""", user)


@router.get("/trips")
@router.get("/state", include_in_schema=False)
async def list_trips(request: Request, user: UUID = Depends(owner), alerts_only: bool = False):
    async with request.app.state.db.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        alerts = [dict(a) for a in await fleet_alerts(conn, user)]
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
        car = await conn.fetchrow("""UPDATE cab_watch_accounts.vehicles SET status='busy',parked_depot_id=NULL
            WHERE id=(SELECT id FROM cab_watch_accounts.vehicles
                      WHERE user_id=$1 AND retired_at IS NULL AND status='available'
                      ORDER BY cab_number LIMIT 1 FOR UPDATE)
            RETURNING id,cab_number""", user)
        if car is None:
            car = await conn.fetchrow("""INSERT INTO cab_watch_accounts.vehicles(user_id,cab_number,status)
                SELECT $1,coalesce(max(cab_number),0)+1,'busy' FROM cab_watch_accounts.vehicles WHERE user_id=$1
                RETURNING id,cab_number""", user)
        row = await conn.fetchrow("""INSERT INTO cab_watch_accounts.trips
            (id,user_id,idempotency_key,vehicle_id,planned_legs) VALUES($1,$2,$3,$4,$5::jsonb) RETURNING *""",
            uuid4(), user, idempotency_key, car["id"], json.dumps({"customer_ride": route}))
        return trip_payload({**row, "cab_number": car["cab_number"]})
