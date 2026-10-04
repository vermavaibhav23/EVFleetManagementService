-- Bounded single-fleet authority. Every operational transaction locks fleet_run.
CREATE TABLE IF NOT EXISTS fleet_run (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    run_id text NOT NULL UNIQUE,
    revision bigint NOT NULL,
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS vehicles (
    id text PRIMARY KEY, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS journey_plans (
    id text PRIMARY KEY, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS planning_jobs (
    id text PRIMARY KEY, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS stations (
    id text PRIMARY KEY, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS depots (
    id text PRIMARY KEY, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
    id text PRIMARY KEY, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    vehicle_id text NOT NULL, charger_id text NOT NULL, port integer NOT NULL,
    starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
    data jsonb NOT NULL, CHECK (ends_at >= starts_at)
);
CREATE INDEX IF NOT EXISTS reservations_port_time ON reservations(charger_id, port, starts_at, ends_at);
CREATE TABLE IF NOT EXISTS simulator_state (
    run_id text PRIMARY KEY REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    data jsonb NOT NULL
);
-- Durable delivery buffers, not replacements for Kafka consumer groups.
CREATE TABLE IF NOT EXISTS outbox (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('TELEMETRY', 'PLANNING')),
    message_key text NOT NULL,
    payload jsonb NOT NULL,
    checkpoint jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(kind, created_at) WHERE sent_at IS NULL;
CREATE TABLE IF NOT EXISTS rejected_events (
    id text PRIMARY KEY, reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS telemetry_receipts (
    event_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    body_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY);
INSERT INTO schema_version VALUES (3) ON CONFLICT DO NOTHING;
