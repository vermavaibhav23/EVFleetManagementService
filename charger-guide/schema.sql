CREATE SCHEMA IF NOT EXISTS charger_guide;
CREATE TABLE IF NOT EXISTS charger_guide.vehicles (
    id text PRIMARY KEY, connector text NOT NULL, capacity_kwh real NOT NULL,
    kwh_per_km real NOT NULL, battery_kwh real NOT NULL, road_km real NOT NULL,
    last_sequence bigint NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS charger_guide.chargers (
    id text PRIMARY KEY, connector text NOT NULL, road_km real NOT NULL,
    online boolean NOT NULL, free_ports integer NOT NULL CHECK (free_ports >= 0)
);
CREATE TABLE IF NOT EXISTS charger_guide.alerts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vehicle_id text NOT NULL REFERENCES charger_guide.vehicles,
    charger_id text REFERENCES charger_guide.chargers, reason text NOT NULL,
    opened_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_alert ON charger_guide.alerts(vehicle_id)
WHERE resolved_at IS NULL;
CREATE SEQUENCE IF NOT EXISTS charger_guide.demo_reading;
INSERT INTO charger_guide.vehicles VALUES
('Van-A','CCS2',40,0.25,26,0,0), ('Van-B','CCS2',40,0.25,24,12,0)
ON CONFLICT DO NOTHING;
INSERT INTO charger_guide.chargers VALUES
('West','CCS2',4,true,0), ('Central','CCS2',10,true,1),
('East','CCS2',30,true,2), ('North','Type2',2,true,2)
ON CONFLICT DO NOTHING;
