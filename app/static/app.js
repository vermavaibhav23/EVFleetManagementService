const api = "/api/v1";
const byId = (id) => document.getElementById(id);
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const number = (value, suffix = "", digits = 1) =>
  value == null ? "—" : `${Number(value).toFixed(digits)}${suffix}`;
const money = (value) => (value == null ? "—" : `₹${Number(value).toFixed(2)}`);
const formatTime = (value) =>
  value
    ? new Date(value).toLocaleTimeString("en-IN", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Kolkata",
      })
    : "—";
const formatDate = (value) =>
  value
    ? new Date(value).toLocaleDateString("en-IN", {
        day: "numeric",
        month: "short",
        timeZone: "Asia/Kolkata",
      })
    : "—";
const human = (value) =>
  String(value || "")
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (c) => c.toUpperCase());
const labels = {
  NORMAL: "Normal",
  NEEDS_CHARGING: "Charging required",
  EMERGENCY: "Energy emergency",
  BLOCKED: "Charging blocked",
  COMPLETE: "Delivery complete",
  NO_DELIVERY: "No delivery scheduled",
  OFFLINE: "Awaiting telemetry",
};
const symbols = {
  NORMAL: "✓",
  NEEDS_CHARGING: "!",
  EMERGENCY: "!",
  BLOCKED: "⊘",
  COMPLETE: "✓",
  NO_DELIVERY: "−",
  OFFLINE: "○",
};
const priority = {
  EMERGENCY: 0,
  BLOCKED: 1,
  NEEDS_CHARGING: 2,
  OFFLINE: 3,
  NORMAL: 4,
  NO_DELIVERY: 5,
  COMPLETE: 6,
};
const scenarios = {
  NORMAL_DAY: {
    name: "Normal Operations",
    group: "Normal Operations",
    description: "Enough energy for the full timetable.",
  },
  CHARGER_CONGESTION: {
    name: "Busy Chargers",
    group: "Charger Scenarios",
    description: "The cheaper station has a longer queue.",
  },
  CHARGER_FAILURE: {
    name: "Charger Offline",
    group: "Charger Scenarios",
    description: "A nearby station is unavailable.",
  },
  NONFINAL_RELAXED: {
    name: "Time to Charge Ahead",
    group: "Non-final Deliveries",
    description: "Time now can protect the later deliveries.",
  },
  NONFINAL_TIGHT: {
    name: "Tight Next Deadline",
    group: "Non-final Deliveries",
    description: "A short charge now; another stop later.",
  },
  NONFINAL_PRIORITY: {
    name: "Priority Delivery",
    group: "Non-final Deliveries",
    description: "Deliver now or charge and accept a delay.",
  },
  NONFINAL_CONFLICT: {
    name: "Timetable Conflict",
    group: "Non-final Deliveries",
    description: "The current deadline needs a manager decision.",
  },
  FINAL_RELAXED: {
    name: "Time to Top Up",
    group: "Final Delivery",
    description: "Top up while the final deadline allows it.",
  },
  FINAL_TIGHT: {
    name: "Deadline First",
    group: "Final Delivery",
    description: "Keep enough time for the final delivery.",
  },
  FINAL_PRIORITY: {
    name: "Priority Final Stop",
    group: "Final Delivery",
    description: "Review the low-reserve arrival before dispatch.",
  },
};
let decisionPreview = null;

let dashboard = null,
  connected = false,
  busy = false,
  epoch = 0;
let refreshInFlight = null,
  refreshAgain = false,
  selectedVin = null,
  activeTab = "overview";
let noOptions = null;
let viewport = null,
  lastSnapshotAt = null,
  lastLoadMs = 0,
  snapshotGapMs = null,
  mapDrag = null,
  suppressMapClick = false;
let search = "",
  filter = "all",
  directorySearch = "";

async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, {
    headers: { "Content-Type": "application/json" },
    signal: AbortSignal.timeout(30000),
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg).join("; ")
          : response.status === 503
            ? "A service dependency is unavailable. Reconnecting…"
            : `Request failed (${response.status}). Please refresh and try again.`,
    );
  }
  return payload;
}
function setMessage(text, error = false) {
  byId("message").textContent = text;
  byId("message").classList.toggle("error", error);
}
function updateControls() {
  document
    .querySelectorAll("button[data-action]")
    .forEach((b) => (b.disabled = busy || !connected));
  byId("seed-button").disabled = busy || !connected;
  byId("start-button").disabled =
    busy ||
    !connected ||
    !dashboard?.vehicles.length ||
    dashboard?.simulator.running;
  byId("stop-button").disabled =
    busy || !connected || !dashboard?.simulator.running;
  byId("refresh-button").disabled = busy;
  byId("scenario").disabled = busy;
  byId("vehicle-count").disabled = busy;
}
function refresh(force = false) {
  if (refreshInFlight) {
    if (force === true) refreshAgain = true;
    return refreshInFlight;
  }
  refreshInFlight = (async () => {
    do {
      refreshAgain = false;
      await loadDashboard();
    } while (refreshAgain);
  })().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}
