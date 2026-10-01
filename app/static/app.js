const api = "/api/v1";

const byId = (id) => document.getElementById(id);
const formatTime = (value) => value ? new Date(value).toLocaleString() : "-";
const number = (value, suffix = "") => value == null ? "-" : `${Number(value).toFixed(1)}${suffix}`;

async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function setMessage(text, error = false) {
  byId("message").textContent = text;
  byId("message").classList.toggle("error", error);
}

let refreshInFlight = null;

async function refresh(force = false) {
  if (refreshInFlight) {
    await refreshInFlight;
    if (!force) return;
  }

  const currentRefresh = loadDashboard();
  refreshInFlight = currentRefresh;
  try {
    await currentRefresh;
  } finally {
    if (refreshInFlight === currentRefresh) refreshInFlight = null;
  }
}

async function loadDashboard() {
  try {
    const [overview, fleet, alerts, plans, simulator, chargers] = await Promise.all([
      request("/fleet/overview"),
      request("/fleet/vehicles?limit=100"),
      request("/alerts?limit=100"),
      request("/charging/plans?limit=100"),
      request("/simulator/status"),
      request("/chargers"),
    ]);
    byId("live-dot").classList.add("online");
    byId("system-label").textContent = "API connected";
    byId("metric-vehicles").textContent = overview.vehicles;
    byId("metric-critical").textContent = overview.critical_alerts;
    byId("metric-alerts").textContent = overview.open_alerts;
    byId("metric-plans").textContent = overview.active_charging_plans;
    byId("metric-chargers").textContent = `${overview.available_chargers} / ${overview.chargers}`;
    byId("sim-clock").textContent = simulator.running
      ? `Running · ${formatTime(simulator.simulated_time)} · ${simulator.emitted_events} events`
      : `Stopped · ${simulator.tracked_vehicles} vehicles loaded`;
    renderVehicles(fleet.vehicles, plans);
    renderMap(fleet.vehicles, chargers);
    renderAlerts(alerts);
    renderPlans(plans);
  } catch (error) {
    byId("live-dot").classList.remove("online");
    byId("system-label").textContent = "API unavailable";
    setMessage(String(error.message || error), true);
  }
}

