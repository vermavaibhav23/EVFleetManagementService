# CabWatch

CabWatch is a simulated fleet dispatch and route-monitoring dashboard built with FastAPI, Apache Kafka, and PostgreSQL. The browser acts as the GPS device on a fictional A–Z road network; no real vehicles or locations are tracked.

## Dispatch a fleet

Register or sign in with your email and password. Each account owns one persistent fleet. Vehicle IDs are globally unique simulated registration plates such as KA01AB1234. Trips reference the plate directly. The small numbered map marker is a local display label. Enter 1–1000 cars and press **Set fleet**. Repeating the size preserves the vehicle IDs and existing trips. Increasing it adds cars to the least populated depots. Decreasing it retires available cars while keeping their history.

Use **Add trip** to pick two intersections. The closest available cab in your fleet travels to pickup, then drop-off, then the closest depot to drop-off. Only the passenger route is colored. **Change route** queues a side turn at the next eligible junction. **Return to route** finds the closest planned junction by road. After signing in again, choose **Start** to resume a paused trip from its latest recorded GPS update.

The browser sends a two-hour bearer JWT to the private endpoints. FastAPI verifies its signature and claims with the account's random signing key. Passwords use salted scrypt hashes. The live account schema is `cab_watch_accounts` and contains users, depots, vehicles, trips, gps_readings and alerts. Account records are separate from historical anonymous simulations in `cab_watch`. Account ownership is checked before publishing telemetry to Kafka.

## One GPS reading

1. The browser submits a trip ID, sequence, leg number, latitude, and longitude to FastAPI. Every tick advances five simulated seconds, with six metres per map unit.
2. The producer publishes one JSON message per moving car to Kafka topic `cab.location.accounts`, keyed by trip ID. The topic has two partitions.
3. The consumer measures each reading against **the planned route for its current leg**, not the whole round trip. The first reading at least 200 m away opens an alert. A reading below 200 m resolves it. Duplicate or older sequences do not advance the detector.
4. The consumer saves the GPS reading and detector state in one PostgreSQL transaction, then commits the Kafka offset. A replay after a crash is skipped using the saved sequence number.
5. The dashboard polls for the confirmed position, moves the marker, and shows the alert. **Explain alert** asks Gemini to explain stored evidence on demand. If the model or quota is unavailable, the UI labels its rule-based explanation.

## Data retention

Completed-trip GPS history expires after seven days except for each trip's last reading. Completed trips and their alerts expire after 90 days. Fleet ownership persists across logins. Configure GPS_RETENTION_DAYS, TRIP_RETENTION_DAYS, and RETENTION_INTERVAL_HOURS to adjust cleanup.

## Run locally

Install Docker with Docker Compose. From the repository root:

```sh
docker compose up --build
```

Open [the dashboard](http://localhost:18080/cab-watch/). Compose starts FastAPI, PostgreSQL, and Kafka. Optionally set `GEMINI_API_KEY` before starting Compose to enable model-generated explanations.

## Tests

```sh
python -m unittest -v test_cab_watch test_retention
node --test test_motion.mjs
```

Trips store a single JSONB route with three named node arrays: `to_pickup`, `customer_ride`, and `to_depot`. Pickup, drop-off, and both depot locations are derived from their endpoints.

### Dispatch API

Send `POST /cab-watch/me/trips` with a bearer token, an `Idempotency-Key` header containing a UUID, and a body such as `{"pickup":"G","dropoff":"K"}`. Generate one key for a new dispatch and reuse it if the response is lost. The server assigns the trip UUID and returns it in `id` alongside the vehicle and route plan.

A new trip returns 201. Retrying with the same account, key and stops returns the existing trip with 200. Changing the stops under a used key returns 409. Keys are retained with their trips. The older `PUT /me/trips/{trip_id}` route is deprecated.
