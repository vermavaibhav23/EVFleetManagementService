# Verification record — 4 October 2026

Local branch: `codex/full-journey-charging`, based on `544f4c65499b55bca55694c917900525835186e3`.

Environment: Windows 11, Python 3.13.2, SciPy 1.16.3, Motor 3.6.0, PyMongo 4.9.2, FastAPI 0.115.6, Pydantic 2.11.3, pytest 8.3.2 and Ruff 0.16.9. Database checks used a separate standalone MongoDB 8.0.20 process bound to localhost, with a fresh uniquely named database per test.

## Checks

- 30 Python test cases passed in a single full run (109.21 seconds): 22 model/physics/execution/timezone tests, 5 real MongoDB/HTTP/process tests, 2 complete scenario tests, and 1 Kafka ingress replay/invalid-payload test.
- 9 Node frontend tests passed, including complete map routes, interrupted-route fallback and approval-aware readiness.
- Ruff checks and Python compilation passed; JavaScript syntax checking and Git whitespace checking passed.
- A real browser loaded the 12-vehicle scenario, ran the process-isolated planner, displayed selectable whole-journey alternatives, and approved the cheaper option. The observed example was ₹32.09 with return at 10:05, compared with an earlier-return alternative at ₹60.62.
- All four daily tariff charts and all seven ports were present, with 20 explicit price intervals and proposal/confirmed distinctions. At a 390-pixel viewport, the page had no horizontal overflow; the detailed tables and charts have their own horizontal scroll regions. Browser error logs were empty.
- The original map portal was restored and checked at desktop and 390-pixel mobile widths. All four tabs, vehicle selection, zoom/fit, complete-journey approval and charger date navigation were exercised. Map leader lines no longer intercept vehicle clicks.
- The cancellation regression distinguishes a waiting vehicle from one actually plugged in. Physical release blocks immediate replanning; interruption stops energy draw. A subsecond-tick regression verifies that a travel-triggered stranding event does not keep shifting into the future.

The database suite exercises approvals from separate OS processes, a process exit immediately after durable commit and before response, idempotent retry, reset fencing, preservation of unrelated records, restart persistence, the HTTP plan/approve/execute flow, and termination of a solver belonging to an old run.

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
