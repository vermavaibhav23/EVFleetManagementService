# Database field guide — v6

One saved row per scenario. Each browser tab remembers its own run ID. GET requests
send `?run_id=R42`; changes send `{"run_id":"R42", ...}`. The server validates it.
JSON in a request identifies what to work on; PostgreSQL still stores the durable result.
There is no global selected run and no tenant/authentication system.

| Table | Every SQL field and purpose |
|---|---|
| fleet_run | `run_id`: saved simulation identity; `scenario`: unique preset name; `revision`: change counter to reject stale writes; `data`: JSONB clock, running flag, seed, policy, events and scenario metadata |
| vehicles | `run_id`: owning scenario run; `id`: vehicle identity within it; `data`: JSONB battery, position, deliveries, connector, execution and telemetry state |
| stations | `run_id`: owning run; `id`: charger identity; `data`: JSONB name, location, connector, port count, power, availability and depot reference |
| depots | `run_id`: owning run; `id`: depot identity; `data`: JSONB location, total power limit and electricity tariffs |
| planning_jobs | `run_id`: owning run; `id`: search job identity; `data`: JSONB vehicle/request, status, worker owner, lease expiry, timestamps, results/error. This tracks the work of finding charging plans. |
| journey_plans | `run_id`: owning run; `id`: proposed journey identity; `data`: JSONB vehicle, charging/delivery operations, cost, status, version and approval information. This stores the answer from the search. |
| reservations | `run_id`: owning run; `id`: charging-stop or external booking identity; `vehicle_id`: booked vehicle; `charger_id`: booked charger; `port`: socket number; `starts_at`: booking start; `ends_at`: booking end |
| simulator_state | `run_id`: owning run and primary key; `data`: JSONB simulated device checkpoint. Separate from observed vehicle state so telemetry genuinely passes through Kafka. |
| outbox | `id`: event identity; `run_id`: owning run; `payload`: JSONB telemetry message; `checkpoint`: trusted internal simulation result; `created_at`: queued time; `sent_at`: HTTP/Kafka acknowledgement time (null means retry); `processed_at`: consumer completion time (used for cleanup) |
| telemetry_receipts | `run_id`: owning run; `event_id`: processed event identity; `body_hash`: detects reuse of an ID with different contents; `created_at`: receipt time. Kept for the run even after detailed history expires. |
| telemetry_history | `run_id`: owning run; `event_id`: event identity; `vehicle_id`: source vehicle; `observed_at`: device time; `received_at`: server time and retention cutoff; `data`: JSONB complete reading. |
| rejected_events | `id`: Kafka topic/partition/offset identity; `reason`: why the message was rejected; `created_at`: rejection time. Invalid messages may have no valid run ID. |
| schema_version | `version`: applied migration number. Infrastructure bookkeeping, not a dashboard entity. |

The five entity tables and reservations use `(run_id,id)` as the primary key.
Receipts and history use `(run_id,event_id)`. All stored run references use a foreign
key with cascade cleanup when that scenario is reset. References inside JSON are
validated in application code, not by SQL foreign keys.

Example: reservation `stop-1`, run `R42`, vehicle `SIM-001`, charger `SIM-C1`, port `1`,
08:02–08:13 means that port is booked for those eleven minutes in that scenario.
Approval checks overlapping intervals while serializing updates with a run-row lock
and revision check. The SQL time check alone does not prevent overlaps.

Removed: `fleet_run.active`; outbox `kind` (always telemetry) and `message_key`
(already derived from payload.vehicle_id); reservation `data` (duplicate details in
plans/external bookings); duplicate run identity/version values inside fleet_run.data;
redundant unscoped reservation index. Revision and worker leases remain because they
protect against stale updates and abandoned or duplicated work.

Migration preserves existing scenarios, jobs, plans, bookings and telemetry. API,
workers and browser must be upgraded together. Open the desired saved scenario once
after the upgrade. Selecting a scenario changes no other scenario; use Pause to stop
one immediately, otherwise its idle timeout pauses it when it is no longer viewed.
