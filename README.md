# EV Fleet Charging Management

Hackathon demonstration of a connected EV fleet platform. The service converts live battery telemetry and assigned trips into distance-based readiness, actionable alerts, reservation-aware charger recommendations, and approved charging plans.

## Manager portal

Open `/portal` for the four-tab Fleetwise manager interface: **Overview**, **Vehicles**, **Chargers**, and **Plans & Decisions**. Start with **Low battery before delivery**, **10 vehicles**, and **Load / reset**. Review Van 001, compare distinct charging stations, approve the recommendation, then press **Start** to watch the complete diversion-to-delivery journey.

All tabs use one coherent operational snapshot. The single map keeps station coordinates fixed; charger prices come from scheduler tariffs; three connected stored trips distinguish the active delivery from later schedule-only legs. Seven paused presets include a deterministic unreachable-charger emergency. Alternative approvals remain intentionally comparison-only. See [the manager demonstration guide](docs/manager-demo.md) for scenarios, limits, and verification commands. The older audit below remains historical context.

## Stage 2 capabilities

- Persistent vehicles, daily trips, chargers, tariffs, reservations, alerts, and charging plans.
- Depot power limits enforced across simultaneous charger reservations.
- Idempotent telemetry ingestion into MongoDB, latest-state caching in Redis, and Kafka publication.
- Readiness calculated from available energy, trip distance, vehicle efficiency, and a configurable reserve.
- Fleet-facing distance values: current range, post-trip range, and safe range margin.
- Separate battery-health flags for temperature, state of health, and diagnostic trouble codes.
- Deduplicated `CRITICAL` and `CHARGE_SOON` alerts with an operational lifecycle.
- Charger selection that checks connector compatibility, reachability, port reservations, waiting time, tariffs, charging duration, and the next departure deadline.
- Proposed-plan approval that atomically creates a charger-port reservation.
- Deterministic simulator with realistic battery decrease while driving and increase while charging.
- Seven reproducible demo scenarios and an accelerated simulation clock.
- Manual fleet portal at `/portal` and interactive OpenAPI documentation at `/docs`.

## Architecture

```text
Simulator / vehicle client
          |
          v
FastAPI telemetry API --> MongoDB telemetry history
          |              Redis latest state
          v
        Kafka
          |
          v
Readiness consumer --> deduplicated fleet alert
          |
          v
Charging scheduler --> proposed plan --> approval --> reservation
          |
          v
New telemetry confirms charging and automatically completes the plan
```

Energy remains the backend source of truth. Distance values are derived for operators:

```text
available energy = usable capacity * battery health * SoC
current range = available energy / expected consumption per km
safe range margin = current range - trip distance - reserve distance
```

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
docker compose up --build
```

Open:

- Fleet portal: `http://localhost:8000/portal`
- API documentation: `http://localhost:8000/docs`
- Readiness check: `http://localhost:8000/api/v1/health/ready`

## Fastest manual demo

1. Open `/portal`.
2. Select **Low battery before delivery** and load 10 vehicles.
3. Van 001 appears as **Charging required**; click **Compare charging options**.
4. Review the charger, charging window, target SoC, cost, and explanation.
5. Approve the plan. This creates a confirmed port reservation.
6. Start the simulator. One real second advances one simulated minute.
7. Watch `EN_ROUTE_TO_CHARGER`, `WAITING_FOR_CHARGER` when needed, then `CHARGING`.
8. SoC and range rise from generated telemetry.
9. At target SoC, the plan and reservation become `COMPLETED`, the port releases, and the vehicle becomes `READY`.
10. The delivery resumes from the charger (`RESUMING_TRIP`) and ends at `AT_CUSTOMER`.
11. Stop freezes all telemetry; reseeding automatically stops and resets the disposable SIM fleet.

Other scenarios:

- `CHARGER_CONGESTION`: the cheapest charger has an existing reservation.
- `CHARGER_FAILURE`: one charger is unavailable.
- `UNEXPECTED_LONG_TRIP`: an added long delivery creates a sudden energy deficit.
- `BATTERY_OVERHEATING`: readiness and battery health are shown independently.
- `NORMAL_DAY`: vehicles begin with comfortable range margins.
- `UNREACHABLE_CHARGER`: a remote vehicle has no reachable healthy compatible charger and stays stranded.

## Core endpoints

### Fleet data

```text
POST  /api/v1/vehicles
GET   /api/v1/vehicles
GET   /api/v1/vehicles/{vin}/latest
GET   /api/v1/vehicles/{vin}/readiness
POST  /api/v1/trips
GET   /api/v1/trips/vehicle/{vin}
PATCH /api/v1/trips/{trip_id}
```

