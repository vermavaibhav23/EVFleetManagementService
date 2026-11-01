-- One saved run per scenario. The dashboard selects one active run.
CREATE TABLE IF NOT EXISTS fleet_run (
    run_id text PRIMARY KEY UNIQUE,
    scenario text NOT NULL UNIQUE,
    active boolean NOT NULL DEFAULT false,
    revision bigint NOT NULL,
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS vehicles (
    id text NOT NULL, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    PRIMARY KEY(run_id,id),
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS journey_plans (
    id text NOT NULL, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    PRIMARY KEY(run_id,id),
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS planning_jobs (
    id text NOT NULL, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    PRIMARY KEY(run_id,id),
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS stations (
    id text NOT NULL, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    PRIMARY KEY(run_id,id),
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS depots (
    id text NOT NULL, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    PRIMARY KEY(run_id,id),
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
    id text NOT NULL, run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    PRIMARY KEY(run_id,id),
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
    kind text NOT NULL CHECK (kind = 'TELEMETRY'),
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

CREATE TABLE IF NOT EXISTS telemetry_history (
    run_id text NOT NULL REFERENCES fleet_run(run_id) ON DELETE CASCADE,
    event_id text NOT NULL, vehicle_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(), data jsonb NOT NULL,
    PRIMARY KEY(run_id, event_id)
);
CREATE INDEX IF NOT EXISTS telemetry_history_received ON telemetry_history(received_at);
INSERT INTO schema_version VALUES (4) ON CONFLICT DO NOTHING;

-- Migrate existing v4 databases without deleting any saved fleet data.
DO $$
DECLARE t text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version=5) THEN
    ALTER TABLE fleet_run ADD COLUMN IF NOT EXISTS scenario text;
    ALTER TABLE fleet_run ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
    UPDATE fleet_run SET scenario=data->>'scenario' WHERE scenario IS NULL;
    ALTER TABLE fleet_run ALTER COLUMN scenario SET NOT NULL;
    ALTER TABLE fleet_run ALTER COLUMN active SET DEFAULT false;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema()
               AND table_name='fleet_run' AND column_name='singleton') THEN
      ALTER TABLE fleet_run DROP COLUMN singleton;
      ALTER TABLE fleet_run ADD PRIMARY KEY(run_id);
    END IF;
    FOREACH t IN ARRAY ARRAY['vehicles','journey_plans','planning_jobs','stations','depots','reservations'] LOOP
      EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', t, t || '_pkey');
      EXECUTE format('ALTER TABLE %I ADD PRIMARY KEY(run_id,id)', t);
    END LOOP;
    ALTER TABLE telemetry_receipts DROP CONSTRAINT telemetry_receipts_pkey;
    ALTER TABLE telemetry_receipts ADD PRIMARY KEY(run_id,event_id);
    INSERT INTO schema_version VALUES(5);
  END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS fleet_run_scenario ON fleet_run(scenario);
CREATE UNIQUE INDEX IF NOT EXISTS fleet_run_active ON fleet_run(active) WHERE active;
CREATE INDEX IF NOT EXISTS reservations_run_port_time ON reservations(run_id,charger_id,port,starts_at,ends_at);
