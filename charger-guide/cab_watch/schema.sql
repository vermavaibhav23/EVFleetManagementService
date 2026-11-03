-- Separate namespace keeps anonymous historical records intact and inaccessible to accounts.
CREATE SCHEMA IF NOT EXISTS cab_watch_accounts;
CREATE TABLE IF NOT EXISTS cab_watch_accounts.users (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    jwt_key bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cab_watch_accounts.depots (
    id text PRIMARY KEY, name text NOT NULL, node text NOT NULL UNIQUE
);
INSERT INTO cab_watch_accounts.depots(id,name,node) VALUES
    ('northwest','Northwest Depot','A'), ('northeast','Northeast Depot','E'),
    ('southwest','Southwest Depot','V'), ('southeast','Southeast Depot','Z')
ON CONFLICT (id) DO UPDATE SET name=excluded.name,node=excluded.node;
CREATE TABLE IF NOT EXISTS cab_watch_accounts.vehicles (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES cab_watch_accounts.users,
    cab_number integer NOT NULL CHECK(cab_number>0),
    parked_depot_id text REFERENCES cab_watch_accounts.depots,
    status text NOT NULL DEFAULT 'available' CHECK(status IN ('available','busy')),
    created_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz,
    UNIQUE(user_id,cab_number)
);
-- Upgrade previous account fleets without changing vehicle IDs or trip history.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='cab_watch_accounts' AND table_name='vehicles' AND column_name='depot_id') THEN
        ALTER TABLE cab_watch_accounts.vehicles RENAME COLUMN depot_id TO parked_depot_id;
        ALTER TABLE cab_watch_accounts.vehicles ALTER COLUMN parked_depot_id DROP NOT NULL;
        UPDATE cab_watch_accounts.vehicles SET parked_depot_id=NULL WHERE status='busy';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='cab_watch_accounts.vehicles'::regclass
                   AND conname='vehicle_parking_matches_status') THEN
        ALTER TABLE cab_watch_accounts.vehicles ADD CONSTRAINT vehicle_parking_matches_status
            CHECK ((status='available') = (parked_depot_id IS NOT NULL));
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS cab_watch_accounts.trips (
    id uuid PRIMARY KEY,
    vehicle_id uuid NOT NULL REFERENCES cab_watch_accounts.vehicles,
    departure_depot_id text NOT NULL REFERENCES cab_watch_accounts.depots,
    arrival_depot_id text NOT NULL REFERENCES cab_watch_accounts.depots,
    pickup_node text NOT NULL, dropoff_node text NOT NULL,
    planned_legs jsonb NOT NULL,
    phase smallint NOT NULL DEFAULT 0,
    last_sequence integer NOT NULL DEFAULT 0,
    distance_m real NOT NULL DEFAULT 0,
    motion jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_trip ON cab_watch_accounts.trips(vehicle_id) WHERE completed_at IS NULL;
CREATE INDEX IF NOT EXISTS vehicle_history ON cab_watch_accounts.trips(vehicle_id,created_at);
CREATE TABLE IF NOT EXISTS cab_watch_accounts.gps_readings (
    trip_id uuid NOT NULL REFERENCES cab_watch_accounts.trips ON DELETE CASCADE,
    sequence integer NOT NULL, leg smallint NOT NULL, lat double precision NOT NULL, lon double precision NOT NULL,
    distance_m real NOT NULL, received_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(trip_id,sequence)
);
CREATE TABLE IF NOT EXISTS cab_watch_accounts.alerts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_id uuid NOT NULL REFERENCES cab_watch_accounts.trips ON DELETE CASCADE,
    opened_sequence integer NOT NULL, closed_sequence integer, peak_metres real NOT NULL,
    opened_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz, opening_samples jsonb NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_alert ON cab_watch_accounts.alerts(trip_id) WHERE closed_sequence IS NULL;
CREATE INDEX IF NOT EXISTS gps_received_at ON cab_watch_accounts.gps_readings(received_at);

