# EV Fleet Charging Management

Full-journey planning, manager approval and deterministic simulation. A journey contains the fixed customer sequence, zero or more charging visits, and an explicit return to the depot. Charging can happen before, between or after deliveries. Completing a charge never completes the journey.

The portal retains the original map-based interface: **Overview**, **Vehicles**, **Chargers**, and **Plans & Decisions**, with the same green styling, attention queue, vehicle details and interactive map. The new planner is integrated into those screens. The selected vehicle panel includes **Journey progress**: live driving, queuing, connecting, charging, unplugging and unloading status; the current step’s finish/arrival time; battery versus charging target; and completed/upcoming stops. It refreshes with the fleet snapshot every three seconds and shows when the simulation is paused. Activity comes from the vehicle’s committed journey, never an unapproved option. The map shows the selected journey's complete remaining route, including every charging visit and the depot return; proposed and approved routes have different styles. Drag to pan, scroll or use +/− to zoom, and use **Fit fleet** to reset the view.

Live portal: [EV Fleet on Railway](https://evfleetmanagementservice-production.up.railway.app/portal).

## Run locally

Python 3.13 and MongoDB 7+ are required. MongoDB may be standalone; replica-set transactions are not required.

```sh
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Alternatively, `docker compose up --build` starts the API and MongoDB. Open `/portal` for the manager dashboard and `/docs` for the API. Configuration is in `.env.example`. Redis is no longer required. Optional Kafka ingress accepts the new run-scoped telemetry contract on `vehicle.telemetry.v2`; enable it only when a broker is configured.

## Manager workflow

1. Choose one of the **four focused scenarios** below and select **Load / reset**. Select **4–100 vehicles**. The first four cover the core cases; additional vehicles get normal independent routes and distinct, seed-reproducible starting locations. The scenario loads paused at the chosen IST time. Loading replaces the current simulation; deploying new code alone preserves the existing run.
2. Keep the clock paused and choose **Plan fleet**, or select a vehicle in the attention queue/map and choose **Review journey**. Under **Plans & Decisions**, use **Compare journey options**. A single search compares feasible choices without asking the manager to select a solver mode. Planning runs in a separate, cancellable process; its status and cancellation control appear under **Journey searches**. Fleet planning serves vehicles with fewer reachable chargers first, then earlier deadlines.
3. Review every delivery, charging stop and return, arrival deadlines, service completion, reserve, energy purchases and total cost. Choose the exact alternative to approve. Individual planning also searches for an earlier-completion alternative, including different energy amounts at the same station. **Vehicles** keeps the original fixed delivery timetable visible independently of charging proposals.
4. Approve the complete journey. Its full reservation chain becomes visible atomically. Then start or step the simulated clock.
5. Incidents interrupt affected journeys safely. Use **Compare journey options** again. If on-time travel with normal reserve is unavailable, the search compares a normal-reserve journey allowing delays against a deadline-priority journey allowing emergency reserve. Each card highlights its actual trade-off, all delayed customers and their delay minutes, lowest planned battery, exact slots and cost. Delay/reserve exceptions require acknowledgement on that specific card. No replacement is auto-approved.

Both feasible cards have their own approval button. Normal-reserve options are listed first; a reduced-reserve option is never labelled as protecting normal reserve. Emergency reserve means less buffer against extra consumption, not a guarantee that a breakdown occurs. Physical energy, health, port availability and site-power constraints remain enforced. If both deadlines and reserve can be satisfied, show the normal option and a distinct earlier-return alternative when available. Identical journeys are deduplicated; the UI never invents two approvable routes when only one or none exists. Unavailable searches and rejected, replaced and unselected alternatives remain grey cards with reasons.

Decision cards use short pointers: total charging cost, delivery impact, depot return and battery reserve. Every charging visit shows arrival and target battery percentages, energy needed until the next charging stop or depot, and the exact station, port and full occupation window in IST (including connection and release). Detailed stop tables are collapsed under **View journey details**. Rejected, replaced and outdated options remain grey with a reason; raw solver output is only under **Technical details**.

**Reject option** records a manager rejection without booking a port. **Recalculate plans** searches from the current time and available slots. While the clock is running, the button says **Pause & recalculate plans**: one click atomically pauses the simulation and queues the search. It stays paused for review; use **Start** to resume. The panel immediately shows **Searching…**, prevents duplicate clicks, and retires earlier unapproved options. If the search fails or finds no usable plan, the panel says so and keeps recalculation available instead of offering an old approval. Approved journeys and their reservations remain intact until explicitly replaced or cancelled. Recovery approval is enabled after acknowledging that card's consequences.

Approval revalidates all requested slots and shared power against the latest MongoDB snapshot, then commits the complete chain atomically. If approval detects a conflict or outdated plan, it books nothing, pauses the simulation and queues a fresh search; if another search is already active, it asks the manager to refresh after that search finishes. A replacement always needs its own approval. Proposed slots are not guaranteed until that commit. Approved journeys start when the vehicle is ready; they have no departure appointment to expire. Portal planning requests carry the current `run_id` and `pause_for_review: true`; noninteractive API clients may omit the pause flag, but estimates can become stale if simulated time advances during solving or review.

Under **Chargers**, daily price charts always show 00:00–24:00 IST, exact tariff boundaries and all ports, with approved reservations, active/releasing sessions and completed sessions on the port bars. Unapproved, rejected and replaced options are retained in a separate expandable history, so they cannot cover approved bookings. Completed sessions explicitly say **Approved · completed**. The date control navigates other days. Tables provide the same information without relying on chart colour or hover. Use the global speed selector with **Start**; **+5 min** advances a paused clock. Each incident control changes only its stated facts and pauses the common clock for review. Buttons enforce the required vehicle state, and cannot replay an incident twice. Reload the scenario for a clean replay.

## Four focused scenarios

The Everyday map separates South fast and South slow economy by over 10 km and the main and northeast return depots by 13 km. All journey travel calculations use those real fixture coordinates. Charger power-supply records no longer create misleading depot markers; the map shows the depots assigned to vehicles.

| Dropdown | Vehicles and starting situation | What to test |
|---|---|---|
| **1. Everyday charging choices** (`EVERYDAY_CHOICES`) | A1: customer A completed in the northeast; A2: southeast customer area; A3: west satellite charger; A4: independent southern route | A1 buys a small expensive top-up, serves B, buys more cheaply, serves C and returns. A2 needs the fast charger to meet its deadline. A3 charges in place below reserve. A4 needs no charging. |
| **2. Shared chargers & disruptions** (`SHARED_CHARGERS`) | B1: north approach; B2: charging at East hub; B3: near West hub; B4: travelling on an independent route | A real external booking occupies North. Test East failure during charging, North failure before arrival, or reduced West supply independently. Plan/approve B3 before reducing power to demonstrate invalidated reservations. |
| **3. Delivery delays & replanning** (`DELIVERY_DELAYS`) | C1: unloading; C2: driving east; C3: customer K completed; C4: continuing independently | Extend active unloading; increase driving consumption; review a delayed journey for C3's tight later deadline. Original deadlines remain fixed. |
| **4. Assistance & impossible journeys** (`ASSISTANCE_CASES`) | D1: empty at west satellite depot without a compatible connector; D2: driving northeast; D3: parked at a customer; D4: ready on a separate route | Inject severe energy loss while moving; raise a health fault while stopped; distinguish physical assistance from an impossible deadline. No assistance is dispatched. |

All four snapshots include simulated prior-approved journeys. A4 is already continuing its no-charge journey; the shared/delay groups also retain their unaffected control vehicles. Every vehicle added beyond the four core cases is healthy, has sufficient battery for its whole route and starts on a simulated prior-approved journey. Extra vehicles rotate between **driving**, **unloading at a customer**, and **waiting for route readiness**, at distinct seed-reproducible locations. They appear as **Journey approved**, not in the attention count. The first four cases retain the scenario's deliberate charging decisions and incidents; the assistance preset intentionally concentrates problems in those four vehicles.

These journeys are produced by the same optimizer, validator, approval and execution code, starting 15 minutes before the displayed snapshot; no winning route is hardcoded. Prior approvals are marked `seeded_journey` in the ledger. All new/replacement journeys still require manager approval. **Plan fleet** skips vehicles already executing these journeys. Existing saved runs are preserved; use **Load / reset** to get the revised healthy starting mix. A healthy vehicle without an approved journey still needs dispatch review: the attention queue includes planning work as well as physical faults.

A1 uses an explicit demo road corridor: A → Premium → B → Economy → C → depot. Leg energy is 1, 4, 5, 10 and 10 kWh respectively; A starts with 5 kWh and must keep 3 kWh reserve. The optimizer adds **8 kWh at Premium and 20 kWh at Economy**. At the default 08:00 start, 92% charging efficiency means this buys 8.696 + 21.739 grid kWh for approximately **₹391.30**, versus ₹608.70 for buying all 28 stored kWh at Premium. Travel, service, connection/release, reserve, ports and real charging efficiency remain enabled. The map draws straight links; this fixture's explicit road costs govern planning and validation.

Each station now has five IST tariff periods: **00:00–06:00** (70% of its base price), **06:00–10:00** (base), **10:00–16:00** (115%), **16:00–22:00** (140%), and **22:00–24:00** (75%). Premium northeast therefore costs ₹14 / ₹20 / ₹23 / ₹28 / ₹15 per grid kWh; Economy northeast costs ₹7 / ₹10 / ₹11.50 / ₹14 / ₹7.50. The optimizer and billed session integration use these same rates, including a change mid-session. Other start times can change the chosen journey and price.

Existing saved runs retain the tariffs used when their plans were calculated. The charger screen identifies older flat-tariff demo runs; load/reset a scenario to use the new bands. Deployment does not rewrite historical bills or silently reprice approved reservations.

Use one disruption per replay so its effect is clear. Other vehicles keep independent state, but charger ports and each site's power are shared. The four supplies are separate so the unaffected comparison vehicle is not stopped by an unrelated site fault. Incidents pause the entire clock, not just one vehicle. **Start** intentionally resumes continuous simulation, in which unapproved options can become outdated.

`NORMAL_DAY` and `EDGE_CASE_DAY` remain API-only compatibility/stress fixtures for existing runs and benchmarks; they are no longer dropdown choices. New scenario requests preserve the selected fleet size (4–100); smaller counts return a validation error because they would omit core cases.

## Railway deployment

This repository's deployment branch is `main`. Push the reviewed changes to GitHub's `main` branch for the connected Railway service to build and deploy them. A localhost preview does not update Railway. The repository includes a Python 3.13 Dockerfile and `railway.toml`; Railway starts one Uvicorn worker on its assigned `$PORT` and checks `/api/v1/health/ready`.

Set `MONGODB_URI` to the Railway-reachable MongoDB connection and `MONGODB_DB` to the intended database. Kafka is optional and disabled by default; Redis is not used. Retain the existing MongoDB settings when upgrading. Do not point a local test suite at the deployed database.

After deployment, refresh `/portal`, choose one of the four scenarios and select **Load / reset**, then review and approve new journeys. An existing old run remains labelled **Legacy run** until you load a new scenario. Legacy simulation collections are preserved and are not silently imported into the new contract. Later restarts reuse the persisted v2 run. Confirm `/api/v1/health/live` reports `schema_version: 2` and readiness succeeds. The portal's asset URLs use a new version to refresh cached JavaScript and CSS.

## Model and safety

The optimizer uses SciPy's `milp` interface to HiGHS. Binary decisions select charging visits and ports and encode piecewise charging/tariff segments and reservation ordering. Energy quantities and timestamps are continuous. There is no recursive search over target SoC percentages.

Normal planning first enforces customer order, readiness, arrival deadlines, service completion, travel energy, compatible available ports, depot power and battery reserve. It then minimizes whole-journey metered energy cost, followed by return completion time. Recovery keeps physical safety hard and minimizes late-stop count, maximum lateness, total lateness, cost, then completion time. Original deadlines never change.

Charging uses 100%, 60% and 30% of rated power in the 0–80%, 80–90% and 90–100% effective battery-capacity bands. Efficiency separates stored energy from billed grid energy. Tariff integration follows actual draw through these bands, including crossings during a session. Travel, unloading, acceptance windows, waiting allowance, connection and release are explicit. There is no artificial review lead time or optimiser-selected departure delay.

The current **bounded model** permits at most one charging visit per gap between customers, including the first and final gaps. Default bounds are 8 customers, the nearest 4 available compatible stations, a 12-hour horizon, and 8 solver seconds per optimization. Ports are exclusive and each session reserves its full chosen rated power for its entire occupation, including setup/release. This conservative allocation does not optimize shared variable power. Idle vehicle energy and auxiliary loads are not modelled. See [model details](docs/model.md).

Fleet coordination is **constrained-first sequential allocation**, with each provisional journey reserved while solving the following vehicle. It prevents the simple flexible-vehicle/unique-charger conflict and limits solve size. It is not a proof of fleet-wide minimum cost. A different processing order or joint fleet model can improve fleet cost or feasibility.

Every candidate is independently forward-validated before publication and again at approval. Valid incumbents are returned as `FEASIBLE`; `OPTIMAL_MODEL` means all lexicographic phases are proven optimal within the stated single-vehicle model. Other statuses distinguish bounded infeasibility, no incumbent before a time limit, solver errors and validation rejection. A simple station-first fallback is offered after a time limit only if the independent validator accepts the complete journey.

## Persistence and execution

`fleet_ledger/_id=active` is the sole scheduling authority. The run contains vehicles, resources, operations, bookings, event progress, jobs and the simulated clock. Every operational writer uses a MongoDB compare-and-swap on `run_id` and `revision`. Approving or replacing a complete journey is one atomic document replacement, not a sequence of independent reservation inserts. A losing concurrent writer receives HTTP 409. Retrying an approved journey ID is idempotent, including after a lost response.

Physical charging occupation survives cancellation until release completes. Resource/telemetry changes stop affected journeys and invalidate proposals; expired plans cannot silently shift into later reservations. The event-driven executor carries unused tick time into subsequent operations, so tick size does not add artificial time. Service in progress survives replanning. A lease allows only one clock owner across API processes; another process can take over after expiry. Pause/resume and process restarts reuse persisted time, service and operation progress.

Dispatch follows readiness: approval → finish any active service/release and satisfy route readiness → travel to the next approved destination → complete its activity → continue immediately. Travel and service durations remain deterministic. Early charger arrivals wait for the approved slot; its start, end, power, energy target and tariff are unchanged. If readiness no longer permits the booked slot or approved delivery timing, the vehicle stops for review rather than silently moving a booking. The stored `depart` timestamp remains a derived leg estimate/execution record for interpolation and history, not a dispatch instruction. Older saved journeys also use this readiness rule for their next pending leg; an already active leg continues from its persisted progress. The portal shows **Ready to continue** or the specific readiness/activity wait.

Reset swaps in a new run atomically. Old jobs and telemetry cannot mutate it; active solver processes discard results and terminate when their run changes. No unrelated collection is deleted. The document is limited to 12 MiB, vehicles to 100, recent operational messages to 1,000 and retained jobs to 20. Plans are retained for review until reset; reaching the document cap returns an explicit error instead of silently deleting history.

## API contracts

Base path: `/api/v1` (the OpenAPI service version is 2.0).

| Endpoint | Purpose |
|---|---|
| `POST /simulator/load` | Atomically load/reset a scenario |
| `POST /simulator/actions/{action_id}` | Apply one state-checked, run-fenced demo incident and pause |
| `POST /simulator/start`, `/pause`, `/tick` | Run-scoped clock controls |
| `GET /fleet`, `/simulator/state` | Authoritative full-run snapshot |
| `POST /journeys/plan` | Queue coordinated fleet planning |
| `POST /vehicles/{vin}/journeys/plan` | Queue normal/recovery alternatives |
| `GET /jobs/{id}`, `POST /jobs/{id}/cancel` | Inspect/cancel solver work |
| `POST /journeys/{id}/approve` | Approve exact run, plan ID and version |
| `POST /journeys/{id}/reject` | Retain a rejected proposal without booking slots |
| `POST /journeys/{id}/cancel` | Cancel remaining work, retain physical release |
| `PUT /resources/{id}` | Update a station or depot power through the same authority |
| `POST /telemetry` | Run/sequence-fenced energy and health update |
| `GET /day-view?day=YYYY-MM-DD` | Full IST day tariffs and port history |
| `GET /health/live`, `/health/ready` | Process and MongoDB readiness |

This is an intentional schema/API revision. Old independent trip, charging-plan and reservation CRUD routes are retired. Old collection contents are preserved but do not participate in v2 simulation. Do not run old and new scheduling writers against the same operational fleet. See [migration and removed logic](docs/migration.md).

Approval clients must inspect the response `status`: `APPROVED` confirms booking; `REPLAN_QUEUED` or `REFRESH_REQUIRED` means nothing was booked and fresh review is required. `GET /fleet` includes derived `review` explanations on plans; these annotations are not stored as scheduling authority.

Portal searches pause the simulation for review. If it is resumed before approval, or an API client plans without pausing, advancing time can invalidate the unapproved timing estimates. Changed conditions can also make an option stale. The portal then removes the stale proposed charger route and price from the selected vehicle panel. Pending customers whose arrival deadlines have passed are named with their overdue time; already-arrived or unloading customers are excluded. **Update options** searches again from the current time and checks available slots. Earlier cards remain locked as historical estimates. Missing an arrival deadline prevents an on-time delivery, but a delayed journey can still be feasible. Approved journeys are not expired by this review-time rule.

## Verification

```sh
pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/test_frontend.cjs
ruff check app tests scripts
```

Set `FLEET_TEST_MONGO` to a disposable local MongoDB URI to enable the real-database tests. They create uniquely named test databases and delete only those databases. These tests include competing OS-process approvals, a crash immediately after commit, retry, reset fencing, HTTP execution and solver-process cancellation. Without that environment variable they are explicitly skipped.

The arithmetic fixture buys 28 kWh at ₹20 and 15 kWh at ₹10 for **₹710**, meeting B's 10:40 deadline. Independent enumeration verifies that buying 13 + 30 arrives at 11:10 and buying all 43 at the expensive charger costs ₹860. Additional tests cover taper billing, tariff precedence, fixed order, delivery-first/after-last charging, alternatives, reserves, service, contention, power loss, the legacy stress fixtures and tick-size invariance. The focused-scenario tests additionally verify the exact split-charge outcome, fast/slow feasibility, charge-at-origin, zero-charge control, state-gated manual incidents, release occupation, unaffected vehicles and delayed journeys.

For reproducible timing and status counts:

```sh
python scripts/benchmark.py --counts 12 24 --seconds 5 --output benchmark.json
```

See [verification results](docs/verification.md) for the measured environment and limits. No remote deployment is performed by the test or benchmark scripts.
