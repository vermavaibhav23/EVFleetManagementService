const assert = require("node:assert/strict");
const test = require("node:test");
const {
  escapeHTML,
  minutePosition,
  bookingStyle,
  bookingLabel,
  isBookedSlot,
  stationChart,
  time,
  journeyRoute,
  readiness,
  vehicleProgress,
  progressMarkup,
  displayPlan,
  overdueDeliveries,
  planningState,
} = require("../app/static/app.js");

test("review explains pending, failed and empty recalculations instead of old results", () => {
  const fleet = { running: false, vehicles: { V1: { state: "PARKED" } }, plans: {}, jobs: {
    old: { vin: "V1", status: "COMPLETED", results: [{ vin: "V1", plan_id: "old" }] },
    fresh: { vin: "V1", status: "QUEUED", results: [] },
  }};
  let state = planningState("V1", fleet);
  assert.equal(state.latest, fleet.jobs.fresh);
  assert.match(state.message, /Searching.*Simulation paused/);
  fleet.jobs.fresh.status = "COMPLETED";
  state = planningState("V1", fleet);
  assert.equal(state.active, undefined);
  assert.match(state.message, /No current approvable plan/);
  fleet.jobs.fresh.status = "ERROR";
  assert.match(planningState("V1", fleet).message, /could not finish/);
  fleet.jobs.fresh.status = "CANCELLED";
  assert.match(planningState("V1", fleet).message, /cancelled/);
});

test("review does not mistake another vehicle's active search for this vehicle's result", () => {
  const fleet = { jobs: { j: { vin: "V2", status: "RUNNING" } }, plans: {} };
  const state = planningState("V1", fleet);
  assert.ok(state.active);
  assert.equal(state.latest, undefined);
  assert.match(state.message, /Another vehicle/);
});
test("untrusted names are escaped", () =>
  assert.equal(
    escapeHTML('<img onerror="x">'),
    "&lt;img onerror=&quot;x&quot;&gt;",
  ));
test("minute placement uses the same full day origin", () =>
  assert.equal(
    minutePosition("2026-10-04T09:17:00+05:30", "2026-10-04T00:00:00+05:30"),
    557,
  ));
test("timestamps are displayed in IST independent of host timezone", () =>
  assert.equal(time("2026-10-04T03:47:00Z"), "09:17"));
test("physical release takes priority over interrupted logical booking", () =>
  assert.equal(
    bookingStyle({ status: "RELEASING", plan_status: "INTERRUPTED" }),
    "active",
  ));
test("proposals and completed bookings have distinct styles", () => {
  assert.equal(
    bookingStyle({ status: "PLANNED", plan_status: "REJECTED" }),
    "rejected",
  );
  assert.equal(
    bookingStyle({ status: "PLANNED", plan_status: "PROPOSED" }),
    "proposed",
  );
  assert.equal(
    bookingStyle({ status: "COMPLETED", plan_status: "EXECUTING" }),
    "completed",
  );
});
test("daily chart shows every port and exact interval tooltip", () => {
  const start = "2026-10-04T00:00:00+05:30",
    end = "2026-10-05T00:00:00+05:30";
  const svg = stationChart(
    { name: "A&B", charger_id: "C1", port_count: 2 },
    [{ start, end, price: 12 }],
    [],
    { start, end, clock: "2026-10-04T09:17:00+05:30" },
  );
  assert.match(svg, /Port 1/);
  assert.match(svg, /Port 2/);
  assert.match(svg, /24:00/);
  assert.match(svg, /09:17/);
  assert.match(svg, /A&amp;B/);
  assert.match(svg, /₹12.00/);
});

test("map follows the full approved chain, including multiple charges and return", () => {
  const vehicle = { vin: "V1", lat: 12, lon: 77 };
  const operations = [
    { kind: "DELIVERY", status: "COMPLETED", name: "Already served" },
    { kind: "CHARGE", status: "ACTIVE", name: "First charger" },
    { kind: "DELIVERY", status: "PLANNED", name: "Customer B" },
    { kind: "CHARGE", status: "PLANNED", name: "Second charger" },
    { kind: "RETURN", status: "PLANNED", name: "Depot" },
  ];
  assert.deepEqual(
    journeyRoute(vehicle, { status: "EXECUTING", operations }, {}),
    [vehicle, ...operations.slice(1)],
  );
  assert.deepEqual(
    journeyRoute(vehicle, { status: "PROPOSED", operations }, {}),
    [vehicle, ...operations.slice(1)],
  );
});

