# CabWatch

CabWatch is a simulated fleet dispatch and route-monitoring dashboard built with FastAPI and PostgreSQL. The browser acts as the GPS device on a fictional A–Z road network; no real vehicles or locations are tracked.

## Dispatch a fleet

Register or sign in with your email and password. Each account owns one persistent fleet. Vehicle IDs are globally unique simulated registration plates such as KA01AB1234. Trips reference the plate directly. The small numbered map marker is a local display label. Enter 1–1000 cars and press **Set fleet**. Repeating the size preserves the vehicle IDs and existing trips. Increasing it adds cars to the least populated depots. Decreasing it retires available cars while keeping their history.

Use **Add trip** to pick two intersections. The closest available cab in your fleet travels to pickup, then drop-off, then the closest depot to drop-off. Only the passenger route is colored. **Change route** queues a side turn at the next eligible junction. **Return to route** chooses the shortest remaining journey: road distance from the current position to a rejoin junction plus the remaining original passenger route to drop-off. If totals tie, it rejoins sooner. For example, on an H-M-L passenger route, a cab at N returns via N-M rather than N-H-M. After signing in again, choose **Start** to resume a paused trip from its latest recorded GPS update.

The browser sends a two-hour bearer JWT to the private endpoints. FastAPI verifies its signature and claims with the account's random signing key. Passwords use salted scrypt hashes. The live account schema is `cab_watch_accounts` and contains users, depots, vehicles, trips and alerts. Account records are separate from historical anonymous simulations in `cab_watch`. Account ownership is checked before processing telemetry.

## One GPS reading

1. The browser moves each simulated cab, timestamps the point, and posts the current batch to `/cab-watch/me/gps`. Hover over a cab for the age of the displayed point.
2. FastAPI locks the signed-in manager’s trips in a stable order. Each newer sequence is checked against its planned leg. A distance of at least 200 m opens an incident; a newer point below 200 m closes it.
3. Latest coordinates, capture time, trip state, depot arrival and alert changes commit in one database transaction before the response. Duplicate and older sequences cannot rewind the cab or clear a newer alert.
4. The dashboard applies alert updates directly from the GPS response, after the transaction commits. It does not poll for alerts. Initial fleet loading restores saved positions and alerts, including the last known position of older trips; any pending GPS from the same tab is replayed first.

### Dashboard update limitation

Only the browser sending GPS receives its acknowledgement and alert update. Other tabs and separate monitoring clients do not automatically see those changes; refreshing loads the saved state. A future deployment with real vehicle devices or multiple monitoring clients needs an independent dashboard update channel, such as authenticated polling or server-sent events (SSE). The existing read-only `/me/state` endpoint remains available for API clients, but the dashboard does not call it periodically.

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
python -m unittest -v test_cab_watch test_retention test_telemetry
node --test test_motion.mjs test_telemetry.mjs
```

Set `CABWATCH_TEST_DATABASE_URL` to a test PostgreSQL database and run `python -m unittest -v test_schema` to verify fresh installation, migration, rollback and ownership constraints in isolated temporary schemas. Set `CABWATCH_TEST_URL` to a running test instance's `/cab-watch` URL to run `test_account_flow`, including concurrent dispatch retries and account isolation.

Trips store a single JSONB route with three named node arrays: `to_pickup`, `customer_ride`, and `to_depot`. Pickup, drop-off, and both depot locations are derived from their endpoints.

### Dispatch API

Send `POST /cab-watch/me/trips` with a bearer token, an `Idempotency-Key` header containing a UUID, and a body such as `{"pickup":"G","dropoff":"K"}`. Generate one key for a new dispatch and reuse it if the response is lost. The server assigns the trip UUID and returns it in `id` alongside the vehicle and route plan.

A new trip returns 201. Retrying with the same account, key and stops returns the existing trip with 200. Changing the stops under a used key returns 409. Keys are retained with their trips. The older `PUT /me/trips/{trip_id}` route is deprecated.

Trips store `user_id` from the verified JWT and the UUID `idempotency_key` directly, with `UNIQUE(user_id, idempotency_key)`. A composite foreign key `(vehicle_id, user_id)` references `vehicles(id, user_id)`, preventing assignment to another user's vehicle. Dispatches are serialized per user and trip creation plus vehicle assignment commit together. Startup migrates existing `dispatch_requests` mappings before removing that table; historical trips without mappings receive a generated key. Existing trip IDs, routes, telemetry and alerts are preserved. As trips expire under retention, their retry keys expire with them. Deploy the schema and backend together; the former backend requires the removed table.


### Ordered telemetry

Every trip expects the next consecutive sequence. A gap returns HTTP 409 with the expected sequence and rolls back the whole batch. Retries of processed readings return the current receipt and latest incident, so a lost response cannot hide an alert. Latest coordinates overwrite the trip location, while alert incidents remain available.

The browser saves up to two unacknowledged batches in account-scoped session storage before drawing their movement. It replays them in order after a refresh in the same tab. It uploads each batch in chunks of at most 25 readings so large fleets share processing time. It automatically retries network failures, 429 and temporary server failures with bounded exponential backoff. Simulation movement waits when the buffer is full. Closing the tab ends this simulator's session storage.

Each server worker admits two telemetry transactions at a time, with one active or waiting batch per account and up to 32 FIFO waiters. Waiting admission is limited to two seconds, database connection acquisition to two seconds, and processing to ten seconds. Overload returns retry guidance. A database advisory lock additionally prevents simultaneous telemetry transactions for the same account across workers. These bounds control resource use and give waiting accounts access in order within a worker. They do not establish a measured fleet-capacity or latency guarantee.
