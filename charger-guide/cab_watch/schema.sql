CREATE SCHEMA IF NOT EXISTS cab_watch;
CREATE TABLE IF NOT EXISTS cab_watch.trips (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL,
    route_index smallint NOT NULL,
    last_sequence integer NOT NULL DEFAULT 0,
    off_count integer NOT NULL DEFAULT 0,
    distance_m real NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(session_id, route_index)
);
ALTER TABLE cab_watch.trips ADD COLUMN IF NOT EXISTS planned_nodes text[];
CREATE TABLE IF NOT EXISTS cab_watch.gps_readings (
    trip_id uuid NOT NULL REFERENCES cab_watch.trips ON DELETE CASCADE,
    sequence integer NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    distance_m real NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(trip_id, sequence)
);
CREATE TABLE IF NOT EXISTS cab_watch.alerts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_id uuid NOT NULL REFERENCES cab_watch.trips ON DELETE CASCADE,
    opened_sequence integer NOT NULL,
    closed_sequence integer,
    peak_metres real NOT NULL,
    opened_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS cab_one_open_alert ON cab_watch.alerts(trip_id)
WHERE closed_sequence IS NULL;

