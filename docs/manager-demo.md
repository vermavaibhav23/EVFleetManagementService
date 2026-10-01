# Manager demonstration

Load a Scenario with 10 vehicles. The fleet is paused at the current time in Asia/Kolkata. Review Van 001, then approve a charging plan or explicit manager decision before starting. Reset only clears disposable SIM records. The same city and physical energy calculations are used across scenarios.

| Group | Scenario | Initial situation | Decision / outcome |
|---|---|---|---|
| Normal Operations | Normal Operations | Energy covers four scheduled legs and reserve | Execute all stops without charging |
| Charger Scenarios | Busy Chargers | Six cars charging, three waiting; cheap station has long backlog | More expensive North Hub preserves the deadline |
| Charger Scenarios | Charger Offline | East Solar is faulty; healthy reachable alternatives | Exclude the failed station and recalculate |
| Non-final Deliveries | Time to Charge Ahead | Six connected stops; time to charge now | Charge to full, cover several stops, book a later session |
| Non-final Deliveries | Tight Next Deadline | Limited charging time; later charging fits | Partial target, on-time first delivery, later reserved charge |
| Non-final Deliveries | Priority Delivery | Can reach customer but not a charger afterward | Accept charging delay, or explicit reserve exception plus recovery and reassignment |
| Non-final Deliveries | Timetable Conflict | Cannot safely dispatch or charge within the deadline | Hold; manager can accept a charging delay |
| Final Delivery | Time to Top Up | One final stop and ample time | Full charge improves next-shift readiness |
| Final Delivery | Deadline First | Full charging would threaten the deadline | Maximum partial charge within the protected window |
| Final Delivery | Priority Final Stop | Direct delivery is possible only below reserve | Explicit manager exception and recovery request, or accept delay |

## Rules

- Timetable order is fixed. A required depot return is included before the route ends.
- Deadline minus drive time minus a 20-minute traffic buffer sets the protected departure. Later deadlines and charger bookings can constrain it further.
- The planner targets up to 100% within that window, using the shared taper/efficiency model. Minimum energy covers the delivery, a reachable continuation and the configured 15 km reserve.
- Remaining legs and future charging stops are validated. Approval reserves the later sessions too; the simulator activates them at their associated leg after prior service completes.
- Feasible alternatives compare unit energy price, travel and waiting because different stations can supply different amounts of energy. This prioritizes readiness; it is not a proof of globally cheapest fleet optimization.
- Both proposal approval and manager exceptions reject stale run/telemetry context. Original deadlines remain unchanged when a delay is accepted.
- Reassignment selects a standby van with enough energy for collection, all transferred legs and reserve. The van drives to collect packages after the source delivery arrives. Source vehicle recovery is requested in the simulation record, not physically performed or sent to an external service.
- No charge is gained while driving or waiting. Occupied ports are exclusive. Canceling a parent plan releases its remaining scheduled bookings.
- Start advances a common simulation clock. Pause freezes it. All schedule, queue and tariff calculations use that clock.

## Display

Use the single Fit fleet button, scroll to zoom and drag to pan. Station coordinates never move. Overlapping car badges have leader lines to their real locations. Charging is green and pulses; waiting is gray. All active customers are shown; route lines belong only to the selected vehicle. More details are collapsed under Why this plan and battery details.

## Limits

Straight-line geography at a simulated 35 km/h, not a road-routing or traffic feed. Recovery is a request/status only. Candidate search is bounded to the configured chargers and the supplied timetable. Backend health guards remain although overheating and unexpected-route presets are removed from the menu. The demonstration runs a single application process; coordination locks are process-local, and multi-record changes are not a distributed database transaction.

## Verification

```powershell
python -m unittest discover -s tests
node --test tests/test_frontend.cjs
ruff check app tests
ruff format --check app tests
```
