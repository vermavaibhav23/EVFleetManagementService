# EV Fleet demo repair and acceptance — 2026-10-01

## Baseline and scope

Worked directly in the existing clean main checkout at `4d805c0cbf88fdfeb6ea68b5a081c054d6623fee`. Preserved the pre-existing non-SIM charger and ignored local credentials/tools. No secret values were printed, changed, or committed. Reviewed Git history, all application modules, models, APIs, simulator, scheduler, frontend, existing tests, container/deployment files and integration configuration.

Baseline: 19 unittest tests / 20 pytest tests passed, Python compilation and JavaScript syntax passed. Ruff found seven broad-exception findings and five files needing formatting. Live repeated approval reproduced HTTP 409 and appeared in Railway logs.

## Defects, root causes, fixes and regressions

| Defect/root cause | Fix and principal files | Regression/evidence |
|---|---|---|
| Concurrent start could create duplicate tasks; seed/approval/tick operations interleaved across awaits. | Reentrant single-process mutation coordination; stop before reseed. `services/coordination.py`, simulator, telemetry and mutation APIs. | Concurrent starts/stops, five concurrent plan/approval requests, seed while running. |
| A failed vehicle task could leave siblings generating events and stale Running status. | Drain all tick work, expose FAILED and sanitized error, recover on restart. `services/simulator.py`, simulator model/API/UI. | Injected Kafka failure, visible task failure, restart and frozen stop event count. |
| Reseed retained cache/runtime state; queued Kafka records could affect new plans. | Reset SIM database/cache/runtime, generation UUID on telemetry/plans and reject stale generation. Stable latest sort by timestamp and sequence. | Seed during run, old generation, old timestamp, duplicates and wrong plan/port tests. |
| Battery-empty vehicles travelled past physical range; terminal/resume states could be lost. | Energy-bounded movement, STRANDED, persistent completion and resume restoration. `services/simulator.py`. | Zero-energy physics, complete journey and reload/resume tests. |
| Charging depended on plan/time rather than physical arrival and ownership; ports could double-book. | Physical arrival, own active reservation/port, charger health and depot occupancy coordination; release on completion/cancel/reject. Simulator/readiness/charging/scheduler. | Complete journey, four-vehicle contention, missing reservation, confirmed blocking reservation, wrong-port/stale events. |
| Arrival telemetry still said En route at zero distance and could skip waiting; a whole interval was credited when a slot opened partway through it. | Emit waiting immediately upon arrival; charge only time after slot start. `services/simulator.py`. | Arrival/slot-boundary regression; repeated live journey. |
| Repeated plan actions returned conflicts; approval used obsolete energy/charger state. | Idempotent generate/approve/reject/cancel; replan on approval, own-reservation exclusion/upsert, alternative charger/port/slot. `api/v1/charging.py`. | Concurrent/repeated API lifecycle, failed charger at approval, delayed deadline, live repeated actions. |
| Slot rounding could precede arrival; target omitted charger-to-customer detour; readiness/cost lacked realistic taper and losses. | Ceiling slots; projected arrival SoC, remaining delivery plus reserve, shared 80/90% taper and 92% efficiency, metered grid energy and tariff estimate. `scheduler.py`, `energy.py`, `pricing.py`. | Slot/taper round trip, target sufficient for route/reserve, price/deadline tests and live plan explanation. |
| Artificial emergency deadlines and naive update datetimes made scheduling unreliable. | Explicit delivery deadline, travel in readiness calculation; timezone-aware model fields and TripUpdate validation. | Naive timestamp rejection, deadline failure and cost-versus-time tests. |
| Failed/hot scenarios could recover incorrectly; long-trip scenario was not a real surprise. | Failed chargers excluded, sustained overheating blocks charging, route extends during active trip. `services/simulator.py`. | All six scenarios individually, deployed hot/failure/long-trip checks. |
| The unexpected 140 km route could make its five-hour delivery window infeasible before an operator could approve charging. | Give this scenario an explicit six-hour window while retaining deadline checks; other scenarios keep five hours. `services/simulator.py`. | Generate and approve after 30 simulated minutes of driving; deployed long-trip case. |
| SIM plans could select an unrelated legacy charger; legacy lowercase/missing port_count broke availability and later caused a dashboard 500. | Restrict SIM planning to its depot; normalize status and default legacy single port. `scheduler.py`, `api/v1/chargers.py`. | Legacy isolation and missing-port regression; deployed chargers/overview 200. |
| 60x clock ignored database processing time, making a trip much slower than advertised. | Monotonic elapsed wall time drives simulation clock. `services/simulator.py`. | Delayed-I/O clock regression; live journey repeat. |
| Consumer duplicated operational work and committed every record, creating growing lag. | Skip already processed/reset records; bounded concurrent batch reads, acknowledge only a fully completed batch, idempotent retry, visible lag/error. `services/alert_consumer.py`, telemetry/readiness, Kafka configuration. | Consumer lock/replay tests and whole-batch commit regression; deployed load/drain measurements. |
| Readiness did not reflect actual Kafka/consumer health. | Mongo/Redis probes, Kafka round trip, consumer status/lag, timeouts and sanitized dependency failures; Railway uses readiness and one worker. Health API, main, railway.toml. | Live dependency health and deployment success, failure regression. |
| Dashboard polls/actions raced; errors and buttons could remain misleading. | One active poll, coalesced follow-up, action epoch, connected/reconnecting/loading/failed state and useful error messages. `static/app.js`. | Four Node DOM tests for polling, stale response suppression, failed health controls and HTML/marker rendering. |
| Chargers stacked at depot; labels collided and mobile symbols shrank. | Distinct fictional Bengaluru station coordinates, geodesic projection, overview plus depot detail, leader-line marker displacement, short IDs/tooltips/key, pixel-sized responsive SVG and contained table scrolling. Simulator, static JS/HTML/CSS. | Desktop 1440x1000 and mobile 390x844 screenshots; page scrollWidth equals clientWidth; map has readable markers. |
| Lint/format baseline failures. | Explicit exception handling at service/task boundaries, import/format cleanup, pinned developer checks. `ruff.toml`, `requirements-dev.txt`, Python modules. | Full lint/format/compile/JS/test checks. |
| Failure regression used a fixed 150 ms sleep and became flaky under CPU load. | Await actual task completion with a bounded timeout. `tests/test_demo_journey.py`. | Full test suite passes without depending on host scheduling speed. |

