-- Convert checkpoints written by releases that still simulated depot journeys.
-- After conversion the legacy marker is gone, so restarts do nothing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM cab_watch_accounts.trips WHERE motion ? 'legacy') THEN
        RETURN;
    END IF;

    UPDATE cab_watch_accounts.alerts a
       SET closed_sequence=t.last_sequence, resolved_at=now()
      FROM cab_watch_accounts.trips t
     WHERE a.trip_id=t.id AND a.closed_sequence IS NULL AND t.completed_at IS NULL
       AND t.motion->'legacy'->>'phase' IN ('0','2');

    WITH finished AS (
        UPDATE cab_watch_accounts.trips
           SET completed_at=now()
         WHERE completed_at IS NULL AND motion->'legacy'->>'phase'='2'
        RETURNING vehicle_id
    )
    UPDATE cab_watch_accounts.vehicles SET status='available'
     WHERE id IN (SELECT vehicle_id FROM finished);

    -- A cab still approaching pickup starts at pickup on its next simulation tick.
    -- Preserve its last_sequence so a retry cannot rewind the trip.
    UPDATE cab_watch_accounts.trips
       SET last_lat=NULL, last_lon=NULL, last_captured_at=NULL,
           motion='{"mode":"route","index":0}'::jsonb
     WHERE completed_at IS NULL AND motion->'legacy'->>'phase'='0';

    UPDATE cab_watch_accounts.trips
       SET motion=CASE WHEN completed_at IS NULL THEN nullif(motion-'legacy','{}'::jsonb) END
     WHERE motion ? 'legacy';
END $$;
