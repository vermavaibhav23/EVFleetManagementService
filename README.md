# EV Fleet Charging Management

Single-fleet deterministic simulation, telemetry, mixed-integer charging decisions and manager approval. The existing four-tab portal, fleet map, fleet-size selector, four scenarios, cost/alternative cards, tariff charts and incident controls remain. No multitenancy or client API-key layer is introduced.

[Open the live Railway portal](https://evfleetmanagementservice-production.up.railway.app/portal). The deployed version uses PostgreSQL for operational state, Kafka for telemetry/planning delivery, and Railway MongoDB for telemetry history. See the [verification record](docs/verification.md) for checks actually performed.

## Implemented data flow

```text
Load / reset → PostgreSQL scenario + separate simulator checkpoint
Approved instructions → vehicle simulator → durable provider outbox
Provider → HTTP POST /api/v1/telemetry → FastAPI validates → Kafka
                                      (202 after broker acknowledgement)
Kafka vehicle.telemetry.v3 [3 partitions; key = vehicle_id]
  ├─ fleet-v3-state: 3 consumers → PostgreSQL current vehicle state
  └─ fleet-v3-history: 1 consumer → MongoDB telemetry_history

Fresh state needing planning → PostgreSQL job + outbox atomically
Outbox → Kafka planning.requests.v3 [1 partition]
Planning consumer → current PostgreSQL snapshot → isolated HiGHS optimiser
Options → PostgreSQL → manager dashboard
Approve → recheck current state + all slots → atomic booking + simulator instructions
```

The demo embeds three telemetry state consumers, one independent history consumer and one planning consumer in the API process. One separate process runs a solve at a time, using one CPU thread. These counts are an example, not a throughput guarantee. Multiple vehicles share a partition; keep partition count and routing fixed during a run.

The simulator executes the approved mission using its own durable checkpoint: location, battery, delivery progress and operation pointer. It does not directly update dashboard state. Its observations pass through a real HTTP forwarder, FastAPI and Kafka. The telemetry processor applies persisted device progress only after consuming the reading. Progress details come from the simulator outbox checkpoint, not arbitrary HTTP fields. External activity labels cannot complete deliveries or release reservations.

Provider failures retry the same event ID. The service checks run, control version, event identity, sequence, observation time and battery/GPS values. An old reading cannot overwrite newer current state; an old mission reading cannot undo new instructions. Kafka offsets are acknowledged after durable work. Poison messages are recorded in `rejected_events`; transient database failures retry without acknowledging.

The simulator waits for the preceding observation frame to be processed before advancing. This backpressure prevents the small demo racing ahead during an outage. Paused vehicles send a heartbeat about every ten seconds without moving simulated time. Freshness uses both simulation observation time and wall-clock receive time (default limit: 120 seconds). Stale data blocks new planning/approval and appears in the selected vehicle panel. Ordered progress messages are processed without discarding intermediate delivery events.

Automatic planning runs when fresh data is available, the vehicle is safely stopped, no search is pending and no usable approved journey or current review option exists. Identical failed searches are not repeated every heartbeat. Changed physical facts or outdated review options permit another search. Searches pause the demo for review and never auto-approve. Manual Compare journey options and Plan fleet remain available. The job and Kafka delivery outbox commit together, so a crash between saving and publishing does not lose the request.

## Railway deployment — no laptop Docker

**PostgreSQL and Kafka must be configured before deploying this version.** Keep the existing Mongo-only service until the new version passes staging checks. Required Railway Variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Reachable PostgreSQL connection |
| `KAFKA_BOOTSTRAP_SERVERS` | Reachable Kafka broker addresses |
| `MONGODB_URI`, `MONGODB_DB` | Historical telemetry database |
| `KAFKA_SECURITY_PROTOCOL` | Your broker's protocol, including TLS/SASL when required |
| `KAFKA_SASL_MECHANISM`, `KAFKA_USERNAME`, `KAFKA_PASSWORD` | Only if the broker requires them; infrastructure credentials, not client API keys |
| `KAFKA_REPLICATION_FACTOR` | 1 for one demo broker; use suitable replication for production |
| `RUN_WORKERS` | true (default) embeds the demo workers |

The forwarder calls `http://127.0.0.1:$PORT` within Railway. The Dockerfile starts one Uvicorn process on that same port (8000 if `PORT` is absent). The deployed service sets `PORT=8000` to match its existing public domain target and uses `/api/v1/health/ready` as its Railway healthcheck. Keep the API near PostgreSQL to avoid repeated cross-region database round trips. Railway builds the Dockerfile **in the cloud**; Docker does not need to run on the laptop. Local Docker Compose has been removed.

Open `/portal`, Load / reset a scenario, wait for telemetry and automatic options, approve a journey, then Start or +5 min. `/api/v1/health/live` reports schema version 3; readiness checks PostgreSQL and Kafka metadata and returns worker status. HTTP 202 means Kafka accepted a reading, not that processing or planning has finished.

For a separate cloud worker, set `RUN_WORKERS=false` on the API and run `python -m app.worker` in another service with the same infrastructure settings and `PROVIDER_API_URL` pointing to FastAPI. Disable HTTP health checks on the worker service. Shared PostgreSQL locks coordinate device execution and forwarding across replicas.

For lightweight development using remote test services:

```sh
pip install -r requirements.txt
# Set .env from .env.example using remote test connections.
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

MongoDB outages stop only history consumption. It catches up from Kafka when MongoDB returns, within Kafka retention. PostgreSQL or Kafka outages block dependent operational work. Default Kafka retention is seven days; one demo broker has no broker-failure redundancy. Mongo history uses a configurable 30-day TTL. Booking and approval never depend on MongoDB.

## Manager workflow

1. Choose one of the **four focused scenarios** below and select **Load / reset**. Select **4–100 vehicles**. The first four cover the core cases; additional vehicles get normal independent routes and distinct, seed-reproducible starting locations. The scenario loads paused at the chosen IST time. Loading replaces the current simulation; deploying new code alone preserves the existing run.
2. Keep the clock paused and choose **Plan fleet**, or select a vehicle in the attention queue/map and choose **Review journey**. Under **Plans & Decisions**, use **Compare journey options**. A single search compares feasible choices without asking the manager to select a solver mode. Planning runs in a separate, cancellable process; its status and cancellation control appear under **Journey searches**. Fleet planning serves vehicles with fewer reachable chargers first, then earlier deadlines.
3. Review every delivery, charging stop and return, arrival deadlines, service completion, reserve, energy purchases and total cost. The main recommendation is cost-first while keeping the entire remaining journey reachable with the configured reserve and meeting deadlines when feasible. Individual planning can show an ordinary earlier-return alternative only when it saves at least one minute; this is not an emergency-versus-deadline choice. **Vehicles** keeps the original fixed delivery timetable visible independently of charging proposals.
4. Approve the complete journey. Its full reservation chain becomes visible atomically. Then start or step the simulated clock.
5. Incidents interrupt affected journeys safely. Use **Compare journey options** again. If on-time travel with normal reserve is unavailable, recommend a reserve-preserving journey allowing delays. Offer the second **Save delivery deadlines · emergency reserve** choice only if it brings at least one otherwise-late customer back on time, does not worsen another customer's lateness, and actually uses reserve later in the journey. Each card highlights affected customers, delay minutes, lowest planned battery, exact slots and cost. Delay/reserve exceptions require acknowledgement on that specific card. No replacement is auto-approved.

Both feasible choices have their own approval button. Normal-reserve options are listed first. A deadline already missed before the search cannot be rescued by taking more battery risk. The planner checks every remaining leg through the next chargers and depot; a plan that reaches one customer but then runs out of battery is not approvable. Emergency reserve means less buffer against extra consumption, not a planned stranding. Physical energy, health, port availability and site-power constraints remain enforced. If no reserve-preserving journey exists but a physically feasible reduced-reserve journey does, it is labelled as an emergency-reserve alternative, not a two-way deadline trade-off. Ordinary alternatives retain their own labels; only one normal option is recommended. Journeys are deduplicated using the same stop/charger/port sequence and tolerances of one second, 0.01 kWh and ₹0.01, rather than exact solver timestamps. Unavailable searches and rejected, replaced and unselected alternatives remain grey cards with reasons.

Decision cards use short pointers: total charging cost, delivery impact, depot return and battery reserve. Every charging visit shows arrival and target battery percentages, energy needed until the next charging stop or depot, and the exact station, port and full occupation window in IST (including connection and release). Detailed stop tables are collapsed under **View journey details**. Rejected, replaced and outdated options remain grey with a reason; raw solver output is only under **Technical details**.

**An initial reserve exception is separate from sacrificing reserve for deadlines.** When a vehicle can physically reach a charger but would arrive below the normal reserve, the planner first tries an on-time journey that permits this only on the first leg, charges there, and restores the normal reserve for every later arrival including depot return. This requires explicit acknowledgement. If needed, the same limited exception can be combined with delivery delays. A whole-journey emergency-reserve option is considered only after these reserve-restoring searches; its objective preserves as much reserve as the delivery outcome allows before minimizing cost. In the disrupted Van B1 example, 4 kWh at start becomes about 1.55 kWh at Depot rapid; the corrected journey costs about ₹298.04 and returns with 3 kWh, with every delivery on time. Prices/times remain scenario-dependent.

**All chargers remain visible** below the journey cards, including before a successful search, after rejection, after a proposal becomes outdated, and when older jobs leave the recent-job list. Current labels distinguish used, unavailable (failed/incompatible/no power), not selected, not needed, and outside the bounded search. Direct-reach energy and actual booking windows provide supporting facts. A temporarily booked station is not described as permanently unavailable; a station omitted by the bounded search is not declared impossible or more expensive without evidence. These are current-condition explanations, not fabricated rejected journeys. The existing grey historical journey cards remain. Both the review and selected-vehicle panels prefer reserve protection over a cheaper risky proposal by default. Old unapproved proposals must be recalculated after changes to planning rules; approved journeys remain intact.

**Reject option** records a manager rejection without booking a port. **Recalculate plans** searches from the current time and available slots. While the clock is running, the button says **Pause & recalculate plans**: one click atomically pauses the simulation and queues the search. It stays paused for review; use **Start** to resume. The panel immediately shows **Searching…**, prevents duplicate clicks, and retires earlier unapproved options. If the search fails or finds no usable plan, the panel says so and keeps recalculation available instead of offering an old approval. Approved journeys and their reservations remain intact until explicitly replaced or cancelled. Recovery approval is enabled after acknowledging that card's consequences.

Approval revalidates all requested slots and shared power against the latest PostgreSQL snapshot, then commits the complete chain atomically. If approval detects a conflict or outdated plan, it books nothing, pauses the simulation and queues a fresh search; if another search is already active, it asks the manager to refresh after that search finishes. A replacement always needs its own approval. Proposed slots are not guaranteed until that commit. Approved journeys start when the vehicle is ready; they have no departure appointment to expire. Portal planning requests carry the current `run_id` and `pause_for_review: true`; noninteractive API clients may omit the pause flag, but estimates can become stale if simulated time advances during solving or review.

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

## Model and safety

The optimizer uses SciPy's `milp` interface to HiGHS. Binary decisions select charging visits and ports and encode piecewise charging/tariff segments and reservation ordering. Energy quantities and timestamps are continuous. There is no recursive search over target SoC percentages.

Normal planning first enforces customer order, readiness, arrival deadlines, service completion, travel energy, compatible available ports, depot power and battery reserve. It then minimizes whole-journey metered energy cost, followed by return completion time. Recovery keeps physical safety hard and minimizes late-stop count, maximum lateness, total lateness, cost, then completion time. Original deadlines never change.

Charging uses 100%, 60% and 30% of rated power in the 0–80%, 80–90% and 90–100% effective battery-capacity bands. Efficiency separates stored energy from billed grid energy. Tariff integration follows actual draw through these bands, including crossings during a session. Travel, unloading, acceptance windows, waiting allowance, connection and release are explicit. There is no artificial review lead time or optimiser-selected departure delay.

The current **bounded model** permits at most one charging visit per gap between customers, including the first and final gaps. Default bounds are 8 customers, the nearest 4 available compatible stations, a 12-hour horizon, and 8 solver seconds per optimization. Ports are exclusive and each session reserves its full chosen rated power for its entire occupation, including setup/release. This conservative allocation does not optimize shared variable power. Idle vehicle energy and auxiliary loads are not modelled. See [model details](docs/model.md).

Fleet coordination is **constrained-first sequential allocation**, with each provisional journey reserved while solving the following vehicle. It prevents the simple flexible-vehicle/unique-charger conflict and limits solve size. It is not a proof of fleet-wide minimum cost. A different processing order or joint fleet model can improve fleet cost or feasibility.

Every candidate is independently forward-validated before publication and again at approval. Valid incumbents are returned as `FEASIBLE`; `OPTIMAL_MODEL` means all lexicographic phases are proven optimal within the stated single-vehicle model. Other statuses distinguish bounded infeasibility, no incumbent before a time limit, solver errors and validation rejection. A simple station-first fallback is offered after a time limit only if the independent validator accepts the complete journey.

## Storage and relationships

| PostgreSQL table | Purpose |
|---|---|
| `fleet_run` | Active run, clock, policy, scenario events, revision and common transaction lock |
| `vehicles` | Latest observed state, deliveries, approved journey ID and operation pointer |
| `journey_plans` | Proposals, approved operations, cost, timing and execution progress |
| `planning_jobs` | Requests, status, ownership leases and results |
| `stations`, `depots` | Charger ports, tariffs, geometry and site power |
| `reservations` | Approved/occupied port intervals and external demo bookings |
| `simulator_state` | Independent device checkpoint and approved instructions |
| `outbox` | Pending telemetry/planning messages and device progress checkpoints |
| `telemetry_receipts` | Processed event IDs and hashes for duplicate/conflicting-ID checks |
| `rejected_events` | Invalid Kafka message positions and reasons |
| `schema_version` | Applied database schema version |

Entity rows reference the active run through foreign keys. JSON bodies preserve rich operations and the existing validated planner contract. Reservations also expose charger, port and interval columns. Every operational writer uses a PostgreSQL transaction with the run lock and revision fence. Approving/replacing all charging slots commits atomically. The simulator has separate state but shares the PostgreSQL server for durability.

MongoDB keeps **one document per distinct reading** in `telemetry_history`, keyed by run plus event ID. New readings append; repeated IDs use idempotent upserts. Older readings remain useful history even when rejected for current-state replacement. Plans remain in PostgreSQL because approval and booking require transactional access.

Reset replaces the active PostgreSQL run and dependent rows, and fences old Kafka jobs/readings. Existing MongoDB v2 records and historical telemetry are not deleted. A new v3 deployment starts with Load / reset; see [migration](docs/migration.md).

Readiness-driven movement remains: finish service/release → drive to approved destination → complete activity → continue. Early arrivals wait for their approved charging slot. No optimiser-selected departure appointment or random travel is added.

The common run lock, snapshot assembly and 100-vehicle bound are intentional demo limits. Kafka adds buffering and independent readers; it does not eliminate database serialization or speed up MILP. 100k events/sec requires benchmark-driven changes to operational partitioning and deployment. That capacity is not claimed here.

## API

Base `/api/v1`; manager routes retain their current shapes.

| Endpoint | Purpose |
|---|---|
| `POST /simulator/load` | Reset scenario and initialize device readings |
| `POST /simulator/start`, `/pause`, `/tick` | Clock controls; tick returns `TELEMETRY_PENDING` |
| `POST /simulator/actions/{id}` | Run-fenced incident controls |
| `GET /fleet`, `/simulator/state` | Observed operational state for existing UI |
| `POST /journeys/plan`, `/vehicles/{vin}/journeys/plan` | Persist a Kafka planning request |
| `GET /jobs/{id}`, `POST /jobs/{id}/cancel` | Search progress/cancellation |
| `POST /journeys/{id}/approve`, `/reject`, `/cancel` | Approval, revalidation, atomic booking |
| `PUT /resources/{id}` | Charger/site updates and affected-journey interruption |
| `POST /telemetry` | Validate and publish; 202 after Kafka acknowledgement |
| `GET /day-view` | IST tariffs and port history |
| `GET /health/live`, `/health/ready` | Process, PostgreSQL, Kafka metadata and workers |

```json
{
  "event_id": "reading-101",
  "run_id": "demo-run-7",
  "vehicle_id": "SIM-001",
  "sequence": 101,
  "observed_at": "2026-10-05T08:01:00+05:30",
  "lat": 12.9716,
  "lon": 77.5946,
  "energy_kwh": 4.75,
  "activity": "TRAVELLING"
}
```

Use the actual run ID, monotonic per-vehicle sequence and current `control_version` (initially 0). Temperature and health have defaults. The internal simulator supplies these automatically. No API-key header is required.

## Verification

```sh
pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/test_frontend.cjs
ruff check app tests scripts
```

Ordinary tests do not launch Docker, Kafka, PostgreSQL or MongoDB. Real integration tests explicitly skip without all three settings: `FLEET_TEST_POSTGRES`, `FLEET_TEST_KAFKA`, `FLEET_TEST_MONGO`. Use disposable **cloud test services**. Tests create unique PostgreSQL schemas, Kafka topics/groups and MongoDB history databases and clean up only those generated names.

The Railway **Fleet Pipeline Verification** service runs this check inside the cloud over private connections. Its `RAILWAY_DOCKERFILE_PATH` is `tests/Dockerfile.cloud`, its start command is `python -m pytest tests/test_cloud_integration.py -v --tb=short -o log_cli=true`, and its restart policy is **Never**. It has no public endpoint and exits after checking. The three test variables reference the project's PostgreSQL, Kafka and MongoDB services; generated test names isolate the data, and simulator/provider locks are scoped to the PostgreSQL schema. No database password needs to be copied to a laptop.

Railway build/deploy settings are configured in the dashboard: Dockerfile `Dockerfile`, blank custom start command (use the image command), healthcheck `/api/v1/health/ready`, one replica and restart on failure. The unused `railway.toml` was removed after live verification showed this service does not apply it. Railway no longer accepts new Config-as-Code adoption; see [Railway's migration notice](https://docs.railway.com/config-as-code).

```sh
python -m pytest tests/test_cloud_integration.py -q
```

That test covers HTTP → Kafka → state/history → automatic Kafka planning → optimiser → approval → simulation readings → completion, revision conflict, reset fencing, all four preset snapshots, split charging and charger failure during a session. Test scripts never deploy to Railway. See [actual verification status](docs/verification.md).
