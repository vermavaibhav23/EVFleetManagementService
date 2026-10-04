# Verification record — 4 October 2026

Local branch: `codex/full-journey-charging`, based on `544f4c65499b55bca55694c917900525835186e3`.

Environment: Windows 11, Python 3.13.2, SciPy 1.16.3, Motor 3.6.0, PyMongo 4.9.2, FastAPI 0.115.6, Pydantic 2.11.3, pytest 8.3.2 and Ruff 0.16.9. Database checks used a separate standalone MongoDB 8.0.20 process bound to localhost, with a fresh uniquely named database per test.

## Checks

- Review-time deadline handling: 57 Python cases passed across the full run and targeted rerun; 20 frontend tests passed. The first full run had one incorrect new test assertion (corrected) and one existing five-second solver case without a plan while other solver work ran. All eight plan-review and scenario tests passed on the isolated rerun in 72.05 seconds. Browser verification reproduced Van A2 at 09:46:30 against its 08:43 deadline: missed customer named, stale charger detour/price removed, and Update options generated current-time delayed alternatives with new costs and slots. Arrived/unloading customers and approved plans are excluded from proposal expiry.

- Trade-off comparison and geography: 56 Python tests passed in 136.55 seconds; 17 frontend tests passed. The real solver produces two independently valid, executable choices in the new regression: retain 3 kWh reserve and delay both customers by 15 minutes, or meet both deadlines and finish with 1 kWh. Both require their own acknowledgement. Tests also cover a single no-charge choice and an empty, chargerless vehicle for which neither mode can invent a feasible route. Existing slot/power/atomic approval tests remain green. Geometry assertions verify southern charger spacing above 10 km and main/northeast depot spacing above 12 km.
- A local MongoDB-backed worker and browser confirmed one comparison action, two independent approval buttons, highlighted trade-offs, all affected customers, exact requested slots, and a grey unavailable strict option. Acknowledging the reserve card did not authorize the emergency-reserve card; that approval returned 422 until its own acknowledgement was checked. Approval preserved the unused option as a grey replaced card. The updated Everyday map shows two actual vehicle depots rather than charger-supply records.

- Healthy starting mix: the latest full run passed all 54 Python tests in 130.49 seconds and all 17 Node frontend tests. All four grouped fixtures preserve the core cases while extra vehicles start in actual approved driving, servicing or scheduled-ready states. Tests execute the extras through all deliveries and depot return with reserve intact; the 100-vehicle fixture remains reproducible apart from unique approval IDs. A4 now executes its prior-approved no-charge journey.
- Local browser/HTTP verification with 12 vehicles showed 9 approved journeys and only the 3 intended Everyday charging decisions needing review. Van A5 showed live driving progress, 61% battery, its independent route and a zero charging estimate. Extra vehicles' simulated prior approvals are disclosed in the selected-vehicle panel. Existing saved runs are not rewritten.

- 54 Python test cases passed in a single full run (124.50 seconds), including 19 focused-scenario, fleet-size and daily-tariff cases. The earlier 35-test baseline comprised: 22 model/physics/execution/timezone tests, 5 real MongoDB/HTTP/process tests, 2 complete scenario tests, 1 Kafka ingress replay/invalid-payload test, and 5 plan-review/booking-refresh tests.
- 9 Node frontend tests passed, including complete map routes, interrupted-route fallback and approval-aware readiness.
- Ruff checks and Python compilation passed; JavaScript syntax checking and Git whitespace checking passed.
- A real browser loaded the 12-vehicle scenario, ran the process-isolated planner, displayed selectable whole-journey alternatives, and approved the cheaper option. The observed example was ₹32.09 with return at 10:05, compared with an earlier-return alternative at ₹60.62.
- All four daily tariff charts and all seven ports were present, with 20 explicit price intervals and proposal/confirmed distinctions. At a 390-pixel viewport, the page had no horizontal overflow; the detailed tables and charts have their own horizontal scroll regions. Browser error logs were empty.
- The original map portal was restored and checked at desktop and 390-pixel mobile widths. All four tabs, vehicle selection, zoom/fit, complete-journey approval and charger date navigation were exercised. Map leader lines no longer intercept vehicle clicks.
- The cancellation regression distinguishes a waiting vehicle from one actually plugged in. Physical release blocks immediate replanning; interruption stops energy draw. A subsecond-tick regression verifies that a travel-triggered stranding event does not keep shifting into the future.

The database suite exercises approvals from separate OS processes, a process exit immediately after durable commit and before response, idempotent retry, reset fencing, preservation of unrelated records, restart persistence, the HTTP plan/approve/execute flow, and termination of a solver belonging to an old run.

## Concise decision cards and booking checks

The review tests cover target battery percentages and the complete requested slot chain, a slot taken after proposal, shared power becoming unavailable, stale departure time, rejected options, and idempotent approval after departure time. A conflict starts a new search without booking a partial chain. Browser checks confirmed grey rejected cards, explicit station/port/second-level IST windows, and a working Refresh options action after simulated time advanced.

