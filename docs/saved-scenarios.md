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