## Verification

All final verification completed successfully on functional commit `6b575e27c96c4920762dd6d6c1ab0bf0e83da604`. The Python test counts from pytest and unittest overlap; they are not independent suites to add together. Mock integration tests use an ASGI application with Mongo-compatible in-memory storage; deployed tests use actual Railway MongoDB, Redis and Kafka.

## Operating limits

- One Railway replica / one Uvicorn worker in Southeast Asia. Mutation coordination is in-process, not safe for multiple workers or replicas without distributed leases/fencing or transactions.
- This is an unauthenticated disposable hackathon demo; it is not a production multi-tenant fleet system.
- Routes are straight-line geodesics, not street routing. Stations use distinct real coordinates but are fictional demo stations. Cost is an explainable tariff/average charging-rate estimate, not a billing-grade integral.
- Redis caches latest state; MongoDB is authoritative. Kafka publication retries are idempotent, but there is no transactional outbox. A broker failure visibly stops simulation and requires recovery/retry.
- The UI shows up to 200 vehicles. The API's 1,000-vehicle input limit is not a tested sustainable capacity.
- No 100,000-events/second claim. Scaling requires batched ingestion, a durable outbox, partitioned independent consumers, materialized latest-state reads and distributed reservation coordination. Current throughput is constrained by serial mutation coordination and per-event Mongo/Redis/Kafka round trips.

## Final test results

- Windows local environment: Python 3.13.2, Node 24.21.0. 45 pytest tests passed; unittest discovered 44 of those tests and passed. Four Node frontend tests passed. Ruff lint and formatting, Python compileall, JavaScript syntax and git diff whitespace checks passed.
- Added 25 Python regressions (23 demo/state-machine/API tests and two consumer tests) and four frontend tests. No database credentials were required by the mock suite.
- Full deployed 10-vehicle LOW_BATTERY_BEFORE_TRIP journey at 60x passed every state: PARKED -> DRIVING -> EN_ROUTE_TO_CHARGER -> WAITING_FOR_CHARGER -> CHARGING -> READY -> RESUMING_TRIP -> AT_CUSTOMER. Monotonic destination distance, energy direction, physical plug/port ownership, completion, release and frozen stop were asserted from recorded telemetry.
- Independent deployed congestion, failed charger, overheating, unexpected long trip and normal day passed. Each scenario was reseeded during a run and verified stopped with zero emitted events and no plans. Congestion, failure and long trip each reached waiting and charging. Overheating and no-need normal planning returned useful 422 responses. Repeated reject/approve/cancel/regenerate passed.
- Desktop 1440x1000 and mobile 390x844 layouts were inspected using screenshots. No page-wide horizontal overflow; tables scroll inside their containers. Browser console inspection showed no errors after the corrected portal reload.