async function loadDashboard() {
  const version = epoch,
    started = Date.now();
  try {
    const [snapshot] = await Promise.all([
      request("/fleet/manager"),
      request("/health/ready"),
    ]);
    if (version !== epoch || busy) return;
    const changedRun = dashboard?.run_id !== snapshot.run_id;
    if (changedRun || !snapshot.vehicles.some((v) => v.vin === selectedVin)) {
      selectedVin =
        snapshot.primary_demo_vin || snapshot.vehicles[0]?.vin || null;
      viewport = null;
      noOptions = null;
      decisionPreview = null;
    }
    dashboard = snapshot;
    if (changedRun) {
      if (snapshot.scenario) byId("scenario").value = snapshot.scenario;
      byId("vehicle-count").value = snapshot.vehicles.length || 10;
    }
    if (!connected && byId("message").classList.contains("error"))
      setMessage("Connection restored. Fleet data is current.");
    connected = true;
    lastLoadMs = Date.now() - started;
    snapshotGapMs = lastSnapshotAt ? Date.now() - lastSnapshotAt : null;
    lastSnapshotAt = Date.now();
    byId("live-dot").classList.add("online");
    byId("system-label").textContent = "Fleet connected";
    render();
  } catch (error) {
    if (version !== epoch) return;
    connected = false;
    byId("live-dot").classList.remove("online");
    byId("system-label").textContent = "Reconnecting · data may be stale";
    setMessage(
      error.name === "TimeoutError"
        ? "Fleet data took too long to load. Retrying automatically."
        : error.message,
      true,
    );
  } finally {
    updateControls();
  }
}
function selected() {
  return dashboard?.vehicles.find((v) => v.vin === selectedVin);
}
function currentPlan(vin = selectedVin) {
  return dashboard?.plans.find(
    (p) =>
      p.vin === vin && ["PROPOSED", "APPROVED", "CHARGING"].includes(p.status),
  );
}
function stationName(id) {
  return dashboard?.chargers.find((c) => c.charger_id === id)?.name || id;
}
function badge(status, label) {
  return `<span class="badge ${escapeHtml(status)}">${escapeHtml(label || labels[status] || human(status))}</span>`;
}
function readinessBadge(v) {
  return badge(
    v.manager_readiness,
    `${symbols[v.manager_readiness] || "○"} ${labels[v.manager_readiness] || "Awaiting assessment"}`,
  );
}
function focusBadge(v) {
  return v.vin === dashboard?.primary_demo_vin ? "" : "";
}
function actionButton(action, id, label, primary = false) {
  return `<button class="${primary ? "primary" : ""}" data-action="${action}" data-id="${escapeHtml(id)}">${escapeHtml(label)}</button>`;
}
function fact(label, value) {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
}
function selectVehicle(vin, tab) {
  if (!dashboard?.vehicles.some((v) => v.vin === vin)) return;
  selectedVin = vin;
  if (tab) switchTab(tab);
  else render();
}
function switchTab(tab) {
  if (!["overview", "vehicles", "chargers", "plans"].includes(tab)) return;
  activeTab = tab;
  document
    .querySelectorAll("[data-panel]")
    .forEach((el) => (el.hidden = el.dataset.panel !== tab));
  document.querySelectorAll("[data-tab]").forEach((el) => {
    el.classList.toggle("active", el.dataset.tab === tab);
    if (el.dataset.tab === tab) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  byId("page-title").textContent = {
    overview: "Overview",
    vehicles: "Vehicles",
    chargers: "Chargers",
    plans: "Plans & Decisions",
  }[tab];
  render();
}
function render() {
  if (!dashboard) return;
  const sim = dashboard.simulator,
    demo = scenarios[dashboard.scenario];
  byId("sim-clock").textContent =
    sim.error ||
    `${sim.running ? `Running · ${sim.time_scale}×` : "Paused"} · ${formatDate(sim.simulated_time)}, ${formatTime(sim.simulated_time)} IST`;
  byId("sim-clock").classList.toggle("error", !!sim.error);
  byId("scenario-guide").innerHTML = demo
    ? `<span class="badge neutral">Scenario</span><strong>${escapeHtml(demo.name)}</strong><span class="muted">${escapeHtml(demo.description)}</span><button data-select="${escapeHtml(dashboard.primary_demo_vin)}" data-select-tab="overview">Van 001 ↗</button>`
    : "";
  byId("freshness").textContent =
    `${dashboard.vehicles.length} vehicles · ${sim.running ? "Live simulation" : "Telemetry frozen while paused"} · snapshot ${(lastLoadMs / 1000).toFixed(2)}s${snapshotGapMs ? ` · received every ${(snapshotGapMs / 1000).toFixed(1)}s` : ""}`;
  if (activeTab === "overview") {
    renderOverview();
    renderMap();
  }
  if (activeTab === "vehicles") renderVehicleProfile();
  if (activeTab === "chargers") renderChargers();
  if (activeTab === "plans") renderPlans();
  updateControls();
}
function vehicleCard(v, tab) {
  return `<button class="vehicle-card ${v.vin === selectedVin ? "selected" : ""}" data-select="${escapeHtml(v.vin)}" ${tab ? `data-select-tab="${tab}"` : ""} aria-pressed="${v.vin === selectedVin}"><span class="row"><strong>${escapeHtml(v.name)}</strong><span class="soc">${number(v.soc_pct, "%", 0)}</span></span><small>${escapeHtml(human(v.operating_state))} · ${number(v.delivery_remaining_km, " km remaining")}</small>${readinessBadge(v)} ${focusBadge(v)}</button>`;
}
function renderOverview() {
  const vs = dashboard.vehicles;
  const counts = [
    [
      "Normal",
      vs.filter(
        (v) =>
          v.manager_readiness === "NORMAL" && v.operating_state !== "CHARGING",
      ).length,
      "✓",
      "",
    ],
    [
      "Needs charging",
      vs.filter(
        (v) =>
          ["NEEDS_CHARGING", "BLOCKED"].includes(v.manager_readiness) &&
          v.operating_state !== "CHARGING",
      ).length,
      "ϟ",
      "warm",
    ],
    [
      "Emergency",
      vs.filter((v) => v.manager_readiness === "EMERGENCY").length,
      "!",
      "red",
    ],
    [
      "Charging",
      vs.filter((v) => v.operating_state === "CHARGING").length,
      "↯",
      "blue",
    ],
    [
      "Completed",
      vs.filter((v) => v.manager_readiness === "COMPLETE").length,
      "✓",
      "",
    ],
  ];
  byId("metrics").innerHTML = counts
    .map(
      ([label, count, icon, kind]) =>
        `<div class="metric ${kind}"><div><span>${label}</span><strong>${count.toString().padStart(2, "0")}</strong></div><i class="metric-icon" aria-hidden="true">${icon}</i></div>`,
    )
    .join("");
  const attention = vs.filter(
    (v) =>
      ["NEEDS_CHARGING", "BLOCKED", "EMERGENCY"].includes(
        v.manager_readiness,
      ) && v.operating_state !== "CHARGING",
  );
  byId("attention-count").textContent = attention.length;
  const filtered = vs
    .filter(
      (v) =>
        (v.name + v.vin).toLowerCase().includes(search.toLowerCase()) &&
        (filter === "all" ||
          (filter === "attention"
            ? attention.includes(v)
            : v.manager_readiness === filter)),
    )
    .sort(
      (a, b) =>
        (priority[a.manager_readiness] ?? 9) -
          (priority[b.manager_readiness] ?? 9) || a.vin.localeCompare(b.vin),
    );
  byId("attention-list").innerHTML =
    filtered.map((v) => vehicleCard(v)).join("") ||
    '<p class="empty">No vehicles match these filters.</p>';
  byId("fleet-count").textContent =
    `${filtered.length} of ${vs.length} vehicles · ordered by readiness urgency`;
  byId("alerts-list").innerHTML =
    attention
      .map(
        (v) =>
          `<div class="alert-row">${readinessBadge(v)}<div><strong>${escapeHtml(v.name)}</strong><p>${escapeHtml(v.explanation)}</p></div><button data-select="${escapeHtml(v.vin)}" data-select-tab="${v.manager_readiness === "NEEDS_CHARGING" ? "plans" : "overview"}">${v.manager_readiness === "NEEDS_CHARGING" ? "Review charging →" : "Inspect vehicle →"}</button></div>`,
      )
      .join("") ||
    '<p class="empty">All clear. No vehicles need charging attention.</p>';
  renderSelected();
}
function renderSelected() {
  const v = selected();
  if (!v) {
    byId("selected-detail").innerHTML =
      '<p class="empty">Select a vehicle.</p>';
    return;
  }
  const plan = currentPlan(),
    trips = v.itinerary || [],
    done = trips.filter((t) => t.status === "COMPLETED").length;
  const actions = plan
    ? actionButton("view", v.vin, "Review plan", true)
    : v.manager_readiness === "NEEDS_CHARGING"
      ? actionButton("compare", v.vin, "Review options", true)
      : "";
  byId("selected-detail").innerHTML =
    `<h2>${escapeHtml(v.name)}</h2><div class="status-row">${readinessBadge(v)} ${badge(v.operating_state)}</div><div class="battery-readout"><strong>${number(v.soc_pct, "", 0)}</strong><span>% battery</span></div><div class="battery-bar ${v.soc_pct < 25 ? "low" : ""}"><i style="width:${v.soc_pct || 0}%"></i></div><div class="detail-body"><dl class="facts">${fact("Next stop", v.current_trip?.destination || "Timetable complete")}${fact("Distance", number(v.delivery_remaining_km, " km"))}${fact("Deadline", formatDate(v.current_trip?.delivery_deadline) + " · " + formatTime(v.current_trip?.delivery_deadline) + " IST")}${fact("Stops completed", `${done} / ${trips.length}`)}</dl><p class="short-reason">${escapeHtml(v.explanation)}</p>${actions}${!plan && ["NEEDS_CHARGING", "EMERGENCY"].includes(v.manager_readiness) ? actionButton("manager", v.vin, "Manager choices") : ""}<details><summary>Battery & route details</summary><dl class="facts">${fact("Available range", number(v.current_range_km, " km"))}${fact("Reserve", number(v.reserve_range_km, " km"))}${fact("Battery temperature", number(v.battery_temperature_c, "Â°C"))}${fact("Updated", formatTime(v.telemetry_time) + " IST")}</dl></details><button class="detail-link" data-select="${escapeHtml(v.vin)}" data-select-tab="vehicles">View timetable →</button></div>`;
}

function renderVehicleProfile() {
  const vs = dashboard.vehicles.filter((v) =>
    (v.name + v.vin).toLowerCase().includes(directorySearch.toLowerCase()),
  );
  byId("vehicle-total").textContent = `${dashboard.vehicles.length} vehicles`;
  byId("vehicle-directory").innerHTML =
    vs.map((v) => vehicleCard(v)).join("") ||
    '<p class="empty">No matching vehicles.</p>';
  const v = selected();
  if (!v) {
    byId("vehicle-profile").innerHTML = "Select a vehicle.";
    return;
  }
  byId("vehicle-profile").innerHTML =
    `<div class="profile-heading"><span class="vehicle-monogram">${escapeHtml(v.vin.slice(-3))}</span><div><h2>${escapeHtml(v.name)}</h2><p>${number(v.soc_pct, "%")} battery · ${human(v.operating_state)}</p></div></div><div class="section-heading"><h2>Timetable</h2><span>${formatDate(v.itinerary?.[0]?.departure_time)} · IST</span></div><ol class="itinerary">${
      (v.itinerary || [])
        .map((t) => {
          const current = t.trip_id === v.current_trip?.trip_id,
            done = t.status === "COMPLETED";
          return `<li class="${done ? "completed" : current ? "current" : ""}">${badge(done ? "COMPLETE" : current ? "APPROVED" : "neutral", done ? "Delivered" : current ? "Next stop" : "Upcoming")}<h3>${escapeHtml(t.destination)}</h3><p>${escapeHtml(t.origin)} → ${escapeHtml(t.destination)} · ${number(t.distance_km, " km")}</p><p>Earliest departure ${formatTime(t.departure_time)} · Due ${formatDate(t.delivery_deadline)}, ${formatTime(t.delivery_deadline)}${t.completed_at ? " · Arrived " + formatTime(t.completed_at) : ""} IST</p>${t.accepted_delay ? '<span class="badge NEEDS_CHARGING">Delay accepted</span>' : ""}${t.reserve_exception ? '<span class="badge EMERGENCY">Reserve exception · recovery requested</span>' : ""}${t.transfer_from_vin ? '<span class="badge neutral">Reassigned · package handover required</span>' : ""}</li>`;
        })
        .join("") || "<li>Available for reassignment.</li>"
    }</ol><details><summary>Vehicle specifications</summary><dl class="facts">${fact("Usable battery", number(v.usable_capacity_kwh, " kWh"))}${fact("Connector", v.connector_type)}${fact("Charging limit", number(v.max_charge_power_kw, " kW"))}${fact("Consumption", number(v.consumption_kwh_per_km, " kWh/km", 2))}</dl></details>`;
}

function renderChargers() {
  const clock = dashboard.simulator.simulated_time;
  byId("charger-cards").innerHTML = dashboard.chargers
    .map((c) => {
      const active = c.reservations
        .filter((r) =>
          ["CONFIRMED", "VEHICLE_EN_ROUTE", "OCCUPIED"].includes(r.status),
        )
        .sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
      const wait = c.next_available_at
        ? Math.max(0, (new Date(c.next_available_at) - new Date(clock)) / 60000)
        : null;
      return `<article class="workspace charger-card"><div class="station-top"><span class="station-icon">ϟ</span>${badge(c.status, c.status === "AVAILABLE" ? "Online" : "Offline")}</div><h2>${escapeHtml(c.name)}</h2><p>${c.connector_type} · ${number(c.available_kw, " kW", 0)} per port</p><div class="port-summary"><div><strong>${c.occupied_ports}/${c.port_count}</strong>Charging</div><div><strong>${c.waiting_count || 0}</strong>Waiting</div><div><strong>${c.free_ports}</strong>Free</div></div><p class="price">${money(c.current_price_per_kwh)} <small>/ kWh</small></p><p>Next availability: <strong>${wait === null ? "Offline" : wait === 0 ? "Now" : number(wait, " min", 0)}</strong></p><div class="station-queue">${active.map((r) => `<button class="reservation-window" data-select="${escapeHtml(r.vin)}" data-select-tab="overview"><strong>Port ${r.port_number} · ${escapeHtml(dashboard.vehicles.find((v) => v.vin === r.vin)?.name || r.vin)}</strong><span>${human(r.status === "OCCUPIED" ? "CHARGING" : dashboard.vehicles.find((v) => v.vin === r.vin)?.operating_state === "WAITING_FOR_CHARGER" ? "WAITING_FOR_CHARGER" : "RESERVED")} · ${formatTime(r.start_time)}–${formatTime(r.end_time)}</span></button>`).join("") || '<p class="muted">No queue or bookings.</p>'}</div><details><summary>Price schedule · IST</summary><table class="tariff-table"><tbody>${c.tariffs.map((t) => `<tr><td>${t.start_time}–${t.end_time}</td><td>${money(t.price_per_kwh)} / kWh</td></tr>`).join("")}</tbody></table></details></article>`;
    })
    .join("");
}

function renderPlans() {
  const v = selected(),
    plan = currentPlan(),
    options = plan?.evaluated_options || [];
  byId("decision-title").textContent = v?.name || "Select a vehicle";
  byId("decision-subtitle").textContent = v
    ? `${number(v.soc_pct, "%")} battery · ${v.current_trip?.destination || "Timetable complete"}`
    : "";
  byId("decision-actions").innerHTML = v
    ? plan?.status === "PROPOSED"
      ? actionButton("replace", plan.plan_id, "Refresh") +
        actionButton("reject", plan.plan_id, "Dismiss")
      : !plan && ["NEEDS_CHARGING", "EMERGENCY"].includes(v.manager_readiness)
        ? actionButton("compare", v.vin, "Compare chargers", true) +
          actionButton("manager", v.vin, "Manager choices")
        : ""
    : "";
  byId("option-cards").innerHTML = options.length
    ? options
        .map(
          (o, i) =>
            `<article class="option-card ${i === 0 ? "recommended" : ""}">${badge(i === 0 ? "NORMAL" : "neutral", i === 0 ? "Recommended" : "Alternative")}<h3>${escapeHtml(stationName(o.charger_id))}</h3><p class="price">${number(o.target_soc_pct, "%", 0)} <small>target charge</small></p><dl class="facts">${fact("Energy & cost", `${number(o.grid_energy_kwh, " kWh")} · ${money(o.electricity_cost)}`)}${fact("Average energy price", money(o.average_price_per_kwh) + " / kWh")}${fact("Travel / wait", `${number(o.travel_minutes, " min")} / ${number(o.wait_minutes, " min")}`)}${fact("Charging", number(o.charging_minutes, " min"))}${fact("Delivery ETA", formatTime(o.delivery_eta) + " IST")}${fact("Traffic buffer", "20 min protected")}${fact("Stops covered now", String(o.covered_stops ?? 0))}</dl><p class="short-reason">${i === 0 ? "Fits the timetable and preserves reserve." : escapeHtml(optionReason(o, options[0]))}</p>${i === 0 && plan.status === "PROPOSED" ? actionButton("approve", plan.plan_id, "Approve plan", true) : i === 0 ? badge(plan.status) : ""}</article>`,
        )
        .join("")
    : `<div class="workspace empty" style="grid-column:1/-1">${escapeHtml(noOptions?.vin === v?.vin && noOptions?.run_id === dashboard.run_id ? noOptions.reason : v?.explanation || "Select a vehicle.")}</div>`;
  const exclusions =
    plan?.exclusions ||
    (noOptions?.vin === v?.vin && noOptions?.run_id === dashboard.run_id
      ? noOptions.exclusions
      : []) ||
    [];
  byId("decision-reasoning").innerHTML =
    (plan
      ? `<details class="reasoning"><summary>Why this plan?</summary><p>${escapeHtml(plan.reason)}</p>${plan.follow_up_stops?.length ? `<h3>Later booked stops</h3><ul>${plan.follow_up_stops.map((s) => `<li>${escapeHtml(stationName(s.charger_id))} · ${formatTime(s.start_time)}–${formatTime(s.end_time)} IST · target ${number(s.target_soc_pct, "%")}</li>`).join("")}</ul>` : ""}</details>`
      : "") +
    (exclusions.length
      ? `<details class="reasoning" ${!plan ? "open" : ""}><summary>Other stations</summary><ul>${exclusions.map((e) => `<li><b>${escapeHtml(stationName(e.charger_id))}:</b> ${escapeHtml(e.reason)}</li>`).join("")}</ul></details>`
      : "") +
    renderManagerChoices(v);
  byId("plans-body").innerHTML =
    dashboard.plans
      .map(
        (p) =>
          `<tr><td><strong>${escapeHtml(dashboard.vehicles.find((v) => v.vin === p.vin)?.name || p.vin)}</strong><br>${escapeHtml(stationName(p.charger_id))}</td><td>${badge(p.status)}</td><td>${formatTime(p.start_time)}–${formatTime(p.end_time)} IST</td><td>${number(p.target_soc_pct, "%")} · ${money(p.estimated_cost)}</td><td>${p.status === "SCHEDULED" ? "Booked for a later leg" : p.status === "COMPLETED" ? "Charging finished; port released" : escapeHtml(human(p.status))}${["PROPOSED", "APPROVED", "CHARGING"].includes(p.status) ? actionButton(p.status === "PROPOSED" ? "reject" : "cancel", p.plan_id, p.status === "PROPOSED" ? "Dismiss" : "Cancel") : ""}</td></tr>`,
      )
      .join("") +
      (dashboard.manager_decisions || [])
        .map(
          (d) =>
            `<tr><td>${escapeHtml(dashboard.vehicles.find((v) => v.vin === d.vin)?.name || d.vin)}</td><td>${d.choice === "deliver-now" ? "Reserve exception" : "Delay accepted"}</td><td>${formatTime(d.created_at)} IST</td><td>${d.choice === "deliver-now" ? number(d.arrival_soc_pct, "% predicted arrival") : "Original deadline retained"}</td><td>${d.replacement ? "Reassigned to " + escapeHtml(d.replacement.name) + ". " : ""}${d.recovery_status ? "Recovery requested; not yet recovered." : ""}</td></tr>`,
        )
        .join("") || '<tr><td colspan="5">No decisions yet.</td></tr>';
}

function optionReason(o, best) {
  const parts = [];
  if (o.average_price_per_kwh > best.average_price_per_kwh)
    parts.push(
      `${money(o.average_price_per_kwh - best.average_price_per_kwh)}/kWh higher`,
    );
  if (o.wait_minutes > best.wait_minutes)
    parts.push(
      `${number(o.wait_minutes - best.wait_minutes, " min")} more waiting`,
    );
  if (o.travel_distance_km > best.travel_distance_km)
    parts.push(
      `${number(o.travel_distance_km - best.travel_distance_km, " km")} farther`,
    );
  return (
    parts.join(" · ") ||
    `Also feasible; ${number(o.target_soc_pct, "%")} charge at ${money(o.average_price_per_kwh)}/kWh.`
  );
}

function renderManagerChoices(v) {
  const d = decisionPreview;
  if (!v || !d || d.vin !== v.vin || d.simulation_run_id !== dashboard.run_id)
    return "";
  if ((dashboard.manager_decisions || []).some((x) => x.trip_id === d.trip_id))
    return "";
  const delayed = d.delay_plan?.evaluated_options?.[0]?.delivery_eta;
  return `<section class="workspace manager-choices"><h3>Manager decision</h3><div class="choice-grid"><article><h3>Charge & accept delay</h3><p>Keep the reserve. ${delayed ? "Delivery around " + formatTime(delayed) + " IST." : "No feasible charging continuation available."}</p>${d.delay_available ? actionButton("accept-delay", v.vin, "Accept delay & review charge", true) : ""}</article><article><h3>Deliver priority customer</h3><p>${number(d.arrival_soc_pct, "%")} predicted arrival battery. ${escapeHtml(d.reason)}</p>${d.replacement ? `<p>${d.remaining_deliveries} later stops → ${escapeHtml(d.replacement.name)} after package handover.</p>` : ""}${d.deliver_now_available ? actionButton("deliver-now", v.vin, "Approve reserve exception") : '<span class="badge neutral">Unavailable</span>'}</article></div></section>`;
}

function mapProjection(camera, width, height) {
  const cos = Math.cos((camera.lat * Math.PI) / 180),
    scale = Math.min(width - 60, height - 60) / camera.spanKm;
  return {
    scale,
    point: (lat, lon) => ({
      x: width / 2 + (lon - camera.lon) * 111.195 * cos * scale,
      y: height / 2 - (lat - camera.lat) * 111.195 * scale,
    }),
  };
}
function geographicDistance(a, b) {
  const radians = Math.PI / 180;
  const value =
    Math.sin(((b.lat - a.lat) * radians) / 2) ** 2 +
    Math.cos(a.lat * radians) *
      Math.cos(b.lat * radians) *
      Math.sin(((b.lon - a.lon) * radians) / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
}
function fitViewport(vehicles, chargers, depots) {
  const v = selected();
  const nodes = [...vehicles, ...chargers, ...depots].filter(
    (n) => n.lat != null && n.lon != null,
  );
  if (v?.current_trip?.destination_lat != null)
    nodes.push({
      lat: v.current_trip.destination_lat,
      lon: v.current_trip.destination_lon,
    });
  for (const van of vehicles)
    for (const t of van.itinerary || [])
      if (t.status !== "COMPLETED" && t.destination_lat != null)
        nodes.push({ lat: t.destination_lat, lon: t.destination_lon });
  if (!nodes.length) return { lat: 12.9716, lon: 77.5946, spanKm: 25 };
  const lats = nodes.map((n) => n.lat),
    lons = nodes.map((n) => n.lon),
    lat = (Math.min(...lats) + Math.max(...lats)) / 2,
    lon = (Math.min(...lons) + Math.max(...lons)) / 2;
  return {
    lat,
    lon,
    spanKm: Math.max(
      15,
      (Math.max(...lats) - Math.min(...lats)) * 111.195 * 1.25,
      (Math.max(...lons) - Math.min(...lons)) *
        111.195 *
        Math.cos((lat * Math.PI) / 180) *
        1.25,
    ),
  };
}
function drawMap(svg, vehicles, chargers, depots) {
  const width = Math.max(300, svg.clientWidth || 900),
    height = Math.max(280, svg.clientHeight || 500);
  svg.setAttribute?.("viewBox", `0 0 ${width} ${height}`);
  if (!viewport) viewport = fitViewport(vehicles, chargers, depots);
  const { point, scale } = mapProjection(viewport, width, height),
    v = selected(),
    plan = currentPlan();
  const line = (a, b, kind) => {
    const p = point(a.lat, a.lon),
      q = point(b.lat, b.lon);
    return `<line class="map-route ${kind}" x1="${p.x}" y1="${p.y}" x2="${q.x}" y2="${q.y}"/>`;
  };
  let routes = "";
  if (v?.current_trip?.destination_lat != null) {
    const target = {
      lat: v.current_trip.destination_lat,
      lon: v.current_trip.destination_lon,
    };
    routes += line(v, target, "delivery");
    let previous = target;
    for (const t of (v.itinerary || []).filter(
      (t) => t.status === "PLANNED" && t.trip_id !== v.current_trip.trip_id,
    )) {
      const next = { lat: t.destination_lat, lon: t.destination_lon };
      routes += line(previous, next, "future");
      previous = next;
    }
    const c = chargers.find((c) => c.charger_id === plan?.charger_id);
    if (c) {
      routes += line(
        v,
        c,
        plan.status === "PROPOSED" ? "proposed" : "approved",
      );
      routes += line(c, target, "return");
    }
  }
  const customers = new Map();
  for (const van of vehicles)
    for (const t of van.itinerary || []) {
      if (
        t.status === "COMPLETED" ||
        t.destination_lat == null ||
        t.destination === "Central Depot"
      )
        continue;
      const k = `${t.destination_lat.toFixed(4)},${t.destination_lon.toFixed(4)}`;
      if (!customers.get(k)?.selected)
        customers.set(k, {
          lat: t.destination_lat,
          lon: t.destination_lon,
          name: t.destination,
          selected: van.vin === selectedVin,
        });
    }
  const pins = [...customers.values()]
    .map((c) => {
      const p = point(c.lat, c.lon);
      return `<g><title>${escapeHtml(c.name)}</title><path d="M${p.x} ${p.y - 7}l6 7l-6 7l-6-7Z" fill="${c.selected ? "#8554dc" : "#c5b4e8"}" stroke="white"/>${c.selected ? `<text class="map-label customer-label" text-anchor="${p.x > width * 0.6 ? "end" : "start"}" x="${p.x > width * 0.6 ? p.x - 9 : p.x + 9}" y="${p.y - 8}">${escapeHtml(c.name)}</text>` : ""}</g>`;
    })
    .join("");
  const depotPins = depots
    .map((d) => {
      const p = point(d.lat, d.lon);
      return `<g><title>${escapeHtml(d.name)}</title><path d="M${p.x} ${p.y - 13}l13 13l-13 13l-13-13Z" fill="#335678" stroke="white" stroke-width="2"/><text class="map-label" x="${p.x + 17}" y="${p.y + 20}">Depot</text></g>`;
    })
    .join("");
  const stationPins = chargers
    .map((c) => {
      const p = point(c.lat, c.lon);
      return `<g class="map-pin" tabindex="0" role="button" data-station="${escapeHtml(c.charger_id)}" aria-label="${escapeHtml(c.name)}"><rect x="${p.x - 12}" y="${p.y - 15}" width="24" height="30" rx="6" fill="${c.status === "FAULTY" ? "#d85658" : "#efb444"}" stroke="white" stroke-width="2"/><text x="${p.x}" y="${p.y + 5}" text-anchor="middle" fill="#593800" font-size="20">ϟ</text><text class="map-label" text-anchor="${p.x > width * 0.6 ? "end" : "start"}" x="${p.x > width * 0.6 ? p.x - 17 : p.x + 17}" y="${p.y - 14}">${escapeHtml(c.name)}</text><text class="map-label station-count" text-anchor="${p.x > width * 0.6 ? "end" : "start"}" x="${p.x > width * 0.6 ? p.x - 17 : p.x + 17}" y="${p.y + 2}">${c.occupied_ports}/${c.port_count} charging · ${c.waiting_count || 0} waiting</text></g>`;
    })
    .join("");
  const placed = [];
  const sorted = [...vehicles.filter((v) => v.lat != null)].sort(
    (a, b) => (a.vin === selectedVin ? 1 : 0) - (b.vin === selectedVin ? 1 : 0),
  );
  const markers = sorted
    .map((van) => {
      const p = point(van.lat, van.lon);
      let x = p.x,
        y = p.y;
      const atStation = chargers.some((c) => geographicDistance(van, c) < 0.05);
      if (atStation) y += 35;
      for (
        let n = 0;
        n < 180 &&
        placed.some((q) => Math.abs(q.x - x) < 31 && Math.abs(q.y - y) < 23);
        n++
      ) {
        const radius = 23 + Math.floor(n / 8) * 19,
          angle = (n * Math.PI) / 4;
        x = p.x + Math.cos(angle) * radius;
        y = p.y + Math.sin(angle) * radius + (atStation ? 35 : 0);
      }
      placed.push({ x, y });
      const charging = van.operating_state === "CHARGING",
        waiting = van.operating_state === "WAITING_FOR_CHARGER",
        emergency = ["STRANDED", "RECOVERY_REQUIRED"].includes(
          van.operating_state,
        ),
        focus = van.vin === selectedVin;
      const fill = charging
        ? "#17a66a"
        : waiting
          ? "#8a939e"
          : emergency
            ? "#df5a62"
            : van.operating_state === "AWAITING_DECISION"
              ? "#ee964b"
              : "#338de0";
      return `<g class="map-pin vehicle-pin ${charging ? "charging-glow" : ""}" tabindex="0" role="button" data-select="${escapeHtml(van.vin)}" aria-label="${escapeHtml(van.name + " " + human(van.operating_state))}"><title>${escapeHtml(van.name)} · ${human(van.operating_state)} · ${number(van.soc_pct, "%")}</title>${Math.hypot(x - p.x, y - p.y) > 2 ? `<line x1="${p.x}" y1="${p.y}" x2="${x}" y2="${y}" stroke="${fill}" stroke-width="1" opacity=".45"/><circle cx="${p.x}" cy="${p.y}" r="2" fill="${fill}"/>` : ""}${focus ? `<rect x="${x - 19}" y="${y - 14}" width="38" height="28" rx="9" fill="none" stroke="#183d61" stroke-width="2"/>` : ""}<rect class="vehicle-body" x="${x - 14}" y="${y - 9}" width="28" height="18" rx="6" fill="${fill}" stroke="white" stroke-width="2"/><rect x="${x - 9}" y="${y - 12}" width="5" height="3" rx="1" fill="#354052"/><rect x="${x + 4}" y="${y + 9}" width="5" height="3" rx="1" fill="#354052"/><text class="marker-code" x="${x}" y="${y}">${escapeHtml(van.vin.slice(-2))}</text></g>`;
    })
    .join("");
  const bar = 5 * scale;
  svg.innerHTML = `<defs><pattern id="grid" width="46" height="46" patternUnits="userSpaceOnUse"><path d="M46 0H0V46" fill="none" stroke="#d6e5ee" stroke-width=".6"/></pattern></defs><rect width="${width}" height="${height}" fill="#f1f7fc"/><rect width="${width}" height="${height}" fill="url(#grid)"/><text class="map-text" x="16" y="22">BENGALURU · SIMULATION</text>${routes}${pins}${depotPins}${stationPins}${markers}<path d="M18 ${height - 20}v5h${bar}v-5" fill="none" stroke="#526b81"/><text class="map-text" x="18" y="${height - 27}">5 km</text>`;
}

function renderMap() {
  if (!dashboard) return;
  byId("map-empty").hidden =
    dashboard.vehicles.length + dashboard.chargers.length > 0;
  drawMap(
    byId("fleet-map"),
    dashboard.vehicles,
    dashboard.chargers,
    dashboard.depots,
  );
  const v = selected();
  byId("map-selection").innerHTML = v
    ? `<strong>${escapeHtml(v.name)}</strong> · ${human(v.operating_state)} · ${number(v.soc_pct, "%")} · ${number(v.delivery_remaining_km, " km to next stop")}`
    : "Select a vehicle.";
}
async function perform(operation, message) {
  if (busy) return;
  busy = true;
  epoch++;
  updateControls();
  setMessage("Updating fleet…");
  try {
    await operation();
    setMessage(message);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    busy = false;
    epoch++;
    await refresh(true);
    updateControls();
  }
}
async function compare(vin, replacePlanId) {
  selectedVin = vin;
  switchTab("plans");
  await perform(async () => {
    if (dashboard.simulator.running)
      await request("/simulator/stop", { method: "POST" });
    if (replacePlanId)
      await request(
        `/charging/plans/${encodeURIComponent(replacePlanId)}/reject`,
        { method: "POST" },
      );
    noOptions = null;
    try {
      await request(`/charging/plans/${encodeURIComponent(vin)}`, {
        method: "POST",
      });
    } catch (error) {
      const evaluation = await request(
        `/charging/recommendations/${encodeURIComponent(vin)}`,
      );
      noOptions = { ...evaluation, run_id: dashboard.run_id };
      decisionPreview = {
        ...(await request(
          `/charging/manager-decisions/${encodeURIComponent(vin)}`,
        )),
        vin,
      };
    }
  }, "Review ready. Simulation paused.");
}
byId("seed-button").addEventListener("click", () => {
  const count = Number(byId("vehicle-count").value),
    scenario = byId("scenario").value;
  if (!Number.isInteger(count) || count < 10 || count > 100) {
    setMessage("Choose a fleet size from 10 to 100 vehicles.", true);
    return;
  }
  perform(async () => {
    await request("/simulator/scenarios", {
      method: "POST",
      body: JSON.stringify({ scenario, vehicle_count: count, seed: 42 }),
    });
    selectedVin = null;
    viewport = null;
  }, "Scenario loaded · paused.");
});
byId("start-button").addEventListener("click", () =>
  perform(
    () =>
      request("/simulator/start", {
        method: "POST",
        body: JSON.stringify({ tick_seconds: 1, time_scale: 60 }),
      }),
    "Simulation started at 60× simulated time. Actual update cadence depends on fleet size.",
  ),
);
byId("stop-button").addEventListener("click", () =>
  perform(
    () => request("/simulator/stop", { method: "POST" }),
    "Simulation paused.",
  ),
);
byId("refresh-button").addEventListener("click", () => refresh(true));
byId("vehicle-search").addEventListener("input", (event) => {
  search = event.target.value;
  renderOverview();
});
byId("readiness-filter").addEventListener("change", (event) => {
  filter = event.target.value;
  renderOverview();
});
byId("directory-search").addEventListener("input", (event) => {
  directorySearch = event.target.value;
  renderVehicleProfile();
});
document.addEventListener("click", async (event) => {
  if (suppressMapClick && event.target.closest("svg")) {
    suppressMapClick = false;
    return;
  }
  const tab = event.target.closest("[data-tab]");
  if (tab) {
    switchTab(tab.dataset.tab);
    return;
  }
  const choice = event.target.closest("[data-select]");
  if (choice) {
    selectVehicle(choice.dataset.select, choice.dataset.selectTab);
    return;
  }
  const station = event.target.closest("[data-station]");
  if (station) {
    setMessage(
      `${stationName(station.dataset.station)}. Open Chargers for ports, tariffs and reservation windows.`,
    );
    return;
  }
  const map = event.target.closest("[data-map]");
  if (map && dashboard) {
    const mode = map.dataset.map,
      v = selected(),
      d = dashboard.depots[0];
    if (mode === "fit")
      viewport = fitViewport(
        dashboard.vehicles,
        dashboard.chargers,
        dashboard.depots,
      );
    if (mode === "vehicle" && v?.lat != null)
      viewport = { lat: v.lat, lon: v.lon, spanKm: 25 };
    if (mode === "depot" && d)
      viewport = { lat: d.lat, lon: d.lon, spanKm: 28 };
    if (mode === "in") viewport.spanKm = Math.max(0.5, viewport.spanKm / 1.8);
    if (mode === "out") viewport.spanKm = Math.min(1000, viewport.spanKm * 1.8);
    renderMap();
    return;
  }
  const button = event.target.closest("button[data-action]");
  if (!button || busy || !connected) return;
  const { action, id } = button.dataset;
  if (action === "manager") {
    selectedVin = id;
    switchTab("plans");
    await perform(async () => {
      if (dashboard.simulator.running)
        await request("/simulator/stop", { method: "POST" });
      decisionPreview = {
        ...(await request(
          `/charging/manager-decisions/${encodeURIComponent(id)}`,
        )),
        vin: id,
      };
    }, "Review the consequences before choosing.");
    return;
  }
  if (["accept-delay", "deliver-now"].includes(action)) {
    const d = decisionPreview;
    if (!d || d.vin !== id) return;
    await perform(
      async () => {
        if (dashboard.simulator.running)
          await request("/simulator/stop", { method: "POST" });
        await request(
          `/charging/manager-decisions/${encodeURIComponent(id)}/${action}`,
          {
            method: "POST",
            body: JSON.stringify({
              simulation_run_id: d.simulation_run_id,
              trip_id: d.trip_id,
              telemetry_sequence: d.telemetry_sequence,
            }),
          },
        );
        decisionPreview = null;
        noOptions = null;
        if (action === "accept-delay")
          await request(`/charging/plans/${encodeURIComponent(id)}`, {
            method: "POST",
          });
      },
      action === "accept-delay"
        ? "Delay accepted. Review the charging plan."
        : "Reserve exception approved. Recovery requested.",
    );
    return;
  }
  if (action === "view") {
    selectVehicle(id, "plans");
    return;
  }
  if (action === "compare") {
    await compare(id);
    return;
  }
  if (action === "replace") {
    await compare(selectedVin, id);
    return;
  }
  await perform(
    () =>
      request(`/charging/plans/${encodeURIComponent(id)}/${action}`, {
        method: "POST",
      }),
    action === "approve"
      ? "Diversion approved and port reserved. Press Start to watch the journey."
      : `Decision ${action === "reject" ? "rejected" : "cancelled"}. Any associated reservation was released.`,
  );
});
document.addEventListener("keydown", (event) => {
  if (
    (event.key === "Enter" || event.key === " ") &&
    event.target.matches?.("svg [role=button]")
  ) {
    event.preventDefault();
    event.target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  }
});
byId("fleet-map").addEventListener("pointerdown", (event) => {
  if (!viewport) return;
  mapDrag = { x: event.clientX, y: event.clientY, camera: { ...viewport } };
  event.currentTarget.setPointerCapture?.(event.pointerId);
});
byId("fleet-map").addEventListener("pointermove", (event) => {
  if (!mapDrag) return;
  const dx = event.clientX - mapDrag.x,
    dy = event.clientY - mapDrag.y;
  if (Math.hypot(dx, dy) < 4) return;
  suppressMapClick = true;
  const { scale } = mapProjection(
    mapDrag.camera,
    Math.max(300, byId("fleet-map").clientWidth || 900),
    Math.max(280, byId("fleet-map").clientHeight || 450),
  );
  viewport = {
    ...mapDrag.camera,
    lat: mapDrag.camera.lat + dy / scale / 111.195,
    lon:
      mapDrag.camera.lon -
      dx / scale / (111.195 * Math.cos((mapDrag.camera.lat * Math.PI) / 180)),
  };
  renderMap();
});
byId("fleet-map").addEventListener("pointerup", () => {
  mapDrag = null;
});
byId("fleet-map").addEventListener("pointercancel", () => {
  mapDrag = null;
  suppressMapClick = false;
});
document.defaultView?.addEventListener("resize", () => {
  if (activeTab === "overview") renderMap();
});
refresh();
setInterval(() => {
  if (!busy) refresh();
}, 3000);

byId("fleet-map").addEventListener(
  "wheel",
  (event) => {
    if (!viewport) return;
    event.preventDefault();
    viewport.spanKm = Math.max(
      1,
      Math.min(150, viewport.spanKm * (event.deltaY > 0 ? 1.15 : 1 / 1.15)),
    );
    renderMap();
  },
  { passive: false },
);
