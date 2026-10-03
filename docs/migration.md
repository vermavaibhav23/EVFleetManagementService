# Schema 2 transition and cleanup

The old recursive target-SoC scheduler, next-trip readiness engine, mutable-departure ordering, primary/follow-up plan chain, independent reservation store, global Python serialization lock, rolling delayed-deadline policy, old scenario catalog and old manager snapshot are removed. Their models, API modules, Redis cache/configuration and demo/live-write scripts are also removed. The shared charging-curve implementation remains and is used by pricing and execution.

`app/domain.py` contains the current contracts. `optimizer.py` and `milp.py` own bounded mathematical planning; `validation.py` independently checks complete candidates. `ledger.py` is the atomic persistence boundary; `control.py`, `execution.py`, `runner.py` and optional `ingestion.py` are its callers. Every API mutation uses that boundary. Separate manual reservation mutation endpoints are retired; only complete approved journeys and explicitly seeded site occupation participate in new simulation bookings.

The original four-tab dashboard, styling and SVG fleet map are retained. Its controller now consumes the new full-run and day-view APIs; route lines follow complete remaining journeys rather than a primary/follow-up charging pair. Daily price and port charts live in Chargers, while approval and recovery live in Plans & Decisions. Removed fields include old readiness thresholds, range-margin flags, advisory charging targets, mutable trip departure ordering, single-session approval state, implicit follow-up dictionaries and old reservation-lock fields. Nameplate capacity, SoH, connector, charge-rate/taper/efficiency, service timing, immutable deadlines and source telemetry sequence remain because they affect current behavior.

Existing MongoDB collections are **not deleted or silently converted**. Legacy plans cannot be losslessly converted: some contain only a primary charge or ambiguous follow-ups and lack fixed customer order. New simulations create their own schema-2 document in `fleet_ledger`. Reset replaces only that document. Unrelated vehicles and source records stay intact, verified by a real-MongoDB test. To switch an existing deployment, stop old writers, back up the database, deploy the new code and load a new run; do not treat old reservations as approved v2 journeys. Historical source-data import requires explicit mapping of customer sequence, deadlines, service, connectors and depot endpoints.

Old tests coupled to removed models/routes/scenario names were replaced with behavioral tests rather than retained as unusable imports. Coverage maps as follows:

| Retired area | Replacement checks |
|---|---|
| Scheduler / readiness | Analytical ₹710 fixture, independent enumeration, physical reserve/health rules, fixed order, complete return |
| Primary/follow-up approval | Whole-chain CAS, multi-process contention, crash-after-commit retry, exact reviewed alternative |
| Journey tracker / timetable | Tick-size invariance, service preservation, full-day completion, charging before/after customers |
| Reservation expiry / power | Tight-gap rejection, interruption and physical release, capacity validation |
| Telemetry replay / alerts | Run and sequence fencing, stale proposal rejection, persisted incidents and event outcomes |
| Old scenario catalog | Exactly two deterministic scenarios, 12 covered edge cases, end-to-end execution |
| Frontend helpers | IST minute placement, escaping, all-port charts and status distinctions; browser review of full journeys |
| Mongo timezone regression | BSON timezone-aware round trip, plus real database API tests |

The repository contains no scripts that automatically load or mutate a public deployment. The benchmark is in-memory. Database integration tests require an explicit disposable URI and create uniquely named databases.