test("interrupted journeys show remaining fixed deliveries with exactly one depot return", () => {
  const depot = { name: "Depot" };
  const vehicle = {
    depot_id: "D",
    state: "WAITING_REVIEW",
    deliveries: [
      { sequence: 2, name: "B", status: "PLANNED" },
      { sequence: 1, name: "A", status: "COMPLETED" },
    ],
  };
  const route = journeyRoute(
    vehicle,
    { status: "INTERRUPTED", operations: [{ name: "Cancelled charger" }] },
    { D: depot },
  );
  assert.deepEqual(
    route.map((n) => n.name),
    [undefined, "B", "Depot"],
  );
  vehicle.deliveries.push({
    sequence: 3,
    name: "Explicit return",
    is_return: true,
    status: "PLANNED",
  });
  assert.equal(journeyRoute(vehicle, null, { D: depot }).length, 3);
  vehicle.state = "COMPLETED";
  vehicle.deliveries.forEach((d) => (d.status = "COMPLETED"));
  assert.deepEqual(journeyRoute(vehicle, null, { D: depot }), [vehicle]);
});

test("readiness never reports an unapproved or interrupted proposal as dispatchable", () => {
  const vehicle = {
    state: "PARKED",
    energy_kwh: 20,
    temperature_c: 30,
    plan_id: "P",
  };
  assert.equal(
    readiness(vehicle, { P: { status: "PROPOSED" } }),
    "NEEDS_CHARGING",
  );
  assert.equal(readiness(vehicle, { P: { status: "APPROVED" } }), "NORMAL");
  assert.equal(
    readiness(vehicle, { P: { status: "INTERRUPTED" } }),
    "NEEDS_CHARGING",
  );
  assert.equal(
    readiness(
      { ...vehicle, health_fault: true },
      { P: { status: "APPROVED" } },
    ),
    "BLOCKED",
  );
  assert.equal(readiness({ ...vehicle, state: "ASSISTANCE" }, {}), "EMERGENCY");
});


function progressFixture(state = "CHARGING") {
  const vehicle = {vin:"V1", state, plan_id:"P1", operation_index:0, energy_kwh:10, capacity_kwh:40, soh_pct:100, deliveries:[], depot_id:"D"};
  const operation = {stop_id:"charge1", kind:"CHARGE", name:"Economy hub", port:2, status:"ACTIVE", start:"2026-10-04T09:00:00+05:30", depart:"2026-10-04T08:40:00+05:30", arrival:"2026-10-04T08:59:00+05:30", end:"2026-10-04T09:21:00+05:30", target_soc:50, energy_end:20};
  const fleet = {clock:"2026-10-04T09:10:00+05:30",running:true,policy:{connection_minutes:1,release_minutes:1},depots:{D:{name:"Home"}},plans:{P1:{vin:"V1",status:"EXECUTING",operations:[operation]}}};
  return {vehicle,operation,fleet};
}
test("selected vehicle charging status updates battery target and remaining time",()=>{
  const {vehicle,fleet}=progressFixture();
  let p=vehicleProgress(vehicle,fleet);
  assert.equal(p.title,"Charging at Economy hub · Port 2");
  assert.match(p.detail,/25.0% now → 50.0% target/);
  assert.equal(p.remaining,10);
  vehicle.energy_kwh=15; fleet.clock="2026-10-04T09:15:00+05:30";
  p=vehicleProgress(vehicle,fleet);
  assert.match(p.detail,/37.5% now/); assert.equal(p.remaining,5);
  fleet.running=false;
  assert.match(progressMarkup(vehicle,fleet),/5 min remaining \(paused\)/);
});
test("live progress never presents an unapproved option as current execution",()=>{
  const {vehicle,fleet}=progressFixture("PARKED");
  fleet.plans.P1.status="PROPOSED";
  const p=vehicleProgress(vehicle,fleet);
  assert.equal(p.title,"Waiting for plan approval"); assert.equal(p.upcoming.length,0); assert.equal(p.until,null);
});

