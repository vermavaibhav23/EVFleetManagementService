# CabWatch

CabWatch is a simulated fleet dispatch and route-monitoring dashboard built with FastAPI and PostgreSQL. The browser acts as the GPS device on a fictional A–Z road network; no real vehicles or locations are tracked.

## Dispatch a fleet

Register or sign in with your email and password. Each account owns one persistent fleet. Vehicle IDs are globally unique simulated registration plates such as KA01AB1234. Trips reference the plate directly. The small numbered map marker is a local display label. Enter 1–1000 cars and press **Set fleet**. Repeating the size preserves the vehicle IDs and existing trips. Increasing it adds cars to the least populated depots. Decreasing it retires available cars while keeping their history.

Use **Add trip** to pick two intersections. The closest available cab in your fleet travels to pickup, then drop-off, then the closest depot to drop-off. Only the passenger route is colored. **Change route** queues a side turn at the next eligible junction. **Return to route** finds the closest planned junction by road. After signing in again, choose **Start** to resume a paused trip from its latest recorded GPS update.

The browser sends a two-hour bearer JWT to the private endpoints. FastAPI verifies its signature and claims with the account's random signing key. Passwords use salted scrypt hashes. The live account schema is `cab_watch_accounts` and contains users, depots, vehicles, trips, dispatch_requests and alerts. Account records are separate from historical anonymous simulations in `cab_watch`. Account ownership is checked before processing telemetry.

## One GPS reading

1. The browser moves each simulated cab, timestamps the point, and posts the current batch to `/cab-watch/me/gps`. Hover over a cab for the age of the displayed point.
2. FastAPI locks the signed-in manager’s trips in a stable order. Each newer sequence is checked against its planned leg. A distance of at least 200 m opens an incident; a newer point below 200 m closes it.
3. Latest coordinates, capture time, trip state, depot arrival and alert changes commit in one database transaction before the response. Duplicate and older sequences cannot rewind the cab or clear a newer alert.
4. The dashboard applies alert updates from the response and polls `GET /cab-watch/me/state?alerts_only=true` about once per second while a cab is busy. Fleet loading restores saved positions, including the last known position of older trips.

## Data retention

New GPS samples update the trip in place. Existing GPS history is preserved on upgrade. Completed trips and their alerts expire after 90 days by default. Configure TRIP_RETENTION_DAYS and RETENTION_INTERVAL_HOURS to adjust cleanup.

## Run locally

Install Docker with Docker Compose. From the repository root:

```sh
docker compose up --build
```

Open [the dashboard](http://localhost:18080/cab-watch/). Compose starts FastAPI and PostgreSQL. Optionally set `GEMINI_API_KEY` before starting Compose to enable model-generated explanations.

## Tests

```sh
python -m unittest -v test_cab_watch test_retention
node --test test_motion.mjs
```

Trips store a single JSONB route with three named node arrays: `to_pickup`, `customer_ride`, and `to_depot`. Pickup, drop-off, and both depot locations are derived from their endpoints.

### Dispatch API

Send `POST /cab-watch/me/trips` with a bearer token, an `Idempotency-Key` header containing a UUID, and a body such as `{"pickup":"G","dropoff":"K"}`. Generate one key for a new dispatch and reuse it if the response is lost. The server assigns the trip UUID and returns it in `id` alongside the vehicle and route plan.

A new trip returns 201. Retrying with the same account, key and stops returns the existing trip with 200. Changing the stops under a used key returns 409. Keys are retained with their trips. The older `PUT /me/trips/{trip_id}` route is deprecated.
