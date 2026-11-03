"""All vehicle access starts with the authenticated account."""

import json
from collections import Counter
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from . import city
from .auth import owner

router = APIRouter(prefix="/me")
DEPOTS = {depot["id"]: depot for depot in city.DEPOTS}


class Setup(BaseModel):
    count: int = Field(ge=1, le=city.MAX_FLEET)


class TripRequest(BaseModel):
    pickup: str = Field(pattern="^[A-Z]$")
    dropoff: str = Field(pattern="^[A-Z]$")


async def lock_fleet(conn, user):
    await conn.fetchval(
        "SELECT id FROM cab_watch_accounts.users WHERE id=$1 FOR UPDATE", user
    )


async def vehicles(conn, user):
    return await conn.fetch(
        """SELECT v.id,v.cab_number AS cab,v.parked_depot_id,v.parked_depot_id AS depot_id,d.node AS current_node,v.status
        FROM cab_watch_accounts.vehicles v LEFT JOIN cab_watch_accounts.depots d ON d.id=v.parked_depot_id
        WHERE v.user_id=$1 AND v.retired_at IS NULL ORDER BY v.cab_number""",
        user,
    )


def trip_payload(row):
    parts = row["planned_legs"]
    if isinstance(parts, str):
        parts = json.loads(parts)
    legs = [
        dict(
            stops=nodes,
            path=[city.NODES[n] for n in nodes],
            metres=sum(city.GRAPH[a][b] for a, b in zip(nodes, nodes[1:])),
        )
        for nodes in parts
    ]
    stops = parts[0] + parts[1][1:] + parts[2][1:]
    return dict(
        id=row["id"],
        vehicle_id=row["vehicle_id"],
        cab=row["cab_number"],
        depot=DEPOTS[row["departure_depot_id"]]["name"],
        arrival_depot=DEPOTS[row["arrival_depot_id"]]["name"],
        origin=row["pickup_node"],
        destination=row["dropoff_node"],
        legs=legs,
        stops=stops,
        path=[city.NODES[n] for n in stops],
        metres=legs[1]["metres"],
        total_metres=sum(leg["metres"] for leg in legs),
        color=city.COLORS[(row["cab_number"] - 1) % len(city.COLORS)],
    )


async def snapshot(conn, user, plans=False):
    cars = await vehicles(conn, user)
    columns = (
        "t.*,v.cab_number"
        if plans
        else "t.id,t.vehicle_id,t.last_sequence,t.distance_m,t.created_at"
    )
    rows = await conn.fetch(
        f"""SELECT DISTINCT ON (t.vehicle_id) {columns},g.lat,g.lon
        FROM cab_watch_accounts.trips t JOIN cab_watch_accounts.vehicles v ON v.id=t.vehicle_id
        LEFT JOIN cab_watch_accounts.gps_readings g ON (g.trip_id,g.sequence)=(t.id,t.last_sequence)
        WHERE v.user_id=$1 AND v.retired_at IS NULL ORDER BY t.vehicle_id,t.created_at DESC""",
        user,
    )
    trips = []
    for row in rows:
        trip = dict(
            id=row["id"],
            last_sequence=row["last_sequence"],
            distance_m=row["distance_m"],
            lat=row["lat"],
            lon=row["lon"],
        )
        if plans:
            trip.update(
                trip_payload(row),
                phase=row["phase"],
                completed=row["completed_at"] is not None,
                motion=json.loads(row["motion"]) if row["motion"] else None,
            )
        trips.append(trip)
    alerts = await conn.fetch(
        """SELECT a.*,v.cab_number AS cab FROM cab_watch_accounts.alerts a
        JOIN cab_watch_accounts.trips t ON t.id=a.trip_id JOIN cab_watch_accounts.vehicles v ON v.id=t.vehicle_id
        WHERE v.user_id=$1 ORDER BY (a.closed_sequence IS NULL) DESC,a.id DESC LIMIT 1000""",
        user,
    )
    return dict(
        vehicles=[dict(v) for v in cars],
        trips=trips,
        alerts=[dict(a) for a in alerts],
        limit=city.LIMIT,
    )