test("elapsed review clears the selected route and cost and reports every missed deadline",()=>{
  const {vehicle,fleet}=progressFixture("PARKED");
  const plan=fleet.plans.P1;
  plan.status="PROPOSED"; plan.valid_until="2026-10-04T08:40:00+05:30";
  vehicle.deliveries=[
    {name:"A",sequence:1,status:"PLANNED",deadline:"2026-10-04T08:43:00+05:30"},
    {name:"B",sequence:2,status:"PLANNED",deadline:"2026-10-04T09:00:00+05:30"},
  ];
  assert.equal(displayPlan(vehicle,fleet,"P1"),null);
  assert.deepEqual(journeyRoute(vehicle,plan,fleet.depots,fleet.clock).slice(1).map(x=>x.name),["A","B","Home"]);
  const progress=vehicleProgress(vehicle,fleet);
  assert.equal(progress.title,"Delivery deadline missed");
  assert.match(progress.detail,/A: deadline passed 27.0 min ago/);
  assert.match(progress.detail,/B: deadline passed 10.0 min ago/);
  assert.match(progress.detail,/delayed journey may still be feasible/);
  assert.equal(progress.upcoming.length,0);
  plan.status="APPROVED";
  assert.equal(displayPlan(vehicle,fleet,"P1"),plan);
});

test("arrived, unloading, completed and depot stops do not become missed arrival deadlines",()=>{
  const {vehicle,fleet}=progressFixture("PARKED");
  vehicle.deliveries=[{status:"COMPLETED"},{status:"SERVICING"},{status:"PLANNED",arrived_at:fleet.clock},{status:"PLANNED",is_return:true}]
    .map((d,i)=>({...d,name:String(i),deadline:"2026-10-04T08:00:00+05:30"}));
  assert.deepEqual(overdueDeliveries(vehicle,fleet.clock),[]);
});

test("changed conditions invalidate a selected proposal even before departure",()=>{
  const {vehicle,fleet}=progressFixture("PARKED");
  Object.assign(fleet.plans.P1,{status:"PROPOSED",valid_until:"2026-10-04T10:00:00+05:30",review:{can_approve:false}});
  assert.equal(displayPlan(vehicle,fleet,"P1"),null);
  assert.equal(vehicleProgress(vehicle,fleet).title,"Options need updating");
});
test("driving, queue, connection and waiting window show the correct next boundary",()=>{
  const {vehicle,operation,fleet}=progressFixture();
  for(const [state,title,until] of [
    ["TRAVELLING","Driving to Economy hub",operation.arrival],
    ["QUEUING","Waiting at Economy hub · Port 2",operation.start],
    ["CONNECTING","Connecting at Economy hub · Port 2","2026-10-04T03:31:00.000Z"],
    ["WAITING_WINDOW","Waiting at Economy hub",operation.start],
  ]) {
    vehicle.state=state; const p=vehicleProgress(vehicle,fleet);
    assert.equal(p.title,title); assert.equal(new Date(p.until).getTime(),new Date(until).getTime());
  }
});

