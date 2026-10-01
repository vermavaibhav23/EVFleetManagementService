const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(
  path.join(__dirname, "../app/static/app.js"),
  "utf8",
);

function harness(fetch) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id))
      nodes.set(id, {
        innerHTML: "",
        textContent: "",
        disabled: false,
        classList: {
          add() {},
          remove() {},
          toggle() {},
          contains() {
            return false;
          },
        },
        addEventListener() {},
        querySelectorAll() {
          return [];
        },
      });
    return nodes.get(id);
  };
  const context = vm.createContext({
    document: {
      getElementById: node,
      querySelectorAll: () => [],
      addEventListener() {},
    },
    fetch,
    AbortSignal,
    setInterval() {},
    setTimeout() {},
    console,
  });
  vm.runInContext(source, context);
  return { context, node, run: (s) => vm.runInContext(s, context) };
}
const overview = {
  vehicles: 0,
  critical_alerts: 0,
  open_alerts: 0,
  active_charging_plans: 0,
  available_chargers: 0,
  chargers: 0,
};
function payload(url) {
  if (url.includes("/manager"))
    return {
      vehicles: [],
      chargers: [],
      depots: [],
      plans: [],
      alerts: [],
      simulator: { running: false },
    };
  if (url.includes("/fleet/vehicles")) return { vehicles: [], total: 0 };
  if (url.includes("/simulator/status"))
    return { running: false, emitted_events: 0 };
  if (url.includes("/health")) return { status: "ready" };
  return [];
}

test("simultaneous forced refreshes coalesce into one ordered follow-up", async () => {
  let calls = [];
  const h = harness(
    (url) =>
      new Promise((resolve) =>
        calls.push(() => resolve({ ok: true, json: async () => payload(url) })),
      ),
  );
  assert.equal(calls.length, 2);
  const first = h.run("refresh(true)");
  const second = h.run("refresh(true)");
  assert.equal(first, second);
  calls.splice(0).forEach((resolve) => resolve());
  await new Promise(setImmediate);
  assert.equal(calls.length, 2);
  calls.splice(0).forEach((resolve) => resolve());
  await first;
  assert.equal(h.node("system-label").textContent, "Fleet connected");
  assert.equal(h.node("start-button").disabled, true);
});

test("old response cannot overwrite an action or a newer generation", async () => {
  const pending = [];
  const h = harness(
    (url) =>
      new Promise((resolve) =>
        pending.push(() =>
          resolve({ ok: true, json: async () => payload(url) }),
        ),
      ),
  );
  h.run("epoch++; busy=true");
  pending.forEach((resolve) => resolve());
  await h.run("refreshInFlight");
  assert.equal(h.run("dashboard"), null);
});

test("failed dependency is shown as reconnecting and actions are disabled", async () => {
  const h = harness(async () => ({
    ok: false,
    status: 503,
    json: async () => ({ detail: { checks: { mongodb: "unavailable" } } }),
  }));
  await h.run("refreshInFlight");
  assert.match(h.node("system-label").textContent, /Reconnecting/);
  assert.equal(h.node("seed-button").disabled, true);
  assert.match(h.node("message").textContent, /dependency/);
});

