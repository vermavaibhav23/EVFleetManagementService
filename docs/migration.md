# Version 3 migration

The operational authority moves from MongoDB's `fleet_ledger` document to PostgreSQL. Existing MongoDB data is not deleted or automatically imported. Deploy into a separate staging service first; configure PostgreSQL and Kafka before changing the live service. A Mongo-only v2 service cannot run v3.

1. Preserve the existing service and MongoDB database for rollback.
2. Configure `DATABASE_URL`, `KAFKA_BOOTSTRAP_SERVERS`, broker TLS/SASL if required, and history `MONGODB_URI`/`MONGODB_DB`. Topic creation permission is required initially. The demo uses three telemetry partitions and one planning partition, with replication factor one.
3. Startup creates the additive PostgreSQL schema under a migration lock. Readiness checks PostgreSQL and Kafka metadata. MongoDB history may be unavailable while operational processing continues; its backlog must be consumed within Kafka retention.
4. Load / reset a fresh v3 scenario. The four scenarios, seed, fleet size, map, tariffs, incident controls and deterministic execution rules remain. Old MongoDB reservations are not imported as approved v3 bookings.
5. Verify readings, automatic planning, approval, booking, progress, an incident and replacement approval. Run the cloud integration test on disposable resources before production cutover.

Removed: MongoDB scheduling writer, direct HTTP-to-ledger telemetry mutation, optional legacy Kafka ingress, Mongo job polling runner, local Docker Compose setup and tests coupled to those implementations. Replacement tests target telemetry fencing/freshness, independent simulation state, Kafka-triggered planning and PostgreSQL persistence. Pure optimiser, execution, scenario and frontend tests remain.

No Docker services are started on the developer laptop. Railway can build the supplied Dockerfile in the cloud. No client API key or multitenancy is introduced. Broker/database connection credentials remain necessary.

Reset replaces only this application's PostgreSQL active run and dependent rows. MongoDB history remains grouped by run and expires through TTL. Kafka retains old-run messages until retention; operational consumers fence them out, while history may still archive them.

PostgreSQL stores separate rows for vehicles, plans, jobs and resources with JSON bodies preserving the validated planner contract. Run relationships use foreign keys; reservations additionally expose station, port and times. The shared run transaction lock is deliberately retained for this bounded, single-fleet demo. This is not a 100k-events/sec deployment.
