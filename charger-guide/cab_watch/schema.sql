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
-- A single sequence supplies synthetic plates across all accounts and retired cabs.
CREATE SEQUENCE IF NOT EXISTS cab_watch_accounts.plate_serial
    MINVALUE 1 MAXVALUE 669173076 START WITH 11233 NO CYCLE;
CREATE OR REPLACE FUNCTION cab_watch_accounts.next_plate() RETURNS text
LANGUAGE sql VOLATILE AS $$
    SELECT 'KA' || lpad((1+n/(676*9999))::text,2,'0')
           || chr(65+(n/9999/26)%26) || chr(65+(n/9999)%26)
           || lpad((1+n%9999)::text,4,'0')
    FROM (SELECT nextval('cab_watch_accounts.plate_serial')::integer-1 AS n) serial
$$;
CREATE TABLE IF NOT EXISTS cab_watch_accounts.vehicles (
    id text PRIMARY KEY DEFAULT cab_watch_accounts.next_plate()
        CONSTRAINT vehicle_plate_format CHECK(id ~ '^[A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{4}$'),
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
    vehicle_id text NOT NULL REFERENCES cab_watch_accounts.vehicles ON UPDATE CASCADE,
    planned_legs jsonb NOT NULL,
    phase smallint NOT NULL DEFAULT 0,
    last_sequence integer NOT NULL DEFAULT 0,
    distance_m real NOT NULL DEFAULT 0,
    motion jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
-- CabStream runs this entire schema under one transaction and advisory lock.
-- Move existing FK values with their parent so trip, GPS and alert IDs stay valid.
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.columns WHERE table_schema='cab_watch_accounts'
               AND table_name='vehicles' AND column_name='id' AND data_type='uuid') THEN
        LOCK TABLE cab_watch_accounts.vehicles, cab_watch_accounts.trips IN ACCESS EXCLUSIVE MODE;
        ALTER TABLE cab_watch_accounts.trips DROP CONSTRAINT trips_vehicle_id_fkey;
        ALTER TABLE cab_watch_accounts.vehicles ALTER COLUMN id TYPE text USING id::text;
        ALTER TABLE cab_watch_accounts.trips ALTER COLUMN vehicle_id TYPE text USING vehicle_id::text;
        ALTER TABLE cab_watch_accounts.trips ADD CONSTRAINT trips_vehicle_id_fkey
            FOREIGN KEY(vehicle_id) REFERENCES cab_watch_accounts.vehicles(id) ON UPDATE CASCADE;
        ALTER TABLE cab_watch_accounts.vehicles ALTER COLUMN id SET DEFAULT cab_watch_accounts.next_plate();
        UPDATE cab_watch_accounts.vehicles SET id=DEFAULT;
        ALTER TABLE cab_watch_accounts.vehicles ADD CONSTRAINT vehicle_plate_format
            CHECK(id ~ '^[A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{4}$');
    END IF;
END $$;
-- Validate route structure before discarding the redundant endpoint columns.
CREATE OR REPLACE FUNCTION cab_watch_accounts.valid_trip_plan(plan jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE
    leg_name text;
    nodes jsonb;
BEGIN
    IF jsonb_typeof(plan) <> 'object' THEN RETURN false; END IF;
    IF plan - ARRAY['to_pickup','customer_ride','to_depot'] <> '{}'::jsonb THEN RETURN false; END IF;
    FOREACH leg_name IN ARRAY ARRAY['to_pickup','customer_ride','to_depot'] LOOP
        nodes := plan -> leg_name;
        IF jsonb_typeof(nodes) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(nodes) = 0 OR EXISTS (
            SELECT FROM jsonb_array_elements_text(nodes) AS n(node)
            WHERE node IS NULL OR node !~ '^[A-Z]$'
        ) THEN RETURN false; END IF;
    END LOOP;
    RETURN plan #>> '{to_pickup,-1}' = plan #>> '{customer_ride,0}'
       AND plan #>> '{customer_ride,-1}' = plan #>> '{to_depot,0}';
END $$;
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.columns WHERE table_schema='cab_watch_accounts'
               AND table_name='trips' AND column_name='pickup_node') THEN
        LOCK TABLE cab_watch_accounts.trips IN ACCESS EXCLUSIVE MODE;
        IF EXISTS (
            SELECT FROM cab_watch_accounts.trips t
            JOIN cab_watch_accounts.depots start_depot ON start_depot.id=t.departure_depot_id
            JOIN cab_watch_accounts.depots end_depot ON end_depot.id=t.arrival_depot_id
            WHERE jsonb_typeof(planned_legs) <> 'array' OR jsonb_array_length(planned_legs) <> 3
               OR planned_legs #>> '{0,0}' IS DISTINCT FROM start_depot.node
               OR planned_legs #>> '{1,0}' IS DISTINCT FROM pickup_node
               OR planned_legs #>> '{1,-1}' IS DISTINCT FROM dropoff_node
               OR planned_legs #>> '{2,-1}' IS DISTINCT FROM end_depot.node
        ) THEN RAISE EXCEPTION 'Stored trip endpoints disagree with planned legs'; END IF;
        UPDATE cab_watch_accounts.trips SET planned_legs=jsonb_build_object(
            'to_pickup', planned_legs->0, 'customer_ride', planned_legs->1, 'to_depot', planned_legs->2);
        ALTER TABLE cab_watch_accounts.trips DROP COLUMN departure_depot_id, DROP COLUMN arrival_depot_id,
            DROP COLUMN pickup_node, DROP COLUMN dropoff_node;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='cab_watch_accounts.trips'::regclass
                   AND conname='valid_named_legs') THEN
        ALTER TABLE cab_watch_accounts.trips ADD CONSTRAINT valid_named_legs
            CHECK (cab_watch_accounts.valid_trip_plan(planned_legs));
    END IF;
END $$;
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
    opened_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz
);
ALTER TABLE cab_watch_accounts.alerts DROP COLUMN IF EXISTS opening_samples;
CREATE UNIQUE INDEX IF NOT EXISTS one_open_alert ON cab_watch_accounts.alerts(trip_id) WHERE closed_sequence IS NULL;
CREATE INDEX IF NOT EXISTS gps_received_at ON cab_watch_accounts.gps_readings(received_at);

