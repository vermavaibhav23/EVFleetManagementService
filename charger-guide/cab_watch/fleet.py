"""All vehicle access starts with the authenticated account."""

import json
from collections import Counter
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from . import city
from .auth import owner

router = APIRouter(prefix="/me")


async def list_depots(conn):
    return await conn.fetch("SELECT id, name, node FROM cab_watch_accounts.depots ORDER BY node")


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


async def parked_counts(conn, user):
    rows = await conn.fetch(
        """SELECT d.id, count(v.id) AS parked
        FROM cab_watch_accounts.depots d
        LEFT JOIN cab_watch_accounts.vehicles v
          ON v.parked_depot_id=d.id AND v.user_id=$1 AND v.retired_at IS NULL
        GROUP BY d.id""",
        user,
    )
    return {str(row["id"]): row["parked"] for row in rows}


def trip_payload(row, depots_by_node):
    parts = row["planned_legs"]
    if isinstance(parts, str):
        parts = json.loads(parts)
    legs = [
        dict(
            stops=nodes,
            path=[city.NODES[n] for n in nodes],
            metres=sum(city.GRAPH[a][b] for a, b in zip(nodes, nodes[1:])),
        )
        for nodes in (parts[name] for name in city.LEG_NAMES)
    ]
    stops = parts["to_pickup"] + parts["customer_ride"][1:] + parts["to_depot"][1:]
    return dict(
        id=row["id"],
        vehicle_id=row["vehicle_id"],
        cab=row["cab_number"],
        departure_depot_id=depots_by_node[parts["to_pickup"][0]]["id"],
        arrival_depot_id=depots_by_node[parts["to_depot"][-1]]["id"],
        depot=depots_by_node[parts["to_pickup"][0]]["name"],
        arrival_depot=depots_by_node[parts["to_depot"][-1]]["name"],
        origin=parts["customer_ride"][0],
        destination=parts["customer_ride"][-1],
        planned_legs=parts,
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
    depots_by_node = {d["node"]: d for d in await list_depots(conn)} if plans else {}
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
                trip_payload(row, depots_by_node),
                phase=row["phase"],
                completed=row["completed_at"] is not None,
                motion=json.loads(row["motion"]) if row["motion"] else None,
            )
        trips.append(trip)
    alerts = await conn.fetch(
        """SELECT a.*,v.id AS vehicle_id,v.cab_number AS cab FROM cab_watch_accounts.alerts a
        JOIN cab_watch_accounts.trips t ON t.id=a.trip_id JOIN cab_watch_accounts.vehicles v ON v.id=t.vehicle_id
        WHERE v.user_id=$1 ORDER BY (a.closed_sequence IS NULL) DESC,a.id DESC LIMIT 1000""",
        user,
    )
    return dict(
        vehicles=[dict(v) for v in cars],
        parked_counts=await parked_counts(conn, user),
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
                "UPDATE cab_watch_accounts.vehicles SET retired_at=now() WHERE id=ANY($1::text[])",
                idle[:-difference],
            )
        elif difference > 0:
            maximum = await conn.fetchval(
                "SELECT coalesce(max(cab_number),0) FROM cab_watch_accounts.vehicles WHERE user_id=$1",
                user,
            )
            counts = Counter(v["parked_depot_id"] for v in cars if v["parked_depot_id"] is not None)
            depot_ids = [d["id"] for d in await list_depots(conn)]
            additions = []
            for number in range(maximum + 1, maximum + difference + 1):
                depot = min(depot_ids, key=lambda d: counts[d])
                counts[depot] += 1
                additions.append((user, number, depot))
            await conn.executemany(
                "INSERT INTO cab_watch_accounts.vehicles(user_id,cab_number,parked_depot_id) VALUES($1,$2,$3)",
                additions,
            )
        return await snapshot(conn, user, plans=True)


@router.post("/trips", status_code=201, responses={200: {"description": "Previously created trip"}})
async def create_trip(
    setup: TripRequest, request: Request, response: Response,
    idempotency_key: UUID = Header(alias="Idempotency-Key"), user: UUID = Depends(owner),
):
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await lock_fleet(conn, user)
        previous_id = await conn.fetchval(
            """SELECT trip_id FROM cab_watch_accounts.dispatch_requests
               WHERE user_id=$1 AND request_key=$2""", user, idempotency_key,
        )
        trip_id = previous_id or uuid4()
        payload = await dispatch_locked(conn, user, trip_id, setup)
        if previous_id is None:
            await conn.execute(
                """INSERT INTO cab_watch_accounts.dispatch_requests(user_id, request_key, trip_id)
                   VALUES($1,$2,$3)""", user, idempotency_key, trip_id,
            )
        else:
            response.status_code = 200
        return payload


@router.put("/trips/{trip_id}", deprecated=True)
async def dispatch(
    trip_id: UUID, setup: TripRequest, request: Request, user: UUID = Depends(owner)
):
    async with request.app.state.db.acquire() as conn, conn.transaction():
        await lock_fleet(conn, user)
        return await dispatch_locked(conn, user, trip_id, setup)


async def dispatch_locked(conn, user, trip_id, setup):
    if setup.pickup == setup.dropoff:
        raise HTTPException(422, "Choose different pickup and drop-off nodes.")
    # Serialize retries and collisions, including requests from different accounts.
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", str(trip_id)
    )
    existing = await conn.fetchrow(
        """SELECT t.*,v.user_id,v.cab_number FROM cab_watch_accounts.trips t
        JOIN cab_watch_accounts.vehicles v ON v.id=t.vehicle_id WHERE t.id=$1""",
        trip_id,
    )
    depots = await list_depots(conn)
    depots_by_node = {d["node"]: d for d in depots}
    if existing:
        if existing["user_id"] != user:
            raise HTTPException(409, "Trip ID unavailable.")
        payload = trip_payload(existing, depots_by_node)
        if (payload["origin"], payload["destination"]) != (setup.pickup, setup.dropoff):
            raise HTTPException(409, "This request already created a trip with different stops.")
        payload["parked_counts"] = await parked_counts(conn, user)
        return payload
    cars = await vehicles(conn, user)
    idle = [v for v in cars if v["status"] == "available"]
    if not idle:
        raise HTTPException(409, "No available cabs in your fleet.")
    pickup_routes = {node: city.shortest_route(node, setup.pickup)
                     for node in {v["current_node"] for v in idle}}
    car = min(
        idle,
        key=lambda v: (
            pickup_routes[v["current_node"]][1],
            v["cab"],
        ),
    )
    arrival = min(
        depots, key=lambda d: city.shortest_route(setup.dropoff, d["node"])[1]
    )
    legs = {
        "to_pickup": pickup_routes[car["current_node"]][0],
        "customer_ride": city.shortest_route(setup.pickup, setup.dropoff)[0],
        "to_depot": city.shortest_route(setup.dropoff, arrival["node"])[0],
    }
    row = await conn.fetchrow(
        """INSERT INTO cab_watch_accounts.trips (id,vehicle_id,planned_legs)
        VALUES($1,$2,$3::jsonb) RETURNING *""",
        trip_id,
        car["id"],
        json.dumps(legs),
    )
    await conn.execute(
        "UPDATE cab_watch_accounts.vehicles SET status='busy',parked_depot_id=NULL WHERE id=$1",
        car["id"],
    )
    payload = trip_payload(dict(row, cab_number=car["cab"]), depots_by_node)
    payload["parked_counts"] = await parked_counts(conn, user)
    return payload
