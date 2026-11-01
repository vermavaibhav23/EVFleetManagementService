# Saved scenarios (schema v5)

Each loaded scenario has one row in `fleet_run`. `run_id` identifies that saved simulation; `scenario` identifies its preset. Related tables use `run_id` to keep each scenario's vehicles, plans and booked intervals separate. The `singleton` column is removed.

Choose a scenario and use **Load / reset** to create or replace its run. Use **Open saved scenario** to return to it without losing progress. Switching pauses the previous run and cancels unfinished searches. Only the selected scenario advances in this shared dashboard. This is not multi-tenant user isolation.

## Verification — 1 November 2026

- Existing Python suite: 91 passed, 2 skipped (external integration resources not enabled for that invocation).
- Frontend suite: 27 passed.
- Real PostgreSQL 18 regression: 2 passed, covering both a fresh schema and migration from v4 with existing rows.
- Verified three saved scenarios, repeated vehicle IDs, preserved data on migration, switch/reopen, cancelled searches, scenario-specific reset, unchanged bookings in another run, inactive-run telemetry delivery, rejection of stale commits and old-run events, and repeatable startup migration.
- Ruff and `git diff --check` pass.

The real PostgreSQL tests use disposable schemas and can run with `FLEET_TEST_POSTGRES=... python -m pytest tests/test_saved_runs.py -q`. Kafka was not required for these database tests; the live broker path is checked separately after deployment.

## Hosted verification

Commit `feb137c` was deployed to Render. `/health/live` reports schema version 5 and `/health/ready` returns HTTP 200. All three scenarios were loaded and each received real Kafka telemetry. Switching back to the original Everyday Choices run preserved its run ID, simulation clock, plan IDs and reservation count. The original scenario is selected and paused. The database now contains three saved scenario rows.

[Updated Excalidraw schema snapshot](https://excalidraw.com/#json=RIkcnEfYYFPMYf4Z6L0ZY,qy7SwfWoiqSTupc41uhFow) replaces the earlier singleton diagram. This link is a snapshot and does not automatically reflect future canvas edits.
