const assert = require("node:assert/strict");
const test = require("node:test");
const {
  escapeHTML,
  minutePosition,
  bookingStyle,
  stationChart,
  time,
  journeyRoute,
  readiness,
} = require("../app/static/app.js");
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
