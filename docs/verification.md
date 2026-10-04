# Verification status — version 3

Local checks completed on 5 October 2026 (IST):

- Python regression suite: **73 passed, 1 skipped** in 103.82 seconds. It covers the preserved mathematical/execution/scenario rules plus telemetry ordering, duplicate fencing, old-run/control-version rejection, freshness, heartbeat stability, independently applied simulator progress, pending planning deduplication and history upserts.
- Frontend: **23 tests passed**. Existing map, review, progress, slot/status and deadline behaviors remain covered.
- Python modules compile; Ruff checks are run on app/tests/scripts.
- No Docker containers or local PostgreSQL/Kafka/MongoDB services were launched for these checks. Solver processes use one CPU thread.

## Not yet verified

The real PostgreSQL + Kafka + MongoDB integration test is **skipped** until disposable cloud test connections are provided in `FLEET_TEST_POSTGRES`, `FLEET_TEST_KAFKA`, and `FLEET_TEST_MONGO`. A unit-test pass is not evidence that the SQL schema, broker permissions, cloud networking or Railway environment have been tested together.

The new branch has not replaced the current Railway deployment. Production cutover requires PostgreSQL and Kafka provisioning/configuration, staging integration checks and verification through the existing portal. The previous live app remains MongoDB-backed until then. No 100k-events/sec capacity or zero-defect claim is made.

The opt-in cloud test is implemented in `tests/test_cloud_integration.py`. It isolates its PostgreSQL schema, topics, consumer groups and MongoDB database, drives the real HTTP/Kafka/state/history/planning/approval/execution flow and cleans up only its generated test names. It never uses or resets a live simulation.

Old Mongo-only benchmark results were removed because they do not measure this architecture. The in-memory mathematical benchmark remains available in `scripts/benchmark.py`; its results must not be presented as distributed-system throughput.