function renderVehicles(vehicles, plans) {
  const priority = {CRITICAL: 0, CHARGE_SOON: 1, UNKNOWN: 2, SAFE: 3};
  const activePlans = new Map(
    plans
      .filter((plan) => ["PROPOSED", "APPROVED", "CHARGING"].includes(plan.status))
      .map((plan) => [plan.vin, plan])
  );
  vehicles.sort((a, b) => (priority[a.readiness] ?? 9) - (priority[b.readiness] ?? 9));
  byId("vehicles-body").innerHTML = vehicles.map((vehicle) => {
    const activePlan = activePlans.get(vehicle.vin);
    const action = activePlan
      ? `<button class="table-action" onclick="viewPlan('${vehicle.vin}')">${activePlan.status === "PROPOSED" ? "View plan" : activePlan.status}</button>`
      : vehicle.readiness === "CRITICAL" || vehicle.readiness === "CHARGE_SOON"
        ? `<button class="table-action" onclick="generatePlan('${vehicle.vin}')">Generate plan</button>`
        : "-";
    const navigation = vehicle.navigation_target
      ? `<strong>${vehicle.navigation_phase || "ROUTE"}</strong><br><span class="muted">${vehicle.navigation_target} · ${number(vehicle.distance_to_destination_km, " km")} · ${number(vehicle.eta_minutes, " min")}</span>`
      : "-";
    return `
    <tr>
      <td><strong>${vehicle.name}</strong><br><span class="muted">${vehicle.vin}</span></td>
      <td><span class="state-label ${vehicle.operating_state || ""}">${vehicle.operating_state || "-"}</span></td>
      <td>${navigation}</td>
      <td>${number(vehicle.soc_pct, "%")}</td>
      <td>${number(vehicle.current_range_km, " km")}</td>
      <td>${number(vehicle.post_trip_range_km, " km")}</td>
      <td>${number(vehicle.range_margin_km, " km")}</td>
      <td><span class="badge ${vehicle.readiness}">${vehicle.readiness}</span></td>
      <td>${action}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="9" class="muted">Seed a scenario to begin.</td></tr>`;
}

function renderMap(vehicles, chargers) {
  const svg = byId("fleet-map");
  const points = [
    ...vehicles.filter((item) => item.lat != null && item.lon != null).map((item) => ({lat: item.lat, lon: item.lon})),
    ...vehicles.filter((item) => item.destination_lat != null && item.destination_lon != null).map((item) => ({lat: item.destination_lat, lon: item.destination_lon})),
    ...chargers.map((item) => ({lat: item.lat, lon: item.lon})),
  ];
  byId("map-empty").hidden = points.length > 0;
  if (!points.length) { svg.innerHTML = ""; return; }

  const lats = points.map((item) => Number(item.lat));
  const lons = points.map((item) => Number(item.lon));
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const latSpan = Math.max(0.02, maxLat - minLat);
  const lonSpan = Math.max(0.02, maxLon - minLon);
  const project = (lat, lon) => ({
    x: 45 + ((Number(lon) - minLon) / lonSpan) * 910,
    y: 380 - ((Number(lat) - minLat) / latSpan) * 340,
  });

  const grid = Array.from({length: 9}, (_, index) => {
    const x = 50 + index * 112.5;
    return `<line class="map-grid" x1="${x}" y1="28" x2="${x}" y2="392"/><line class="map-grid" x1="35" y1="${35 + index * 43}" x2="965" y2="${35 + index * 43}"/>`;
  }).join("");
  const routes = vehicles.map((vehicle) => {
    if (vehicle.lat == null || vehicle.destination_lat == null) return "";
    const from = project(vehicle.lat, vehicle.lon);
    const to = project(vehicle.destination_lat, vehicle.destination_lon);
    const routeClass = vehicle.navigation_phase === "TO_CHARGER" ? "charger-route" : "delivery-route";
    return `<line class="map-route ${routeClass}" x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}"/>`;
  }).join("");
  const chargerNodes = chargers.map((charger) => {
    const point = project(charger.lat, charger.lon);
    return `<g><rect class="map-charger" x="${point.x - 7}" y="${point.y - 7}" width="14" height="14"/><text class="map-label" x="${point.x + 11}" y="${point.y - 8}">${charger.name}</text></g>`;
  }).join("");
  const vehicleNodes = vehicles.map((vehicle) => {
    if (vehicle.lat == null) return "";
    const point = project(vehicle.lat, vehicle.lon);
    const critical = vehicle.readiness === "CRITICAL" ? " critical-vehicle" : "";
    return `<g><circle class="map-vehicle${critical}" cx="${point.x}" cy="${point.y}" r="7"/><text class="map-label vehicle-label" x="${point.x + 10}" y="${point.y + 4}">${vehicle.name.replace("Simulation ", "")}</text></g>`;
  }).join("");
  svg.innerHTML = `${grid}${routes}${chargerNodes}${vehicleNodes}`;
}

function renderAlerts(alerts) {
  byId("alerts-body").innerHTML = alerts.map((alert) => `
    <tr><td><span class="badge ${alert.severity}">${alert.severity}</span></td><td>${alert.vin}</td>
    <td>${alert.status}</td><td>${alert.message}</td><td>${formatTime(alert.updated_at)}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">No alerts.</td></tr>`;
}

function renderPlans(plans) {
  byId("plans-body").innerHTML = plans.map((plan) => `
    <tr data-vin="${plan.vin}"><td>${plan.vin}</td><td>${plan.charger_id} · port ${plan.port_number}</td>
    <td>${number(plan.travel_distance_km, " km")}<br><span class="muted">ETA ${formatTime(plan.estimated_arrival_time)}</span></td>
    <td>${formatTime(plan.start_time)}<br><span class="muted">to ${formatTime(plan.end_time)}</span></td>
    <td>${number(plan.target_soc_pct, "%")}</td><td>₹${Number(plan.estimated_cost).toFixed(2)}</td>
    <td><span class="badge ${plan.status}">${plan.status}</span></td>
    <td>${plan.status === "PROPOSED" ? `<button class="table-action primary" onclick="approvePlan('${plan.plan_id}')">Approve</button>` : "-"}</td></tr>`).join("")
    || `<tr><td colspan="8" class="muted">No charging plans.</td></tr>`;
}

async function seedScenario() {
  try {
    const result = await request("/simulator/scenarios", {
      method: "POST",
      body: JSON.stringify({scenario: byId("scenario").value, vehicle_count: Number(byId("vehicle-count").value), seed: 42}),
    });
    setMessage(`Seeded ${result.vehicles_seeded} vehicles. Demo vehicle: ${result.primary_demo_vin}`);
    await refresh(true);
  } catch (error) { setMessage(error.message, true); }
}

async function startSimulation() {
  try {
    await request("/simulator/start", {method: "POST", body: JSON.stringify({tick_seconds: 1, time_scale: 60})});
    setMessage("Simulation started at 60× time.");
    await refresh(true);
  } catch (error) { setMessage(error.message, true); }
}

async function stopSimulation() {
  try { await request("/simulator/stop", {method: "POST"}); setMessage("Simulation stopped. Latest telemetry is frozen; Start resumes from this point."); await refresh(true); }
  catch (error) { setMessage(error.message, true); }
}

async function generatePlan(vin) {
  try {
    const plan = await request(`/charging/plans/${vin}`, {method: "POST"});
    setMessage(`Plan ${plan.plan_id} is ready for review.`);
    await refresh(true);
    viewPlan(vin);
  }
  catch (error) { setMessage(error.message, true); }
}

function viewPlan(vin) {
  byId("plans-section").scrollIntoView({behavior: "smooth", block: "start"});
  const row = [...byId("plans-body").querySelectorAll("tr")]
    .find((item) => item.dataset.vin === vin);
  if (row) {
    row.classList.add("attention");
    window.setTimeout(() => row.classList.remove("attention"), 2000);
  }
}

async function approvePlan(planId) {
  try { await request(`/charging/plans/${planId}/approve`, {method: "POST"}); setMessage("Plan approved and charger reserved."); await refresh(true); }
  catch (error) { setMessage(error.message, true); }
}

byId("seed-button").addEventListener("click", seedScenario);
byId("start-button").addEventListener("click", startSimulation);
byId("stop-button").addEventListener("click", stopSimulation);
byId("refresh-button").addEventListener("click", refresh);
window.generatePlan = generatePlan;
window.approvePlan = approvePlan;
window.viewPlan = viewPlan;
refresh();
setInterval(refresh, 2000);