Final journey: 164.4 seconds from approval polling to customer arrival, target SoC 41.9%, customer SoC 10.384%. Stop froze the event counter at 490; final consumer lag 0. Plan and reservation both COMPLETED.

## Measured load, not a maximum-capacity claim

Environment: actual Railway application plus MongoDB, Redis and Kafka, Southeast Asia, one replica and one Uvicorn worker. Client runs from Windows while the portal also polls. Each run seeds NORMAL_DAY, warms for five seconds, requests a one-second tick at 60x and samples health/status/fleet concurrently every two seconds for at least 60 seconds. The measured duration includes stop draining in-flight work. Resource limits were not profiled. These short observations establish demo-rate behavior, not a long-duration production SLA or maximum sustainable capacity.

| Vehicles | Measured seconds | Events | Events/second | Dashboard probe p95 | Max consumer lag | Lag after stop | Drain check seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| 10 | 64.71 | 190 | 2.94 | 1.291 s | 10 | 0 | 1.56 |
| 50 | 62.24 | 750 | 12.05 | 2.593 s | 42 | 0 | 1.55 |

There were no failed load-test requests or simulator task failures. The configured one-second cadence was not achieved: approximately 3.4 seconds per fleet update with 10 vehicles, and 4.2 seconds with 50. The simulation clock still follows elapsed wall time, so movement honors 60x. The implementation's per-event database/cache/broker operations and serialized mutation path are the principal throughput constraints; no CPU/network profiler was used to apportion their exact costs. Kafka batching removed accumulating replay backlog. Higher sustained throughput requires the architecture changes in Operating limits above.

## Acceptance artifacts

The delivered evidence bundle includes `checks.json`, `live-acceptance-final.json` (request statuses and complete vehicle telemetry), `scenarios.json`, `load-test.json`, final deployment/state evidence, desktop/mobile screenshots and Railway log inspection. Earlier evidence is retained separately to distinguish interim runs from the final application test. A final documentation-only commit records this report; its successful Railway deployment and exact SHA are in the delivery report.

## Changed files since baseline

- `README.md`
- `app/api/v1/alerts.py`
- `app/api/v1/chargers.py`
- `app/api/v1/charging.py`
- `app/api/v1/depots.py`
- `app/api/v1/fleet.py`
- `app/api/v1/health.py`
- `app/api/v1/reservations.py`
- `app/api/v1/simulator.py`
- `app/api/v1/tariffs.py`
- `app/api/v1/telemetry.py`
- `app/api/v1/trips.py`
- `app/api/v1/vehicles.py`
- `app/core/dependencies.py`
- `app/core/kafka.py`
- `app/main.py`
- `app/models/alert.py`
- `app/models/charger.py`
- `app/models/charging.py`
- `app/models/reservation.py`
- `app/models/simulator.py`
- `app/models/telemetry.py`
- `app/models/trip.py`
- `app/models/vehicle.py`
- `app/services/alert_consumer.py`
- `app/services/coordination.py`
- `app/services/energy.py`
- `app/services/fleet_readiness.py`
- `app/services/pricing.py`
- `app/services/scheduler.py`
- `app/services/simulator.py`
- `app/services/telemetry.py`
- `app/static/app.js`
- `app/static/index.html`
- `app/static/styles.css`
- `docs/adr/0002-stage-2-operational-model.md`
- `docs/demo-audit.md`
- `railway.toml`
- `requirements-dev.txt`
- `ruff.toml`
- `scripts/demo_load.py`
- `scripts/live_acceptance.py`
- `scripts/live_scenarios.py`
- `tests/test_consumer_replay.py`
- `tests/test_demo_journey.py`
- `tests/test_frontend.cjs`
