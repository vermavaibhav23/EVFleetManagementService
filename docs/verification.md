# Verification — interview v4
## Executed local checks (31 October 2026)

- Python 3.13: `python -m pytest -q --tb=short` — **79 passed, 1 skipped**, 103.31 seconds. The skipped test is the opt-in PostgreSQL/Kafka integration test; this result does not establish cloud connectivity.
- Frontend: `node --test tests/test_frontend.cjs` — **26 passed**.
- `python scripts/benchmark.py` — all six solves returned `OPTIMAL_MODEL`, with no late deliveries. Full candidate search cost: INR 699.20 across SIM-001–003; nearest-first single-station restriction: INR 949.63 (26.4% reduction on this fixed synthetic fixture).

The comparison uses MILP for both methods, with identical hard constraints. It isolates station-choice flexibility; it is not a pure greedy comparison, fleet-wide optimality claim, throughput benchmark, or production savings estimate. Reproducible per-vehicle output is in `benchmark-results.json`.

## Hosted checks

Deployment validation is pending. The public demo uses its own PostgreSQL database and Kafka topic, separate from the existing deployment.

## Optional integration test

Set `FLEET_TEST_POSTGRES`, `FLEET_TEST_KAFKA`, and `FLEET_TEST_TOPIC` to disposable test resources and run `pytest tests/test_cloud_integration.py -q`. The test uses a unique SQL schema, a precreated dedicated topic, and configured TLS/SASL settings. It exercises telemetry ingestion, persisted history, planning, approval, reservations, and stale-plan rejection after reset.
