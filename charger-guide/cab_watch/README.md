# CabWatch

CabWatch is a simulated fleet dispatch and route-monitoring dashboard built with FastAPI and PostgreSQL. The browser acts as the GPS device on a fictional A–Z road network; no real vehicles or locations are tracked.

## Start a trip

Sign in, choose **Add trip**, then select a pickup and drop-off on the map. FastAPI assigns a simulated plate and finds the shortest road route with Dijkstra. The cab appears at pickup, starts moving, and finishes at drop-off. Each simultaneous ride gets its own cab. An idle cab can be reused for a later trip.

**Change route** queues a random turn at the next junction. The cab can take any connected road except the planned next road when an alternative exists, including turning back at a junction. **Return to route** cancels a queued turn. During a detour, Return reverses the cab along its current road and retraces every visited junction to the original turn, including loops. Crossing the planned route while wandering clears the alert below 200 metres; reaching 200 metres again opens a new incident. Refreshing the dashboard restores saved trips and alerts. Press **Start** to resume.

## One GPS reading

1. The browser moves each simulated cab, timestamps the point, and posts the current batch to `/cab-watch/me/gps`. Hover over a cab for the age of the displayed point.
2. FastAPI locks the signed-in manager’s trips in a stable order. Each newer sequence is checked against its planned route. A distance of at least 200 m opens an incident; a newer point below 200 m closes it.
3. Latest coordinates, capture time, trip state, trip completion and alert changes commit in one database transaction before the response. Duplicate and older sequences cannot rewind the cab or clear a newer alert.
4. The dashboard applies alert updates directly from the GPS response, after the transaction commits. It does not poll for alerts. Dashboard loading restores saved positions and alerts, including the last known position of older trips; any pending GPS from the same tab is replayed first.

### Dashboard update limitation

Only the browser sending GPS receives its acknowledgement and alert update. Other tabs and separate monitoring clients do not automatically see those changes; refreshing loads the saved state. A future deployment with real vehicle devices or multiple monitoring clients needs an independent dashboard update channel, such as authenticated polling or server-sent events (SSE).

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

### Trip API

Send `POST /cab-watch/me/trips` with a bearer token, a UUID `Idempotency-Key` header, and a body such as `{"pickup":"G","dropoff":"K"}`. A new ride returns 201 with a trip UUID, cab plate and ordered route. Repeating the request returns 200 with the same trip. Reusing the key with different stops returns 409.

`GET /cab-watch/me/trips` restores the account's trips, alerts and route deviation threshold. New rides store one customer route. On upgrade, older rides approaching pickup start at pickup. Rides carrying a passenger keep their progress. A ride already returning to a depot is complete and its cab becomes available. Reload an older open dashboard to use the updated GPS format.

### Ordered telemetry

Each active trip accepts any sequence greater than its last saved sequence. A missing reading does not delay newer positions or other trips in the batch. Late and duplicate readings return the current receipt and latest incident without changing the saved position or alert.

The browser saves up to two unacknowledged batches in account-scoped session storage before drawing their movement. It replays them in order after a refresh in the same tab. It uploads each batch in chunks of at most 25 readings so large fleets share processing time. It automatically retries network failures, 429 and temporary server failures with bounded exponential backoff. Simulation movement waits when the buffer is full. Closing the tab ends this simulator's session storage.

Each server worker admits two telemetry transactions at a time, with one active or waiting batch per account and up to 32 FIFO waiters. Waiting admission is limited to two seconds, database connection acquisition to two seconds, and processing to ten seconds. Overload returns retry guidance. A database advisory lock additionally prevents simultaneous telemetry transactions for the same account across workers. These bounds control resource use and give waiting accounts access in order within a worker. They do not establish a measured fleet-capacity or latency guarantee.
