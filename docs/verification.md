# Verification status — version 3

## Reserve restoration and complete charger review

On 5 October 2026, the full local suite passed **84 Python tests, 1 cloud-only skip** in 110.88 seconds and **26 frontend tests**. Follow-up safety/scenario checks passed **25 tests** in 66.15 seconds after excluding powerless stations from candidate bounds. Ruff passed. Coverage includes limited initial reserve use, restoration at subsequent stops and depot, required acknowledgement, full simulated execution, external telemetry respecting only the initial exception, and refusing a physically unreachable charger. Charger inventory checks cover failed/incompatible/powerless/out-of-bound stations, actual booking windows, rejected/stale options and pruned job history. Frontend tests check visible grey charger cards, escaped reasons and safe default selection.

A read-only replay of the actual live disrupted Van B1 snapshot produced an on-time **₹298.04** reserve-restoring journey ending with **3 kWh**, with no production booking or mutation. The cloud test now additionally exercises combined North/East failures, initial-exception approval and reserve-preserving completion; its execution is verified separately from these local results.

The expanded real PostgreSQL/Kafka/MongoDB integration test passed on Railway for commit `9eefebf`: **1 passed in 26.07 seconds** (deployment `de1f91fc-1fb3-4a80-a31e-8effc5d01466`). It confirmed all charger rows through the API, rejected initial-exception approval without acknowledgement, and completed the acknowledged journey through real telemetry with at least 3 kWh remaining. The previously recorded Pydantic alias warning remains. No live user scenario was reset for this test.

## Deadline-versus-battery comparison correction

On 5 October 2026, the full Python suite passed **77 tests, 1 cloud-only skip** in 117.95 seconds, and the frontend suite passed **24 tests**. After adding an additional sole-escape-route check, all **7 focused trade-off tests** passed in 7.43 seconds; Ruff passed. These cover solver-noise duplicates, a genuinely earlier ordinary alternative, a real deadline rescued using emergency reserve, already-missed deadlines, not worsening another delivery, and refusing a route that reaches its first customer but strands the vehicle later. A physically feasible reduced-reserve escape with no normal-reserve route is labelled as an alternative, not an invented two-way choice. These are local regression results; the earlier cloud integration run below remains separate evidence.

Local checks completed on 5 October 2026 (IST):

- Python regression suite: **73 passed, 1 skipped** in 103.82 seconds. It covers the preserved mathematical/execution/scenario rules plus telemetry ordering, duplicate fencing, old-run/control-version rejection, freshness, heartbeat stability, independently applied simulator progress, pending planning deduplication and history upserts.
- Frontend: **23 tests passed**. Existing map, review, progress, slot/status and deadline behaviors remain covered.
- Python modules compile; Ruff checks are run on app/tests/scripts.
- No Docker containers or local PostgreSQL/Kafka/MongoDB services were launched for these checks. Solver processes use one CPU thread.

## Cloud integration and live deployment

The real PostgreSQL + Kafka + MongoDB integration test **passed inside Railway** on 5 October 2026: the expanded check completed in **22.55 seconds**. It covered HTTP ingestion, Kafka consumption, SQL state, Mongo history, automatic planning, approval, simulated completion, revision conflicts, reset fencing, all four scenario snapshots, Premium/Economy split charging and physical release after an East charger failure. The independent comparison vehicle retained its approved journey. Test-generated schemas, topics and history databases were cleaned up.

The implementation was pushed to GitHub `main` and deployed to the existing Railway portal. PostgreSQL is now the operational authority; the existing Kafka and MongoDB services are connected. A live configuration mismatch between the image's port 8000 and Railway's assigned port was found and corrected with explicit `PORT=8000`, matching the existing public domain target. The image now also honors `PORT`. Railway's dashboard has an explicit `/api/v1/health/ready` probe; the unused `railway.toml` was removed. The API was moved beside PostgreSQL to reduce database round trips.

Live portal verification confirmed all four scenario choices and the fleet-size control; automatic options; a manager approval reserving exactly two slots for ₹391.30; and a five-minute step advancing Van A1 to **CHARGING**, **5.38 kWh**, through live telemetry. All three state consumers, history, planning, provider and simulation workers reported **OK**. The map and selected vehicle progress panel retain their existing structure. The demo remains paused for the manager.

The cloud test emitted one Pydantic/FastAPI schema warning about the legacy `vin` validation alias; the canonical `vehicle_id` telemetry path passed. This is recorded rather than hidden. No 100k-events/sec capacity or zero-defect claim is made. Single-broker loss, extended infrastructure outages, and large-fleet throughput are not covered by this integration run.

The opt-in cloud test is implemented in `tests/test_cloud_integration.py`. It isolates its PostgreSQL schema, topics, consumer groups and MongoDB database, drives the real HTTP/Kafka/state/history/planning/approval/execution flow and cleans up only its generated test names. It never uses or resets a live simulation.

Old Mongo-only benchmark results were removed because they do not measure this architecture. The in-memory mathematical benchmark remains available in `scripts/benchmark.py`; its results must not be presented as distributed-system throughput.