test("overlapping cars remain individually selectable with leaders to the unchanged station", () => {
  const h = harness(() => new Promise(() => {}));
  h.run(
    `drawMap(byId('fleet-map'), Array.from({length:10},(_,i)=>({vin:'VIN'+i,lat:12.97,lon:77.59,name:'<script>bad</script>',operating_state:'PARKED',soc_pct:50})),[{charger_id:'C1',name:'Station',lat:12.97,lon:77.59,status:'AVAILABLE'}],[])`,
  );
  const markup = h.node("fleet-map").innerHTML;
  assert.ok(!markup.includes("<script>"));
  assert.ok(markup.includes("&lt;script&gt;"));
  const circle = [...markup.matchAll(/<circle cx="([^"]+)" cy="([^"]+)"/g)];
  assert.equal(circle.length, 10);
  assert.equal((markup.match(/class="map-pin vehicle-pin/g) || []).length, 10);
  assert.ok(
    markup.includes(
      `x="${+circle[0][1] - 12}" y="${+circle[0][2] - 15}" width="24"`,
    ),
  );
});

test("map camera is stable across telemetry updates", () => {
  const h = harness(() => new Promise(() => {}));
  h.run(
    `drawMap(byId('fleet-map'),[{vin:'A',lat:12.97,lon:77.59,name:'Van'}],[],[])`,
  );
  const before = h.run("JSON.stringify(viewport)");
  h.run(
    `drawMap(byId('fleet-map'),[{vin:'A',lat:13.01,lon:77.69,name:'Van'}],[],[])`,
  );
  assert.equal(h.run("JSON.stringify(viewport)"), before);
});

test("map contains only selected vehicle routes", () => {
  const h = harness(() => new Promise(() => {}));
  h.run(
    `dashboard={primary_demo_vin:'A',plans:[],vehicles:[{vin:'A',name:'First',lat:13,lon:77,soc_pct:20,delivery_remaining_km:10,current_trip:{destination_lat:13.1,destination_lon:77.1,destination:'One'}},{vin:'B',name:'Second',lat:13,lon:77,current_trip:{destination_lat:13.2,destination_lon:77.2,destination:'Two'}}]};selectedVin='A';drawMap(byId('fleet-map'),dashboard.vehicles,[],[])`,
  );
  assert.equal(
    (h.node("fleet-map").innerHTML.match(/class="map-route delivery"/g) || [])
      .length,
    1,
  );
  assert.ok(!h.node("fleet-map").innerHTML.includes(">Two<"));
});

test("run replacement resets selection and prevents old vehicles leaking into tabs", async () => {
  const v = {
    vin: "SIM1",
    name: "First",
    manager_readiness: "NORMAL",
    operating_state: "PARKED",
    itinerary: [],
  };
  let run = "one";
  const h = harness(async (url) => ({
    ok: true,
    json: async () =>
      url.includes("/manager")
        ? {
            run_id: run,
            primary_demo_vin: run === "one" ? "SIM1" : "SIM2",
            vehicles: [
              {
                ...v,
                vin: run === "one" ? "SIM1" : "SIM2",
                name: run === "one" ? "First" : "Second",
              },
            ],
            plans: [],
            chargers: [],
            depots: [],
            alerts: [],
            simulator: { running: false },
          }
        : { status: "ready" },
  }));
  await h.run("refreshInFlight");
  assert.equal(h.run("selectedVin"), "SIM1");
  run = "two";
  await h.run("refresh(true)");
  assert.equal(h.run("selectedVin"), "SIM2");
  h.run("renderVehicleProfile();renderPlans()");
  assert.ok(!h.node("vehicle-directory").innerHTML.includes("First"));
  assert.equal(h.node("decision-title").textContent, "Second");
});

test("alternatives have no approval controls and reasoning uses numeric differences", () => {
  const h = harness(() => new Promise(() => {}));
  h.run(
    `dashboard={vehicles:[{vin:'A',name:'Van',manager_readiness:'NEEDS_CHARGING'}],chargers:[],plans:[{vin:'A',plan_id:'P',status:'PROPOSED',reason:'calculated',evaluated_options:[{charger_id:'One',electricity_cost:100,travel_distance_km:3,travel_minutes:5,wait_minutes:10,charging_minutes:20,deadline_margin_minutes:40},{charger_id:'Two',electricity_cost:150,travel_distance_km:5,travel_minutes:8,wait_minutes:2,charging_minutes:20,deadline_margin_minutes:50}]}]};selectedVin='A';renderPlans()`,
  );
  const markup = h.node("option-cards").innerHTML;
  assert.equal((markup.match(/data-action="approve"/g) || []).length, 1);
  assert.ok(!markup.includes("Comparison only"));
  assert.match(markup, /2.0 km farther/);
  assert.match(markup, /50.00/);
  assert.match(markup, /Recommended/);
});

test("currency and readiness text remain correctly encoded", () => {
  const h = harness(() => new Promise(() => {}));
  assert.equal(h.run("money(50)"), "₹50.00");
  assert.match(h.run("badge('NORMAL','✓ Normal')"), /✓ Normal/);
  assert.ok(!source.includes("Â·"));
});

test("infeasible options display exclusions only for the evaluated vehicle and run", () => {
  const h = harness(() => new Promise(() => {}));
  h.run(
    `dashboard={run_id:'r1',simulator:{running:false},vehicles:[{vin:'A',name:'Van',manager_readiness:'NEEDS_CHARGING'}],chargers:[{charger_id:'C1',name:'Station One'}],plans:[]};selectedVin='A';noOptions={vin:'A',run_id:'r1',reason:'No feasible slot',exclusions:[{charger_id:'C1',reason:'Deadline infeasible'}]};renderPlans()`,
  );
  assert.match(h.node("decision-reasoning").innerHTML, /Deadline infeasible/);
  h.run("dashboard.run_id='r2';renderPlans()");
  assert.equal(h.node("decision-reasoning").innerHTML, "");
});

test("arrived vehicle does not retain an obsolete diversion distance label", () => {
  const h = harness(() => new Promise(() => {}));
  h.run(
    `dashboard={primary_demo_vin:'A',vehicles:[{vin:'A',name:'Van',lat:13,lon:77,soc_pct:30,manager_readiness:'NEEDS_CHARGING',delivery_remaining_km:10,current_trip:{destination_lat:13.1,destination_lon:77.1,destination:'Customer'}}],plans:[{vin:'A',status:'CHARGING',charger_id:'C',travel_distance_km:4.3}]};selectedVin='A';drawMap(byId('fleet-map'),dashboard.vehicles,[{charger_id:'C',name:'Charger',lat:13,lon:77,status:'AVAILABLE'}],[])`,
  );
  assert.ok(!h.node("fleet-map").innerHTML.includes("km diversion"));
  assert.ok(h.node("fleet-map").innerHTML.includes("Charger"));
  assert.equal(h.run("geographicDistance({lat:13,lon:77},{lat:13,lon:77})"), 0);
  assert.ok(h.run("geographicDistance({lat:0,lon:0},{lat:0,lon:1})") > 111);
});

test("disclosure state survives redraws, respects closure and is scoped to vehicle/run", () => {
  const h = harness(() => new Promise(() => {}));
  h.run(`dashboard={run_id:'r1'};selectedVin='A';document.querySelectorAll=()=>[{dataset:{disclosure:'r1:A:why-plan'},open:true}];captureDisclosures()`);
  assert.match(h.run("disclosure('why-plan')"), / open/);
  h.run("captureDisclosures()");
  assert.match(h.run("disclosure('why-plan')"), / open/);
  h.run("document.querySelectorAll=()=>[{dataset:{disclosure:'r1:A:why-plan'},open:false}];captureDisclosures()");
  assert.ok(!h.run("disclosure('why-plan')").includes(' open'));
  h.run("selectedVin='B'");
  assert.ok(!h.run("disclosure('why-plan')").includes(' open'));
});

test("journey shows observed interruption without claiming skipped charging, then ends at emergency", () => {
  const h = harness(() => new Promise(() => {}));
  h.run("dashboard={plans:[],simulator:{simulated_time:'2026-10-01T09:00:00Z'}}");
  const markup=h.run(`renderJourney({vin:'A',manager_readiness:'NEEDS_CHARGING',journey_progress:{steps:[{stage:'to_charger'},{stage:'interrupted',note:'Station fault'},{stage:'decision'}]}})`);
  assert.match(markup,/Station fault/);
  assert.match(markup,/aria-current="step"/);
  assert.ok(!markup.includes('stage-charging'));
  assert.match(markup,/Review the available choices/);
  const emergency=h.run(`renderJourney({vin:'A',manager_readiness:'EMERGENCY',operating_state:'STRANDED'})`);
  assert.match(emergency,/Energy Emergency/);
  assert.ok(!emergency.includes('class="upcoming"'));
});
