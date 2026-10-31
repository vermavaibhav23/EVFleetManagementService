# Verification — interview v4
## Executed local checks (31 October 2026)

- Python 3.13: `python -m pytest -q --tb=short` — **79 passed, 1 skipped**, 103.31 seconds. The skipped test is the opt-in PostgreSQL/Kafka integration test; this result does not establish cloud connectivity.
- Frontend: `node --test tests/test_frontend.cjs` — **26 passed**.
- `ruff check app tests scripts`, `ruff format --check app tests scripts`, and `git diff --check` — passed.
- `python scripts/benchmark.py` — all six solves returned `OPTIMAL_MODEL`, with no late deliveries. Full candidate search cost: INR 699.20 across SIM-001–003; nearest-first single-station restriction: INR 949.63 (26.4% reduction on this fixed synthetic fixture).

The comparison uses MILP for both methods, with identical hard constraints. It isolates station-choice flexibility; it is not a pure greedy comparison, fleet-wide optimality claim, throughput benchmark, or production savings estimate. Reproducible per-vehicle output is in `benchmark-results.json`.

## Hosted checks

Executed against [the Render demo](https://ev-fleet-interview.onrender.com/portal) on 31 October 2026, deploying code commit `9982bb7`:

- Render reports **Deploy succeeded | Live**, using Docker on Free (512 MB, 0.1 CPU). Aiven PostgreSQL and Kafka both report Running on Free plans.
- `/api/v1/health/ready` returns HTTP 200, PostgreSQL authority, and telemetry/planning/provider/simulation workers `OK`.
- All three public scenarios load with four vehicles and live telemetry; their observed planning jobs complete without worker errors.
- Everyday Van A1 produces the expected INR 391.30 plan. Browser approval succeeds and reports both slots booked together.
- A read-only Aiven SQL query confirms **40 telemetry history rows and 2 reservations for SIM-001** at the observation time.
- Manual clock advancement passes through Kafka and moves the approved van into `CHARGING`.
- Resetting the scenario makes a previous-run approval return HTTP 409.
- The dashboard renders correctly and the everyday scenario is restored, paused, for review.

The first approval encountered concurrent fleet updates and returned HTTP 409 with no partial booking; retry succeeded. A manual step similarly waited for outstanding telemetry to catch up. These are safe retry conditions, and their latency is visible on the free tier. Solver results may be feasible rather than proven optimal when the time limit is reached. This was a functional smoke test, not a load or long-duration stability test.

The opt-in local integration test remains skipped; hosted browser/API checks and the direct SQL observation above are separate evidence. The public demo uses its own PostgreSQL database and Kafka topic, separate from the existing deployment.

## Optional integration test

Set `FLEET_TEST_POSTGRES`, `FLEET_TEST_KAFKA`, and `FLEET_TEST_TOPIC` to disposable test resources and run `pytest tests/test_cloud_integration.py -q`. The test uses a unique SQL schema, a precreated dedicated topic, and configured TLS/SASL settings. It exercises telemetry ingestion, persisted history, planning, approval, reservations, and stale-plan rejection after reset.
