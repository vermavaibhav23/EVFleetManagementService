# Manager demonstration

Load a Scenario with 10 vehicles. Each reset starts paused at current IST and clears only disposable SIM records. Start runs the fleet continuously until Pause or Reset. Reviewing or approving a choice does not pause other vehicles; only a vehicle requiring a decision holds. The four groups arrange demonstrations, not separate decision engines.

| Group | Scenario | Starting situation | Decision / outcome |
|---|---|---|---|
| Normal Operations | Smooth Deliveries | Energy covers four scheduled legs and reserve | Execute the timetable without charging |
| Normal Operations | Charging Needed Later | Initial stops safe; six-stop route exceeds battery | Drive first; request charging when needed |
| Charger Scenarios | Busy Chargers – Save Money | Six charging; three waiting; generous deadline | Choose cheaper East Solar despite its queue |
| Charger Scenarios | Busy Chargers – Protect Deadline | Same real congestion; tight deadline | Choose more expensive North Hub to arrive on time |
| Charger Scenarios | Charger Unavailable / Offline / Faulty / Incompatible Connector | Select a variation; unsuitable East Solar or all stations unavailable | Exclude unsuitable stations; if charging is required and none is reachable, declare Energy Emergency |
| Charger Scenarios | Charger Fails During Journey | Approved station fails en route or while charging | Cancel affected bookings; hold and review a new safe option |
| Charger Scenarios | Queue Takes Longer | Existing charging sessions slow and overrun | Recheck arrival; cancel a now-infeasible booking and request a new decision |
| Non-final Deliveries | Charge Ahead for Later Stops | Next leg is already safe; later legs need charging | Use the generous window now; full charge and later booking |
| Non-final Deliveries | Tight Next Deadline | Limited time now; future charging fits | Maximum safe partial target; preserve next deadline and later continuation |
| Non-final Deliveries | Priority Delivery – Reserve Exception | Customer reachable; arrival below reserve | Manager: charge + accept delay, or deliver + recovery and reassignment |
| Non-final Deliveries | Priority Delivery – No Safe Continuation | Customer reachable with reserve; next charger/leg is not | Same explicit choices; never silently strand the van |
| Non-final Deliveries | Timetable Conflict | First stop feasible; a later deadline is impossible | Hold; charging + accepted delay; unsafe urgent option disabled |
| Final Delivery | Time to Top Up | One final stop; low battery; ample time | Charge to full within the protected window |
| Final Delivery | Deadline First | Full charging threatens final deadline | Maximum partial charge that retains reserve and traffic buffer |
| Final Delivery | Priority Final Stop | Customer reachable only below reserve | Manager: charging + delay, or delivery + recovery request |

## Shared rules

- Timetable order is fixed; a required depot return is included where applicable. No unexpected trips are invented. Finished vehicles remain complete while the simulation clock continues.
- Deadline minus travel and a 20-minute traffic buffer sets the charging window. Proposals also allow five simulated minutes for review before a new booking. Approval rechecks the displayed station, port, target and reserved times. If conditions invalidate that plan, refresh the review or pause to decide.
- Charging targets up to 100% when useful and feasible, using the same taper and efficiency model as execution. Minimum energy covers the delivery, a reachable continuation and 15 km reserve. A timetable already safely covered needs no charging detour.
- The planner validates remaining deliveries and later charging stops. Approval books those stops; the simulator activates each at its associated leg after prior service. Live station faults, incompatible connectors and queue overruns invalidate affected bookings and trigger shared replanning rules.
- Deadline feasibility comes before price. Among feasible options, compare energy price, then travel and waiting. This is a bounded timetable planner, not a proof of globally optimal fleet routing.
- Accepting charging + delay approves the actual charging plan and accepts delay for the remaining timetable, while keeping original deadlines visible. Deliver + recovery requires feasible arrival and a capable standby replacement for remaining work. Reassignment includes physical package collection; recovery is a request/status, not external roadside dispatch.
- No energy is gained while driving or waiting. Ports are exclusive. Cancelling an interrupted plan releases its unstarted dependent bookings. A new manager choice is possible after an accepted charging plan is cancelled.
- Scenario presets configure vehicles, deadlines, chargers, queues and optional environment events. Runtime planning and status transitions do not branch on scenario names.

## Display

The selected vehicle's Journey progress sits below View timetable. Ticks represent observed events, the highlighted stage is current, and hollow markers indicate the expected continuation. Skipped stages are never falsely ticked. Interrupted attempts remain visible. Each new delivery starts a fresh leg with a summary of recent completed deliveries. Emergency, recovery and handover hold states stop the normal progress display until resolved.

Use Fit fleet, +/− zoom or gentle scrolling; drag to pan. Station coordinates stay fixed and separated. Charging cars pulse green; waiting cars are gray. Purple diamonds are customers: dark labelled diamonds belong to the selected vehicle, light diamonds to others. The legend uses the same diamond. Why this plan, Other stations and battery details preserve their open/closed state through polling, until reload or scenario reset.

## Limits

Straight-line geography at simulated 35 km/h, not road-routing or a live traffic feed. Recovery remains a request/status. Search is bounded to available chargers and the supplied timetable. Backend health guards remain, although overheating and unexpected-route presets are absent. The demo runs one application process: locks are process-local and multi-record changes are not distributed transactions.

## Verification

```powershell
python -m unittest discover -s tests
node --test tests/test_frontend.cjs
ruff check app tests
ruff format --check app tests
```
