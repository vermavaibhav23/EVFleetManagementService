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
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL, node text NOT NULL UNIQUE
);
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
    parked_depot_id uuid REFERENCES cab_watch_accounts.depots,
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
                   AND conname='busy_vehicle_has_no_depot') THEN
        ALTER TABLE cab_watch_accounts.vehicles DROP CONSTRAINT IF EXISTS vehicle_parking_matches_status;
        ALTER TABLE cab_watch_accounts.vehicles ADD CONSTRAINT busy_vehicle_has_no_depot
            CHECK (status <> 'busy' OR parked_depot_id IS NULL);
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS cab_watch_accounts.trips (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
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
    -- New rides have one route. Older three-leg plans remain readable.
    IF plan - 'customer_ride' = '{}'::jsonb AND jsonb_typeof(plan->'customer_ride') = 'array' THEN
        nodes = plan->'customer_ride';
        RETURN jsonb_array_length(nodes) >= 2 AND NOT EXISTS (
            SELECT FROM jsonb_array_elements_text(nodes) AS n(node) WHERE node IS NULL OR node !~ '^[A-Z]$');
    END IF;
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
-- Older installations used direction strings. Assign one UUID per existing
-- depot and translate every parked vehicle before restoring its foreign key.
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.columns WHERE table_schema='cab_watch_accounts'
               AND table_name='depots' AND column_name='id' AND data_type='text') THEN
        LOCK TABLE cab_watch_accounts.depots, cab_watch_accounts.vehicles IN ACCESS EXCLUSIVE MODE;
        ALTER TABLE cab_watch_accounts.depots ADD COLUMN new_id uuid NOT NULL DEFAULT gen_random_uuid();
        ALTER TABLE cab_watch_accounts.vehicles
            DROP CONSTRAINT IF EXISTS vehicles_parked_depot_id_fkey,
            DROP CONSTRAINT IF EXISTS vehicles_depot_id_fkey;
        UPDATE cab_watch_accounts.vehicles v SET parked_depot_id=d.new_id::text
            FROM cab_watch_accounts.depots d WHERE v.parked_depot_id=d.id;
        ALTER TABLE cab_watch_accounts.vehicles ALTER COLUMN parked_depot_id TYPE uuid USING parked_depot_id::uuid;
        ALTER TABLE cab_watch_accounts.depots ALTER COLUMN id TYPE uuid USING new_id;
        ALTER TABLE cab_watch_accounts.depots ALTER COLUMN id SET DEFAULT gen_random_uuid(), DROP COLUMN new_id;
        ALTER TABLE cab_watch_accounts.vehicles ADD FOREIGN KEY (parked_depot_id) REFERENCES cab_watch_accounts.depots;
    END IF;
END $$;
INSERT INTO cab_watch_accounts.depots(name,node) VALUES
    ('Northwest Depot','A'), ('Northeast Depot','E'), ('Southwest Depot','V'), ('Southeast Depot','Z')
ON CONFLICT (node) DO NOTHING;
CREATE UNIQUE INDEX IF NOT EXISTS one_active_trip ON cab_watch_accounts.trips(vehicle_id) WHERE completed_at IS NULL;
CREATE INDEX IF NOT EXISTS vehicle_history ON cab_watch_accounts.trips(vehicle_id,created_at);
ALTER TABLE cab_watch_accounts.trips
    ADD COLUMN IF NOT EXISTS last_lat double precision,
    ADD COLUMN IF NOT EXISTS last_lon double precision,
    ADD COLUMN IF NOT EXISTS last_captured_at timestamptz;
