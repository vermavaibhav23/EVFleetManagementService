# EV Fleet Charging Management

**[Launch the application](https://evfleetmanagementservice-production.up.railway.app/portal)** · [API documentation](https://evfleetmanagementservice-production.up.railway.app/docs) · [Scenario guide](docs/manager-demo.md)

A working hackathon demo that helps fleet managers decide **when, where and how much to charge** around a delivery timetable. It compares battery range, queues, prices and deadlines, explains its recommendation, and executes the approved plan in a running simulation. The hosted demo uses simulated vehicles, customers and charging stations; no installation is needed to try it.

## Manager portal

Open the **[live manager portal](https://evfleetmanagementservice-production.up.railway.app/portal)** for **Overview**, **Vehicles**, **Chargers**, and **Plans & Decisions**. Choose a grouped **Scenario**, keep the fleet size at 10, and click **Load / reset**. Selecting a name alone does not load its starting conditions. Review Van 001 and approve the charging plan or manager decision. Every load starts paused at current IST; press **Start** to run it.

Fifteen scenarios are arranged into four compact groups. Non-final cases execute a connected six-stop timetable, including later reserved charging sessions. The planner charges toward full within the protected departure window, preserves reserve by default, and checks continuation through later deliveries. Priority cases offer an explicit reserve exception with recovery requested and feasible package handover/reassignment, or accepting a charging delay while retaining original deadlines.

The city map shows fixed, separated stations, distributed customers, individually selectable cars, green charging pulses and gray waiting vehicles. Port occupancy and queues come from actual simulation sessions. See [the demonstration guide](docs/manager-demo.md) for the full scenario table and limits.

## Current capabilities

- Persistent vehicles, daily trips, chargers, tariffs, reservations, alerts, and charging plans.
- Depot power limits enforced across simultaneous charger reservations.
- Idempotent telemetry ingestion into MongoDB, latest-state caching in Redis, and Kafka publication.
- Readiness calculated from available energy, trip distance, vehicle efficiency, and a configurable reserve.
- Fleet-facing distance values: current range, post-trip range, and safe range margin.
- Separate battery-health flags for temperature, state of health, and diagnostic trouble codes.
- Deduplicated `CRITICAL` and `CHARGE_SOON` alerts with an operational lifecycle.
- Charger selection that checks connector compatibility, reachability, port reservations, waiting time, tariffs, charging duration, delivery deadlines and later stops.
- Serialized proposed-plan approval that revalidates and reserves current and later charging slots.
- Repeatable simulation with battery consumption while driving, no gain while waiting, and charging that follows the same efficiency and taper model as planning.
- Manager choices for charging with accepted delay, or an eligible priority delivery with recovery requested and remaining work reassigned, including package collection.
- Charger-failure and queue-overrun handling that invalidates affected bookings and requests a new decision.
- Per-delivery journey tracking, observed interruptions and recent delivery history.
- Fifteen grouped demo scenarios and an accelerated simulation clock.
- Manual fleet portal at `/portal` and interactive OpenAPI documentation at `/docs`.

## Architecture

```text
Simulator / vehicle client
          |
          v
FastAPI telemetry API --> MongoDB telemetry history
          |           --> Redis latest state + immediate operational readiness
          v
        Kafka
          |
          v
Consumer --> process unapplied events / skip already-applied operations

Manager dashboard --> charging scheduler --> proposed plan
          --> approval revalidation --> current and later reservations
          |
          v
New telemetry confirms charging and automatically completes the plan
```

The backend uses **Python, FastAPI, Pydantic and Uvicorn**. The frontend uses **HTML, CSS, JavaScript and SVG**. MongoDB is the authoritative store; Redis caches the latest vehicle update; Kafka carries VIN-keyed events for background processing and replay. Decisions use explicit rules and calculations, not an LLM or trained model.

Energy remains the backend source of truth. Distance values are derived for operators:

```text
available energy = usable capacity * battery health * SoC
current range = available energy / expected consumption per km
safe range margin = current range - trip distance - reserve distance
```

## Local setup

Install Docker with Docker Compose, then run from the repository root:

```sh
docker compose up --build
```

Compose starts the API, MongoDB, Redis and Kafka with their internal connection settings. Wait for dependency readiness before loading a scenario. For Python development or tests on Windows, use Python 3.11+:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
```

Open:

- [Local fleet portal](http://localhost:8000/portal)
- [Local API documentation](http://localhost:8000/docs)
- [Local readiness check](http://localhost:8000/api/v1/health/ready)

If running the API outside Compose, configure reachable dependency addresses in `.env`; Kafka must advertise an address reachable by that client. The Compose API installs its own Python dependencies.

## Fastest manual demo

1. Open the **[live application](https://evfleetmanagementservice-production.up.railway.app/portal)** or your local portal.
2. Select **Charge Ahead for Later Stops** and click **Load / reset** with 10 vehicles.
3. Van 001 appears as **Charging required**; click **Review options**.
4. Review the charger, charging window, target SoC, cost, and explanation.
5. Approve the plan. This creates a confirmed port reservation.
6. Press **Start** if paused. The default 60x setting requests one simulated minute per tick; processing and database time can slow the actual wall-clock cadence.
7. Watch `EN_ROUTE_TO_CHARGER`, `WAITING_FOR_CHARGER` when needed, then `CHARGING`.
8. SoC and range rise from generated telemetry.
9. At target SoC, the plan and reservation become `COMPLETED`, the port releases, and the vehicle becomes `READY`.
10. The delivery resumes from the charger (`RESUMING_TRIP`) and ends at `AT_CUSTOMER`.
11. **Pause** freezes telemetry; **Start** resumes. **Load / reset** stops and replaces the shared SIM fleet. Finished timetables remain complete while other vehicles and the clock continue.

For a short cost-versus-deadline comparison, load **Busy Chargers - Save Money**, then **Busy Chargers - Protect Deadline**. The first can wait for cheaper East Solar; the second selects more expensive North Hub because the cheaper queue would miss delivery. Use **Manager choices** in a priority-delivery case to see the available exceptions and their consequences before approval.

After approval, the map highlights the route to the charger in blue. **Next stop** shows that charger and the remaining distance; the customer is displayed as the following delivery with a dashed route. Once charging ends, the customer becomes the active destination again. Green glowing vehicles are charging, gray vehicles are waiting, and purple diamonds are customer stops. Use **Fit fleet**, **+ / -**, scrolling or drag-to-pan to explore.

Journey progress records actual stages, skips inapplicable stages and starts a new leg for the next delivery. **Why this plan?** and **Other stations** stay expanded through polling until closed, reloaded or reset. **Review plan** remains available during other requests. Approval shows **Approving...**, prevents duplicate submissions and displays its returned result without waiting for another dashboard refresh. Temporary missed updates show stale-data status; sustained failures disable actions that change fleet state.

Scenario groups: **Normal Operations**, **Charger Scenarios**, **Non-final Deliveries**, and **Final Delivery**. The [demonstration guide](docs/manager-demo.md) lists all 15 cases and station-failure variations. Presets configure starting conditions; all vehicles use shared runtime planning. Reviewing options leaves the simulation running. The selected vehicle’s journey tracker records real transitions for each leg.

| Group | Scenarios |
|---|---|
| Normal Operations | Smooth Deliveries; Charging Needed Later |
| Charger Scenarios | Busy Chargers - Save Money; Busy Chargers - Protect Deadline; Charger Unavailable / Offline / Faulty / Incompatible Connector; Charger Fails During Journey; Queue Takes Longer |
| Non-final Deliveries | Charge Ahead for Later Stops; Tight Next Deadline; Priority Delivery - Reserve Exception; Priority Delivery - No Safe Continuation; Timetable Conflict |
| Final Delivery | Time to Top Up; Deadline First; Priority Final Stop |

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
GET   /api/v1/charging/manager-decisions/{vin}
POST  /api/v1/charging/manager-decisions/{vin}/{choice}
POST  /api/v1/charging/plans/{vin}
POST  /api/v1/charging/plans/{plan_id}/approve
POST  /api/v1/charging/plans/{plan_id}/reject
POST  /api/v1/charging/plans/{plan_id}/cancel
```

### Simulation and monitoring

```text
POST /api/v1/telemetry
POST /api/v1/simulator/scenarios
POST /api/v1/simulator/start
POST /api/v1/simulator/stop
GET  /api/v1/simulator/status
GET  /api/v1/fleet/overview
GET  /api/v1/fleet/manager
GET  /api/v1/fleet/vehicles
GET  /api/v1/alerts
PATCH /api/v1/alerts/{dedupe_key}
GET  /api/v1/health/live
GET  /api/v1/health/ready
```

## Tests

The Python and frontend regression suites cover the current demo workflow. Coverage includes full timetables, disruptions, manager choices, duplicate/replayed telemetry, conflicting reservations, approval responsiveness and charger-route display. These are functional checks, not production performance measurements. Node.js is required for the frontend tests; Python integration tests use an in-memory MongoDB substitute and service doubles.

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m unittest discover -s tests -v
python -m ruff check app tests scripts
python -m ruff format --check app tests scripts
python -m compileall app tests scripts
node --check app/static/app.js
node --test tests/test_frontend.cjs
```

Manager decision choices are `accept-delay` and `deliver-now`; requests must include the reviewed decision context shown in OpenAPI. A stale approval requires a fresh review rather than silently changing the booked option.

## Railway configuration

Railway hosts one application service with MongoDB, Redis and Kafka as backing services. Set their connection values in Railway variables:

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
SCHEDULER_SLOT_MINUTES=1
CHARGING_EFFICIENCY=0.92
```

For Railway Simple Kafka, `KAFKA_BOOTSTRAP_SERVERS` can reference the broker's private URL:

```env
KAFKA_BOOTSTRAP_SERVERS=${{kafka-broker.KAFKA_URL}}
```

## Operational boundaries and measurements

Deploy **one replica and one Uvicorn worker**, as configured in [railway.toml](railway.toml). Process-local locks coordinate simulation mutations, approvals and reservations; physical port and power transitions remain ordered while independent vehicle telemetry writes can run concurrently. These are not distributed locks. Multiple API replicas need distributed coordination or transactional scheduling. A deployment restart pauses simulation; press Start to resume the stored fleet.

This is a disposable, unauthenticated hackathon demo, not a tenant-isolated production fleet service. Seed resets only SIM vehicles and their records. Kafka generation IDs and current-event checks prevent old runs from changing new plans. Redis is a latest-state cache; MongoDB remains authoritative. Publication failures stop simulation visibly; resubmitting an event repairs delivery without inserting duplicate telemetry.

Routes are straight-line geography at a modelled 35 km/h, not road routing or live traffic predictions. The SVG map uses simulated coordinates and the dashboard polls approximately every three seconds. Charger coordinates are distinct locations around Bengaluru representing fictional stations. Charging uses a shared 80%/90% taper curve and 92% efficiency. The normal policy protects 15 km reserve and a 20-minute traffic buffer; new proposals also budget five simulated minutes for review. Deadlines vary by scenario and the fixed timetable includes later deliveries and charging. Overheating and unexpected-long-trip presets are absent from the current menu. Prices use metered grid kWh and applicable tariffs, with an average-rate estimate across the charging window.

The portal accepts 10-100 vehicles and is demonstrated with 10. The simulator API accepts up to 1,000 for experiments; this is not validated capacity. No production throughput, latency percentiles, availability, financial savings or emissions reduction has been established. Use the [current scenario guide](docs/manager-demo.md) for the present demo. Future work includes real vehicle and charger feeds, road-based travel estimates, authenticated fleet access, durable coordination and a measured operator pilot.

Reproducible live acceptance (mutates the disposable SIM fleet):

```sh
python scripts/live_acceptance.py --base-url https://evfleetmanagementservice-production.up.railway.app --output evidence.json
python scripts/live_scenarios.py --base-url https://evfleetmanagementservice-production.up.railway.app --output scenarios.json
python scripts/demo_load.py --base-url https://evfleetmanagementservice-production.up.railway.app --output load.json --seconds 60 --vehicles 10 50
```

## Repository layout and data model

- `app/api/v1/`: fleet, charging, telemetry, simulator and health endpoints.
- `app/models/`: validated API contracts and persisted operational records.
- `app/services/`: readiness, scheduling, tariffs, reservations, journey tracking and simulation.
- `app/static/`: the current manager portal.
- `tests/`: domain, API, complete-journey and frontend regression tests.
- `scripts/`: repeatable live journey, scenario and load checks.
- `docs/manager-demo.md`: the current panel demonstration guide.

MongoDB stores vehicles, trips, depots, chargers, tariffs, reservations, charging plans, telemetry, alerts, simulation state, simulation events and manager decisions. These collections support the current workflow. Journey history is embedded in telemetry. Run IDs, event IDs, sequence numbers, processing markers, parent plans and handover links are required for replay protection and operational continuity.

Charger occupancy counts are calculated from reservations when read, not saved as charger configuration. The unused telemetry `evt` and reservation `grace_period_minutes` fields have been removed from the models. Older documents remain readable; loading a scenario replaces its SIM telemetry and reservations with the current schema and refreshes its charger configuration. Historical non-SIM records are not rewritten by a demo reset.

The older `LOW_BATTERY_BEFORE_TRIP` and `UNREACHABLE_CHARGER` presets remain for journey acceptance and edge-case regression coverage; the portal presents the current 15 scenarios. Earlier phase notes and one-off sample seed scripts are available in Git history.