@router.get("/state")
async def status(request: Request, user: UUID = Depends(owner)):
    async with (
        request.app.state.db.acquire() as conn,
        conn.transaction(isolation="repeatable_read", readonly=True),
    ):
        return await snapshot(conn, user)


@router.get("/fleet")
async def fleet(request: Request, user: UUID = Depends(owner)):
    async with (
        request.app.state.db.acquire() as conn,
        conn.transaction(isolation="repeatable_read", readonly=True),
    ):
        return await snapshot(conn, user, plans=True)


@router.put("/fleet")
async def resize(setup: Setup, request: Request, user: UUID = Depends(owner)):
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await lock_fleet(conn, user)
        cars = await vehicles(conn, user)
        difference = setup.count - len(cars)
        if difference < 0:
            idle = [v["id"] for v in reversed(cars) if v["status"] == "available"]
            if len(idle) < -difference:
                raise HTTPException(409, "The fleet size must include every busy cab.")
            await conn.execute(
                "UPDATE cab_watch_accounts.vehicles SET retired_at=now() WHERE id=ANY($1::uuid[])",
                idle[:-difference],
            )
        elif difference > 0:
            maximum = await conn.fetchval(
                "SELECT coalesce(max(cab_number),0) FROM cab_watch_accounts.vehicles WHERE user_id=$1",
                user,
            )
            counts = Counter(v["parked_depot_id"] for v in cars if v["parked_depot_id"] is not None)
            additions = []
            for number in range(maximum + 1, maximum + difference + 1):
                depot = min(DEPOTS, key=lambda d: counts[d])
                counts[depot] += 1
                additions.append((uuid4(), user, number, depot))
            await conn.executemany(
                "INSERT INTO cab_watch_accounts.vehicles(id,user_id,cab_number,parked_depot_id) VALUES($1,$2,$3,$4)",
                additions,
            )
        return await snapshot(conn, user, plans=True)


@router.put("/trips/{trip_id}")
async def dispatch(
    trip_id: UUID, setup: TripRequest, request: Request, user: UUID = Depends(owner)
):
    if setup.pickup == setup.dropoff:
        raise HTTPException(422, "Choose different pickup and drop-off nodes.")
    db = request.app.state.db
    async with db.acquire() as conn, conn.transaction():
        await lock_fleet(conn, user)
        # Serialize retries and collisions, including requests from different accounts.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", str(trip_id)
        )
        existing = await conn.fetchrow(
            """SELECT t.*,v.user_id,v.cab_number FROM cab_watch_accounts.trips t
            JOIN cab_watch_accounts.vehicles v ON v.id=t.vehicle_id WHERE t.id=$1""",
            trip_id,
        )
        if existing:
            if existing["user_id"] != user or (
                existing["pickup_node"],
                existing["dropoff_node"],
            ) != (setup.pickup, setup.dropoff):
                raise HTTPException(409, "Trip ID unavailable.")
            return trip_payload(existing)
        cars = await vehicles(conn, user)
        idle = [v for v in cars if v["status"] == "available"]
        if not idle:
            raise HTTPException(409, "No available cabs in your fleet.")
        car = min(
            idle,
            key=lambda v: (
                city.shortest_route(v["current_node"], setup.pickup)[1],
                v["cab"],
            ),
        )
        arrival = min(
            city.DEPOTS, key=lambda d: city.shortest_route(setup.dropoff, d["node"])[1]
        )
        nodes = [car["current_node"], setup.pickup, setup.dropoff, arrival["node"]]
        legs = [city.shortest_route(a, b)[0] for a, b in zip(nodes, nodes[1:])]
        row = await conn.fetchrow(
            """INSERT INTO cab_watch_accounts.trips
            (id,vehicle_id,departure_depot_id,arrival_depot_id,pickup_node,dropoff_node,planned_legs)
            VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) RETURNING *""",
            trip_id,
            car["id"],
            car["parked_depot_id"],
            arrival["id"],
            setup.pickup,
            setup.dropoff,
            json.dumps(legs),
        )
        await conn.execute(
            "UPDATE cab_watch_accounts.vehicles SET status='busy',parked_depot_id=NULL WHERE id=$1",
            car["id"],
        )
        return trip_payload(dict(row, cab_number=car["cab"]))
