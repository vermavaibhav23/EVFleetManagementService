# EV Fleet Charging Management

Full-journey planning, manager approval and deterministic simulation. A journey contains the fixed customer sequence, zero or more charging visits, and an explicit return to the depot. Charging can happen before, between or after deliveries. Completing a charge never completes the journey.

The portal retains the original map-based interface: **Overview**, **Vehicles**, **Chargers**, and **Plans & Decisions**, with the same green styling, attention queue, vehicle details and interactive map. The new planner is integrated into those screens. The map shows the selected journey's complete remaining route, including every charging visit and the depot return; proposed and approved routes have different styles. Drag to pan, scroll or use +/− to zoom, and use **Fit fleet** to reset the view.

Live portal: [EV Fleet on Railway](https://evfleetmanagementservice-production.up.railway.app/portal).

## Run locally

Python 3.13 and MongoDB 7+ are required. MongoDB may be standalone; replica-set transactions are not required.

```sh
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Alternatively, `docker compose up --build` starts the API and MongoDB. Open `/portal` for the manager dashboard and `/docs` for the API. Configuration is in `.env.example`. Redis is no longer required. Optional Kafka ingress accepts the new run-scoped telemetry contract on `vehicle.telemetry.v2`; enable it only when a broker is configured.

## Manager workflow

1. Load `NORMAL_DAY` or `EDGE_CASE_DAY`, specifying a seed and an IST start time. The default start is deliberately fixed for reproducibility. Edge-case coverage requires at least 12 vehicles; all begin at the depot.
2. Keep the clock paused and choose **Plan fleet**, or select a vehicle in the attention queue/map and choose **Review journey**. Under **Plans & Decisions**, use **Find normal journey** for individual alternatives. Planning runs in a separate, cancellable process; its status and cancellation control appear under **Planner jobs**. Fleet planning serves vehicles with fewer reachable chargers first, then earlier deadlines.
3. Review every delivery, charging stop and return, arrival deadlines, service completion, reserve, energy purchases and total cost. Choose the exact alternative to approve. Individual planning also searches for an earlier-completion alternative, including different energy amounts at the same station. **Vehicles** keeps the original fixed delivery timetable visible independently of charging proposals.
4. Approve the complete journey. Its full reservation chain becomes visible atomically. Then start or step the simulated clock.
5. Incidents interrupt affected journeys safely. Request a fresh normal plan or recovery options. Recovery shows the number of late stops, maximum delay, total delay and reserve floor, and requires explicit acknowledgement. No recovery is auto-approved.

Under **Chargers**, daily price charts always show 00:00–24:00 IST, exact tariff boundaries and all ports, with separate proposal, confirmed, active/releasing, completed and cancelled styles. The date control navigates other days. Tables provide the same information without relying on chart colour or hover. Use the global speed selector with **Start**; **+5 min** advances a paused clock. The two supported scenarios replace the old list of single-incident presets.

## Railway deployment

This repository's deployment branch is `main`. Push the reviewed changes to GitHub's `main` branch for the connected Railway service to build and deploy them. A localhost preview does not update Railway. The repository includes a Python 3.13 Dockerfile and `railway.toml`; Railway starts one Uvicorn worker on its assigned `$PORT` and checks `/api/v1/health/ready`.

Set `MONGODB_URI` to the Railway-reachable MongoDB connection and `MONGODB_DB` to the intended database. Kafka is optional and disabled by default; Redis is not used. Retain the existing MongoDB settings when upgrading. Do not point a local test suite at the deployed database.

After the first v2 deployment, open `/portal` and load **Normal day** or **Edge-case day** to create the new run ledger, then plan and approve journeys. Legacy simulation collections are preserved and are not silently imported into the new contract. Later restarts reuse the persisted v2 run. Confirm `/api/v1/health/live` reports `schema_version: 2` and readiness succeeds. The portal's asset URLs use a new version to refresh cached JavaScript and CSS.

## Model and safety

The optimizer uses SciPy's `milp` interface to HiGHS. Binary decisions select charging visits and ports and encode piecewise charging/tariff segments and reservation ordering. Energy quantities and timestamps are continuous. There is no recursive search over target SoC percentages.

Normal planning first enforces customer order, readiness, arrival deadlines, service completion, travel energy, compatible available ports, depot power and battery reserve. It then minimizes whole-journey metered energy cost, followed by return completion time. Recovery keeps physical safety hard and minimizes late-stop count, maximum lateness, total lateness, cost, then completion time. Original deadlines never change.

Charging uses 100%, 60% and 30% of rated power in the 0–80%, 80–90% and 90–100% effective battery-capacity bands. Efficiency separates stored energy from billed grid energy. Tariff integration follows actual draw through these bands, including crossings during a session. Travel, unloading, acceptance windows, waiting allowance, connection, release and review lead time are explicit.

The current **bounded model** permits at most one charging visit per gap between customers, including the first and final gaps. Default bounds are 8 customers, the nearest 4 available compatible stations, a 12-hour horizon, and 8 solver seconds per optimization. Ports are exclusive and each session reserves its full chosen rated power for its entire occupation, including setup/release. This conservative allocation does not optimize shared variable power. Idle vehicle energy and auxiliary loads are not modelled. See [model details](docs/model.md).

Fleet coordination is **constrained-first sequential allocation**, with each provisional journey reserved while solving the following vehicle. It prevents the simple flexible-vehicle/unique-charger conflict and limits solve size. It is not a proof of fleet-wide minimum cost. A different processing order or joint fleet model can improve fleet cost or feasibility.

Every candidate is independently forward-validated before publication and again at approval. Valid incumbents are returned as `FEASIBLE`; `OPTIMAL_MODEL` means all lexicographic phases are proven optimal within the stated single-vehicle model. Other statuses distinguish bounded infeasibility, no incumbent before a time limit, solver errors and validation rejection. A simple station-first fallback is offered after a time limit only if the independent validator accepts the complete journey.

## Persistence and execution

`fleet_ledger/_id=active` is the sole scheduling authority. The run contains vehicles, resources, operations, bookings, event progress, jobs and the simulated clock. Every operational writer uses a MongoDB compare-and-swap on `run_id` and `revision`. Approving or replacing a complete journey is one atomic document replacement, not a sequence of independent reservation inserts. A losing concurrent writer receives HTTP 409. Retrying an approved journey ID is idempotent, including after a lost response.

Physical charging occupation survives cancellation until release completes. Resource/telemetry changes stop affected journeys and invalidate proposals; expired plans cannot silently shift into later reservations. The event-driven executor carries unused tick time into subsequent operations, so tick size does not add artificial time. Service in progress survives replanning. A lease allows only one clock owner across API processes; another process can take over after expiry. Pause/resume and process restarts reuse persisted time, service and operation progress.

Reset swaps in a new run atomically. Old jobs and telemetry cannot mutate it; active solver processes discard results and terminate when their run changes. No unrelated collection is deleted. The document is limited to 12 MiB, vehicles to 100, recent operational messages to 1,000 and retained jobs to 20. Plans are retained for review until reset; reaching the document cap returns an explicit error instead of silently deleting history.

## API contracts

Base path: `/api/v1` (the OpenAPI service version is 2.0).

| Endpoint | Purpose |
|---|---|
| `POST /simulator/load` | Atomically load/reset a scenario |
| `POST /simulator/start`, `/pause`, `/tick` | Run-scoped clock controls |
| `GET /fleet`, `/simulator/state` | Authoritative full-run snapshot |
| `POST /journeys/plan` | Queue coordinated fleet planning |
| `POST /vehicles/{vin}/journeys/plan` | Queue normal/recovery alternatives |
| `GET /jobs/{id}`, `POST /jobs/{id}/cancel` | Inspect/cancel solver work |
| `POST /journeys/{id}/approve` | Approve exact run, plan ID and version |
| `POST /journeys/{id}/cancel` | Cancel remaining work, retain physical release |
| `PUT /resources/{id}` | Update a station or depot power through the same authority |
| `POST /telemetry` | Run/sequence-fenced energy and health update |
| `GET /day-view?day=YYYY-MM-DD` | Full IST day tariffs and port history |
| `GET /health/live`, `/health/ready` | Process and MongoDB readiness |

This is an intentional schema/API revision. Old independent trip, charging-plan and reservation CRUD routes are retired. Old collection contents are preserved but do not participate in v2 simulation. Do not run old and new scheduling writers against the same operational fleet. See [migration and removed logic](docs/migration.md).

## Verification

```sh
pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/test_frontend.cjs
ruff check app tests scripts
```

Set `FLEET_TEST_MONGO` to a disposable local MongoDB URI to enable the real-database tests. They create uniquely named test databases and delete only those databases. These tests include competing OS-process approvals, a crash immediately after commit, retry, reset fencing, HTTP execution and solver-process cancellation. Without that environment variable they are explicitly skipped.

The arithmetic fixture buys 28 kWh at ₹20 and 15 kWh at ₹10 for **₹710**, meeting B's 10:40 deadline. Independent enumeration verifies that buying 13 + 30 arrives at 11:10 and buying all 43 at the expensive charger costs ₹860. Additional tests cover taper billing, tariff precedence, fixed order, delivery-first/after-last charging, alternatives, reserves, service, contention, power loss, both scenarios and tick-size invariance.

For reproducible timing and status counts:

```sh
python scripts/benchmark.py --counts 12 24 --seconds 5 --output benchmark.json
```

See [verification results](docs/verification.md) for the measured environment and limits. No remote deployment is performed by the test or benchmark scripts.
