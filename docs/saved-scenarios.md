# Saved scenarios (schema v6)

Each browser tab remembers its selected run ID in sessionStorage and sends it with every request.
`fleet_run` stores run_id, scenario, revision and data. No active flag or singleton.
Opening a saved scenario returns its ID without pausing or changing another scenario.
Reset replaces only the requested scenario. Idle runs pause after five minutes without views.

See [every table and field](schema-fields.md) for the complete schema and examples.
The database retains IDs, relations, booking intervals and delivery tracking. Flexible nested
vehicle/plan details remain JSONB. API request JSON carries commands, not the durable database.

Migration covers fresh databases, legacy v4 and saved-scenario v5, and is repeatable.
Tests cover separate API selections, independent mutations, stale writes, scenario reset,
old and duplicate telemetry, preserved bookings and removed redundant fields.

[Updated Excalidraw HLD and complete schema](https://excalidraw.com/#json=9k3pOeGHeRX4v5rrWbT8w,nJALdkNVnK9JBSx383vpgw). This is a read-only snapshot.

Verified live on schema v6: all three saved run IDs retained; four vehicles per scenario;
booked intervals preserved; all twelve vehicles receiving LIVE Kafka telemetry.
Dashboard Open saved scenario tested after reload.
Local checks: 91 Python tests, 27 frontend tests, three real PostgreSQL migration/API
cases (fresh, v4, v5); Ruff passes. The Kafka integration test needs its dedicated test service
and was not run locally; the hosted pipeline was checked instead.