## Four focused scenario groups

All four groups loaded through the real local HTTP server backed by isolated MongoDB. Fleet sizes now remain selectable from 4 to 100; the four core cases stay intact. The HTTP checks confirmed stale-run rejection, a successful charging-failure action, retained energy, persisted pause and prevention of duplicate incident replay.

The ten scenario tests cover distinct initial map positions, valid staged charging/driving/unloading states, solver-selected split charging (8 + 20 stored kWh at 92% efficiency), execution through return, fast-versus-slow deadline feasibility, charge-at-origin below reserve, a no-charge journey, external bookings, site-power conflicts, release occupation, preserved deadlines, physical stranding, health blocking and unaffected comparison vehicles. The final Everyday preset refinement was followed by another successful ten-test run (17.17 seconds).

Browser verification confirmed exactly four dropdown choices, four vehicles, a ₹391.30 two-slot split-charge option beside the ₹608.70 one-slot alternative, and a functioning failure button which paused the clock, preserved B2's 3.3 kWh, showed releasing status and disabled repeat application. Deployments retain the user's current run until they explicitly load a new scenario.

## Benchmarks

Command: `python scripts/benchmark.py --counts 12 24 --seconds 5 --output benchmark.json`. Seed 42; start 2026-10-04 08:00 IST. Full per-vehicle records are in [benchmark-results.json](benchmark-results.json).

| Scenario | Vehicles | Proven optimal within model | Valid feasible | Bounded infeasible | Total wall time |
|---|---:|---:|---:|---:|---:|
| Normal day | 12 | 7 | 5 | 0 | 28.22 s |
| Edge-case day | 12 | 2 | 7 | 3 | 36.17 s |
| Normal day | 24 | 14 | 10 | 0 | 63.71 s |
| Edge-case day | 24 | 7 | 14 | 3 | 76.11 s |

Every returned benchmark candidate passed independent validation against earlier allocated journeys. The three infeasible edge vehicles were SIM-001 (zero energy and no compatible depot connector), SIM-003 (impossible original arrival deadline), and SIM-010 (health fault). These outcomes were solver/physical-rule results, not scenario-specific planner branches.

The five-second setting is a solver budget, not a strict end-to-end service-time guarantee. Model construction, incumbent validation, fallback checks, process startup and scheduling add overhead. The largest observed per-vehicle wall time in these runs was 7.72 seconds. Jobs have a separate wall-clock deadline and can be cancelled without blocking the event loop. Time-limited incumbents and fallbacks remain labelled feasible. Measurements were taken on a shared development machine and are not production capacity claims; 100 vehicles were not benchmarked.

The benchmark measures initial planning. The end-to-end scenario tests separately exercise operational events, interruptions and completion. The later travel-trigger adjustment affects execution of the edge scenario, not its initial planning inputs.

## Unverified boundaries

Docker was not running, so a container build was not executed. The application and database were run directly on Windows. Kafka parsing, run fencing, duplicate handling and explicit per-partition commits were tested with a consumer fixture; no live Kafka broker was available. Automated tests use only isolated local databases. Deployment verification is separate from these tests; the repository README describes the GitHub-to-Railway workflow.

The model deliberately does not promise a global fleet optimum, variable shared-power allocation, charger chaining within one customer gap, auxiliary battery drain or real road-network travel times. See [model scope](model.md) and [migration notes](migration.md) before using the new contract with existing clients.

## Restored fleet size selection

The browser accepted a fleet size of 12, retained it when switching scenarios, and loaded all 12 vehicles. Five additional tests check expansion in every scenario, preservation of the core vehicles and incident controls, valid added journeys, distinct starting positions, the 4–100 limits and a repeatable 100-vehicle fleet. All 96 added vehicles in the largest fixture received independently validated journeys.

## Restored selected-vehicle progress

Fifteen frontend tests pass, including six progress tests covering changing battery/finish estimates, proposal-versus-execution separation, driving/queue/connection/waiting phases, physical release and unloading after interruption, recorded completion, assistance and escaping. Browser verification advanced a charging vehicle from 08:00 to 08:05: battery rose from 7.3% to 15.9%, target stayed 45.4%, and remaining charge time fell from 23 to 18 minutes. The panel explicitly indicated that the simulation was paused and retained the upcoming delivery/depot timeline. Backend scheduling was unchanged.

## Approval history and time-of-day tariffs

Seventeen frontend tests pass, including regression checks that a superseded unapproved alternative cannot overlay the approved/completed bar, and that released reservations differ from never-booked options. A local HTTP plan/approve/execute replay produced the two completed sessions (₹173.91 Premium and ₹217.39 Economy) with approval evidence, alongside a separate never-approved ₹608.70 alternative. Browser checks confirmed both labels, retained expanded history after refresh, five rate bands for every station, no mobile overflow and no console errors.

New tariff tests check full-day coverage and a charging session crossing 10:00 for every scenario. Existing saved runs are intentionally not repriced; the portal explains when reset is needed to load the new tariff fixture.