-- Older releases only recorded receipt time. Preserve their final coordinates
-- without presenting that receipt time as the time the GPS point was captured.
DO $$
BEGIN
    IF to_regclass('cab_watch_accounts.gps_readings') IS NOT NULL THEN
        UPDATE cab_watch_accounts.trips t SET last_lat=g.lat, last_lon=g.lon
        FROM cab_watch_accounts.gps_readings g
        WHERE t.id=g.trip_id AND t.last_sequence=g.sequence AND t.last_lat IS NULL;
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS cab_watch_accounts.alerts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_id uuid NOT NULL REFERENCES cab_watch_accounts.trips ON DELETE CASCADE,
    opened_sequence integer NOT NULL, closed_sequence integer, peak_metres real NOT NULL,
    opened_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz
);
ALTER TABLE cab_watch_accounts.alerts DROP COLUMN IF EXISTS opening_samples;
CREATE UNIQUE INDEX IF NOT EXISTS one_open_alert ON cab_watch_accounts.alerts(trip_id) WHERE closed_sequence IS NULL;



-- Fold dispatch retry mappings into their trips. Runtime executes this migration
-- in one transaction: failed validation preserves the original table and data.
ALTER TABLE cab_watch_accounts.trips
    ADD COLUMN IF NOT EXISTS user_id uuid,
    ADD COLUMN IF NOT EXISTS idempotency_key uuid;
DO $$
BEGIN
    LOCK TABLE cab_watch_accounts.vehicles, cab_watch_accounts.trips IN ACCESS EXCLUSIVE MODE;
    UPDATE cab_watch_accounts.trips t SET user_id=v.user_id
        FROM cab_watch_accounts.vehicles v WHERE v.id=t.vehicle_id AND t.user_id IS NULL;
    IF to_regclass('cab_watch_accounts.dispatch_requests') IS NOT NULL THEN
        LOCK TABLE cab_watch_accounts.dispatch_requests IN ACCESS EXCLUSIVE MODE;
        IF EXISTS (
            SELECT FROM cab_watch_accounts.dispatch_requests r
            JOIN cab_watch_accounts.trips t ON t.id=r.trip_id
            WHERE r.user_id IS DISTINCT FROM t.user_id
               OR (t.idempotency_key IS NOT NULL AND t.idempotency_key <> r.request_key)
        ) THEN RAISE EXCEPTION 'Dispatch mappings disagree with trip ownership or retry keys'; END IF;
        UPDATE cab_watch_accounts.trips t SET idempotency_key=r.request_key
            FROM cab_watch_accounts.dispatch_requests r WHERE r.trip_id=t.id;
    END IF;
    -- Older PUT-created trips have no dispatch mapping; give each a stable key.
    UPDATE cab_watch_accounts.trips SET idempotency_key=gen_random_uuid() WHERE idempotency_key IS NULL;
    ALTER TABLE cab_watch_accounts.trips
        ALTER COLUMN user_id SET NOT NULL, ALTER COLUMN idempotency_key SET NOT NULL;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='cab_watch_accounts.vehicles'::regclass
                   AND conname='vehicles_id_user_unique') THEN
        ALTER TABLE cab_watch_accounts.vehicles ADD CONSTRAINT vehicles_id_user_unique UNIQUE(id,user_id);
    END IF;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='cab_watch_accounts.trips'::regclass
                   AND conname='trips_user_id_fkey') THEN
        ALTER TABLE cab_watch_accounts.trips ADD CONSTRAINT trips_user_id_fkey
            FOREIGN KEY(user_id) REFERENCES cab_watch_accounts.users(id);
    END IF;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='cab_watch_accounts.trips'::regclass
                   AND conname='trips_user_idempotency_unique') THEN
        ALTER TABLE cab_watch_accounts.trips ADD CONSTRAINT trips_user_idempotency_unique UNIQUE(user_id,idempotency_key);
    END IF;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='cab_watch_accounts.trips'::regclass
                   AND conname='trips_vehicle_owner_fkey') THEN
        ALTER TABLE cab_watch_accounts.trips ADD CONSTRAINT trips_vehicle_owner_fkey
            FOREIGN KEY(vehicle_id,user_id) REFERENCES cab_watch_accounts.vehicles(id,user_id) ON UPDATE CASCADE;
    END IF;
    ALTER TABLE cab_watch_accounts.trips DROP CONSTRAINT IF EXISTS trips_vehicle_id_fkey;
    DROP TABLE IF EXISTS cab_watch_accounts.dispatch_requests;
END $$;
