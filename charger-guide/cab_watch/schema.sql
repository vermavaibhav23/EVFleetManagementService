CREATE SCHEMA IF NOT EXISTS cab_watch;
CREATE TABLE IF NOT EXISTS cab_watch.depots (
    id text PRIMARY KEY,
    name text NOT NULL,
    node text NOT NULL UNIQUE
);
INSERT INTO cab_watch.depots(id,name,node) VALUES
    ('northwest','Northwest Depot','A'),
    ('northeast','Northeast Depot','E'),
    ('southwest','Southwest Depot','V'),
    ('southeast','Southeast Depot','Z')
ON CONFLICT (id) DO UPDATE SET name=excluded.name,node=excluded.node;
CREATE TABLE IF NOT EXISTS cab_watch.vehicles (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL,
    vehicle_index smallint NOT NULL,
    depot_id text NOT NULL REFERENCES cab_watch.depots(id),
    current_node text NOT NULL,
    status text NOT NULL DEFAULT 'available' CHECK (status IN ('available','busy')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(session_id,vehicle_index)
);
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
ALTER TABLE cab_watch.trips ADD COLUMN IF NOT EXISTS vehicle_id uuid REFERENCES cab_watch.vehicles(id);
ALTER TABLE cab_watch.trips ADD COLUMN IF NOT EXISTS pickup_node text;
ALTER TABLE cab_watch.trips ADD COLUMN IF NOT EXISTS dropoff_node text;
ALTER TABLE cab_watch.trips ADD COLUMN IF NOT EXISTS planned_legs jsonb;
ALTER TABLE cab_watch.trips ADD COLUMN IF NOT EXISTS phase smallint NOT NULL DEFAULT 0;
ALTER TABLE cab_watch.trips ADD COLUMN IF NOT EXISTS completed_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS cab_one_active_trip_per_vehicle ON cab_watch.trips(vehicle_id)
WHERE vehicle_id IS NOT NULL AND completed_at IS NULL;
CREATE TABLE IF NOT EXISTS cab_watch.gps_readings (
    trip_id uuid NOT NULL REFERENCES cab_watch.trips ON DELETE CASCADE,
    sequence integer NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    distance_m real NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(trip_id, sequence)
);
ALTER TABLE cab_watch.gps_readings ADD COLUMN IF NOT EXISTS leg smallint NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS cab_watch.alerts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_id uuid NOT NULL REFERENCES cab_watch.trips ON DELETE CASCADE,
    opened_sequence integer NOT NULL,
    closed_sequence integer,
    peak_metres real NOT NULL,
    opened_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
ALTER TABLE cab_watch.alerts ADD COLUMN IF NOT EXISTS opening_samples jsonb;
UPDATE cab_watch.alerts a SET opening_samples = (
    SELECT jsonb_agg(jsonb_build_object('sequence',g.sequence,'metres',round(g.distance_m::numeric))
                     ORDER BY g.sequence)
    FROM cab_watch.gps_readings g
    WHERE g.trip_id=a.trip_id AND g.sequence BETWEEN a.opened_sequence-2 AND a.opened_sequence
) WHERE a.opening_samples IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cab_one_open_alert ON cab_watch.alerts(trip_id)
WHERE closed_sequence IS NULL;
CREATE INDEX IF NOT EXISTS cab_gps_received_at ON cab_watch.gps_readings(received_at);