test("ready vehicle does not display a scheduled departure countdown",()=>{
  const {vehicle,fleet}=progressFixture("READY");
  let p=vehicleProgress(vehicle,fleet);
  assert.equal(p.title,"Ready to continue"); assert.equal(p.until,null);
  fleet.plans.P1.operations.push({kind:"DELIVERY",trip_id:"D1",status:"PLANNED"});
  vehicle.deliveries=[{trip_id:"D1",ready_at:"2026-10-04T10:00:00+05:30"}];
  p=vehicleProgress(vehicle,fleet);
  assert.equal(p.title,"Waiting for route readiness");
  assert.equal(p.until,vehicle.deliveries[0].ready_at);
});
test("interrupted release and ongoing unloading survive loss of the assigned plan",()=>{
  const {vehicle,operation,fleet}=progressFixture("RELEASING");
  vehicle.plan_id=null; vehicle.release_until=operation.end;
  fleet.plans.P1.status="INTERRUPTED"; operation.status="RELEASING";
  let p=vehicleProgress(vehicle,fleet);
  assert.equal(p.title,"Unplugging at Economy hub · Port 2"); assert.equal(p.until,vehicle.release_until);
  vehicle.health_fault=true; vehicle.energy_kwh=0;
  assert.equal(vehicleProgress(vehicle,fleet).title,"Unplugging at Economy hub · Port 2");
  vehicle.health_fault=false; vehicle.energy_kwh=5;
  vehicle.state="SERVICING"; vehicle.service_until="2026-10-04T09:40:00+05:30";
  vehicle.deliveries=[{trip_id:"D1",name:"Customer B",status:"SERVICING"}];
  p=vehicleProgress(vehicle,fleet);
  assert.equal(p.title,"Unloading at Customer B"); assert.equal(p.remaining,30);
});
test("progress distinguishes completed visits, planned stops and assistance without invented completion",()=>{
  const {vehicle,operation,fleet}=progressFixture();
  operation.status="COMPLETED"; operation.actual_end=operation.end;
  fleet.plans.P1.operations.push({stop_id:"next",kind:"DELIVERY",name:"Customer C",status:"PLANNED",arrival:"2026-10-04T10:00:00+05:30"});
  let p=vehicleProgress(vehicle,fleet);
  assert.equal(p.completed.length,1); assert.equal(p.upcoming.length,1);
  vehicle.plan_id=null; vehicle.state="ASSISTANCE"; vehicle.energy_kwh=0;
  p=vehicleProgress(vehicle,fleet); assert.equal(p.warning,true); assert.equal(p.upcoming.length,0);
  assert.match(p.detail,/Nobody has been dispatched/);
  vehicle.state="COMPLETED"; vehicle.energy_kwh=3;
  assert.equal(vehicleProgress(vehicle,fleet).title,"Journey complete");
});
test("live progress escapes untrusted station names and incident text",()=>{
  const {vehicle,operation,fleet}=progressFixture();
  operation.name='<img src=x onerror=alert(1)>';
  const html=progressMarkup(vehicle,fleet);
  assert.ok(!html.includes('<img')); assert.ok(html.includes('&lt;img'));
});


test("unselected alternatives cannot overlay approved completed slots",()=>{
  const start="2026-10-04T00:00:00+05:30",end="2026-10-05T00:00:00+05:30";
  const slot={charger_id:"C1",port:1,start:"2026-10-04T08:43:00+05:30",end:"2026-10-04T08:53:00+05:30",cost:173.91};
  const used={...slot,vin:"APPROVED-VAN",status:"COMPLETED",plan_status:"COMPLETED",was_approved:true};
  const old={...slot,vin:"UNSELECTED-VAN",status:"PLANNED",plan_status:"SUPERSEDED",cost:608.7};
  assert.equal(bookingLabel(used),"Approved · completed");
  assert.equal(bookingLabel(old),"Not selected / replaced · never booked");
  assert.equal(isBookedSlot(old),false);
  const chart=stationChart({name:"Premium",charger_id:"C1",port_count:1},[{start,end,price:20}],[used,old],{start,end,clock:start});
  assert.match(chart,/APPROVED-VAN · Approved · completed/);
  assert.ok(!chart.includes('UNSELECTED-VAN'));
  assert.ok(!chart.includes('608.70'));
});
test("booking labels distinguish unapproved options from released reservations",()=>{
  assert.equal(bookingLabel({plan_status:"PROPOSED",status:"PLANNED"}),"Awaiting approval · not booked");
  assert.equal(bookingLabel({plan_status:"SUPERSEDED",status:"CANCELLED",was_approved:true}),"Replaced · reservation released");
  assert.equal(bookingLabel({plan_status:"CANCELLED",status:"PLANNED"}),"Cancelled option · never booked");
  assert.equal(isBookedSlot({plan_status:"INTERRUPTED",status:"RELEASING"}),true);
  assert.equal(bookingLabel({plan_status:"INTERRUPTED",status:"RELEASING"}),"Approved · unplugging / port occupied");
});
