# Demo reliability audit — 2026-10-01

Baseline: main `4d805c0`, clean tracked tree. unittest: 19 passed; pytest: 20 passed (timezone function was missed by unittest). Python compilation and JS syntax pass. Ruff: seven BLE001 findings; five files need formatting. Live readiness: HTTP 200.

## Defect checklist (verification in progress)

- [ ] Serialize start/stop/seed/planning/approval and telemetry operations; concurrent start has an await-before-task race.
- [ ] Surface background failure and drain all vehicle work on stop; failed task can make stop fail.
- [ ] Reset Redis, runtime, database and reject previous-generation telemetry on reseed.
- [ ] Bound movement by available energy; preserve arrival/resume state across restart.
- [ ] Use actual reservation statuses, verify own port reservation and charger health before charging.
- [ ] Stale/unrelated telemetry must never complete a new plan or revive a cancelled plan.
- [ ] Repeated approval/rejection/cancellation must be idempotent; reject must release reservations.
- [ ] Revalidate approval against current energy, health, reservations and deadline; choose an alternative on conflict.
- [ ] Correct slot rounding, charger-to-customer energy, charging taper/efficiency and metered pricing.
- [ ] Replace invented emergency deadline with explicit delivery deadline; validate update datetimes.
- [ ] Synchronize readiness rows to the same telemetry snapshot; report real free ports.
- [ ] Serialize polling/action refresh, expose useful failures and valid button states.
- [ ] Separate real charger coordinates; fit geographic map plus depot detail; remove overlapping text labels.
- [ ] Expose consumer state/lag and verify Kafka readiness; sanitize public dependency errors.
- [ ] Add API/state-machine/contention/frontend regressions; run measured load and live acceptance.

Deployment and acceptance evidence will be appended after verification. No 100,000 events/s claim is validated.
