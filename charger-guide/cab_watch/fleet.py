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
    route = json.loads(row["planned_route"])
    line = [city.NODES[node] for node in route]
    distance = city.distance_from_path(line, row["last_lat"], row["last_lon"]) if row["last_lat"] is not None else 0
    return dict(id=row["id"], vehicle_id=row["vehicle_id"],
                  origin=route[0], destination=route[-1], route=route,
                  metres=sum(city.GRAPH[a][b] for a, b in zip(route, route[1:])),
                  completed=row["completed_at"] is not None,
                  last_sequence=row["last_sequence"], distance_m=distance,
                  lat=row["last_lat"], lon=row["last_lon"], captured_at=row["last_captured_at"],
                  motion=json.loads(row["motion"]) if row["motion"] else None)


TRIP_SELECT = "SELECT t.* FROM cab_watch_accounts.trips t"


async def fleet_alerts(conn, user):
    return await conn.fetch("""SELECT a.*,t.vehicle_id FROM cab_watch_accounts.alerts a
        JOIN cab_watch_accounts.trips t ON t.id=a.trip_id
        WHERE t.user_id=$1 ORDER BY (a.closed_sequence IS NULL) DESC,a.id DESC LIMIT 1000""", user)


@router.get("/trips")
async def list_trips(request: Request, user: UUID = Depends(owner)):
    async with request.app.state.db.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        alerts = [dict(a) for a in await fleet_alerts(conn, user)]
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
