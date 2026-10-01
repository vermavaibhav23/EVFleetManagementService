# Manager portal upgrade

The portal has four manager views: Overview, Vehicles, Chargers, and Plans & Decisions. All read one coherent `/api/v1/fleet/manager` projection of the existing MongoDB records. The simulator, scheduler, Kafka consumer, Redis cache, FastAPI framework, and Railway single-worker configuration remain in place.

## Present the core workflow

1. Open `/portal`. Load **Low battery before delivery**, **10** vehicles. Every scenario loads paused at **09:00 Asia/Kolkata** on the demo date. Van 001 is selected and labelled Demo focus.
2. Read the charging-required explanation, remaining delivery distance, available range, and reserve. Operational state and charging readiness are separate.
3. Click **Compare charging options**. If running, this pauses the simulation first. Review up to three different stations. Only the recommended option can be approved; alternatives are explicitly comparison-only.
4. Approve the diversion. Review the reservation in **Chargers**, then press **Start**. The vehicle physically travels to its assigned station, waits for its slot, charges, releases the port, resumes delivery, and reaches its customer.
5. **Pause** freezes simulation time. **Load / reset** resets disposable SIM runtime records and decisions, leaving non-demo records intact. Decision history persists across browser reloads, but not demo resets.

## Scenario selection

| Scenario | Immediate condition | Manager action | Observable outcome |
| --- | --- | --- | --- |
| Low battery before delivery | Van 001 has 20% SoC and a 95 km delivery | Compare, approve, Start | Complete charging and resumed-delivery journey |
| Charger congestion | Solar Canopy has a labelled 75-minute seeded reservation | Compare available stations and their waiting times | Reserved station remains distinct from free alternatives; approved diversion waits for its slot |
| Charger failure | Solar Canopy is faulty | Review exclusion and approve a healthy station | Faulty station cannot be selected |
| Unexpected long delivery | Preconfigured incident checkpoint with a 140 km delivery | Review shortfall and approve | Target SoC covers charger-to-customer distance plus reserve and extra margin |
| Battery overheating | Van 001 battery is 48°C | Inspect health warning; keep paused for presentation | No normal charging plan; if started, vehicle remains on health hold |
| Unreachable charger emergency | Van 001 is 80 km southwest of the depot at 0% SoC | Inspect assistance-required message | Vehicle remains stranded at true coordinates; no impossible plan or simulated assistance dispatch |
| Normal day | Comfortable range for active deliveries | Start and inspect itinerary | Active deliveries complete and do not restart |

## Deliberate limits

- Routes are geodesic straight-line simulations, not road routes. The single map keeps station markers at their true coordinates and preserves its viewport. Use Fit fleet, Focus selected, Focus depot, zoom buttons, or drag to pan. Only the selected vehicle has detailed routes.
- Three connected trips per van form the stored planned itinerary. Only the first leg is executed. Later legs are explicitly schedule-only and excluded from live readiness and execution. There is no full-day fleet optimization.
- Solar Canopy uses explicit station-specific demo tariffs. North Hub and Overflow share the depot tariff. The manager projection and scheduler call the same tariff selection and pricing functions. Currency is INR; tariff timezone is shown.
- Energy emergency means a delivery energy deficit with no physically reachable compatible healthy charger. Queues and deadline failures are separate constraints. Health warnings can block charging independently.
- Scheduler explanations describe the evaluated slots and the existing weighted objective. They do not claim global optimality or an external AI agent.
- Approval recalculates feasibility. If station, port, timing, target, price, run, trip, or deadline changes, approval returns a conflict with no reservation and no silent alternative substitution. The manager can refresh the proposal and review again. Datetime comparison respects MongoDB millisecond precision.
- The browser requests a snapshot every three seconds, coalesces overlapping requests, and rejects responses across action/reset epochs. It does not continuously request fleet-wide recommendations. The footer reports observed receipt cadence. A configured one-second tick is not a throughput guarantee.
- Keep one Railway replica and one Uvicorn worker. This remains the existing unauthenticated disposable hackathon demonstration, not a production security or multi-tenant redesign.

## Verification

The untouched baseline was 45 Python tests and four frontend tests. The upgrade adds tests for distinct options, exact approval/staleness behavior, physical reachability, deadline classification, blocked vehicles, connected schedule-only itineraries, preservation of non-demo records, coherent 100-vehicle snapshots, UTF-8 labels, and stable single-map coordinates. Existing end-to-end unit coverage still checks movement, charging only after arrival, port assignment and release, resumed delivery, repeat actions, reset, previous-run telemetry rejection, and task failures.

Run:

```text
python -m pytest -q
node --test tests/test_frontend.cjs
node --check app/static/app.js
python -m ruff check app tests scripts
python -m ruff format --check app tests scripts
python -m compileall -q app tests scripts
```

The live verification scripts operate only on disposable SIM data. Run them sequentially, as each resets the shared demo:

```text
python scripts/live_acceptance.py --base-url https://evfleetmanagementservice-production.up.railway.app --output live-acceptance.json
python scripts/live_scenarios.py --base-url https://evfleetmanagementservice-production.up.railway.app --output live-scenarios.json
python scripts/demo_load.py --base-url https://evfleetmanagementservice-production.up.railway.app --vehicles 10 100 --seconds 60 --output load-test.json
```

Deployment results, measured performance, screenshots, and a five-minute presentation guide are delivered separately so this document does not confuse historical measurements with a future deployment.