### Charging operations

```text
POST  /api/v1/chargers
GET   /api/v1/chargers
POST  /api/v1/depots
GET   /api/v1/depots
POST  /api/v1/tariffs
GET   /api/v1/tariffs
POST  /api/v1/reservations
GET   /api/v1/reservations
PATCH /api/v1/reservations/{reservation_id}
GET   /api/v1/charging/recommendations/{vin}
POST  /api/v1/charging/plans/{vin}
POST  /api/v1/charging/plans/{plan_id}/approve
POST  /api/v1/charging/plans/{plan_id}/reject
POST  /api/v1/charging/plans/{plan_id}/cancel
```

### Simulation and monitoring

```text
POST /api/v1/simulator/scenarios
POST /api/v1/simulator/start
POST /api/v1/simulator/stop
GET  /api/v1/simulator/status
GET  /api/v1/fleet/overview
GET  /api/v1/fleet/vehicles
GET  /api/v1/alerts
PATCH /api/v1/alerts/{dedupe_key}
```

## Tests

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
python -m unittest discover -v
python -m ruff check app tests scripts
python -m ruff format --check app tests scripts
python -m compileall app tests scripts
node --check app/static/app.js
node --test tests/test_frontend.cjs
```

The unit suite covers readiness, distance derivation, health flags, charger pricing versus deadlines, reservation overlap, and simulator battery physics.

## Railway configuration

The existing single-service Railway deployment remains supported. Set:

```env
MONGODB_URI=mongodb://...
REDIS_URL=redis://...
KAFKA_BOOTSTRAP_SERVERS=...
KAFKA_SECURITY_PROTOCOL=PLAINTEXT
KAFKA_SASL_MECHANISM=
KAFKA_USERNAME=
KAFKA_PASSWORD=

RESERVE_RANGE_KM=15
CHARGE_SOON_MARGIN_KM=15
CHARGING_DEADLINE_BUFFER_MINUTES=20
SCHEDULER_SLOT_MINUTES=15
CHARGING_EFFICIENCY=0.92
```

For Railway Simple Kafka, `KAFKA_BOOTSTRAP_SERVERS` can reference the broker's private URL:

```env
KAFKA_BOOTSTRAP_SERVERS=${{kafka-broker.KAFKA_URL}}
```

## Operational boundaries and measurements

Deploy **one replica and one Uvicorn worker**. The in-process mutation lock orders seed, simulator ticks, telemetry consumption, approvals, and reservation interval checks. It is not a distributed lock. MongoDB unique indexes enforce one active plan per vehicle and one reservation per plan. Multiple API replicas need a distributed lease with fencing or transactional scheduling before they are safe.

This is a disposable, unauthenticated hackathon demo, not a tenant-isolated production fleet service. Seed resets only SIM vehicles and their records. Kafka generation IDs and current-event checks prevent old runs from changing new plans. Redis is a latest-state cache; MongoDB remains authoritative. Publication failures stop simulation visibly; resubmitting an event repairs delivery without inserting duplicate telemetry.

Distances are geodesic straight-line demo routes, not road routing. Charger coordinates are distinct real coordinates around Bengaluru, representing fictional demo stations. Charging uses a shared 80%/90% taper curve and 92% efficiency. Plans budget the charger journey, charger-to-customer journey, reserve, and a delivery deadline. Seeded deliveries have a five-hour delivery window (six hours for the unexpected 140 km trip); other trips can specify `delivery_deadline`. Prices use metered grid kWh and applicable tariffs, with an average-rate estimate across the charging window.

The fleet table displays at most 200 vehicles, with its limit clearly indicated. The simulator allows up to 1,000 for experiments; this is not a capacity claim. No 100,000-events/second throughput claim has been measured. See `docs/demo-audit.md` for test evidence and actual load results. Higher throughput needs batched ingestion, a durable transactional outbox, independently scaled producers/consumers, partitioning by vehicle, materialized latest-state reads, and distributed reservation coordination.

Reproducible live acceptance (mutates the disposable SIM fleet):

```sh
python scripts/live_acceptance.py --base-url https://evfleetmanagementservice-production.up.railway.app --output evidence.json
python scripts/live_scenarios.py --base-url https://evfleetmanagementservice-production.up.railway.app --output scenarios.json
python scripts/demo_load.py --base-url https://evfleetmanagementservice-production.up.railway.app --output load.json --seconds 60 --vehicles 10 50
```
