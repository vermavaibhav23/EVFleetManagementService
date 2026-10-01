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
  LOW_BATTERY_BEFORE_TRIP: {
    name: "Low battery before delivery",
    description: "Van 001 cannot finish its delivery with the safety reserve.",
    action: "Compare options and approve a diversion.",
    result: "Watch it reach a charger, charge, release the port, then deliver.",
  },
  CHARGER_CONGESTION: {
    name: "Charger congestion",
    description:
      "Solar Canopy has a seeded 75-minute reservation; Van 001 needs energy.",
    action: "Compare the reserved station with the available alternatives.",
    result: "The chosen vehicle waits for its booked window before charging.",
  },
  CHARGER_FAILURE: {
    name: "Charger failure",
    description: "Solar Canopy is faulty. Van 001 has a low battery.",
    action: "Review the excluded station and approve a healthy alternative.",
    result: "The diversion goes to a working charger.",
  },
  UNEXPECTED_LONG_TRIP: {
    name: "Unexpected long delivery",
    description:
      "Loaded at the incident checkpoint: Van 001 now has a 140 km assignment.",
    action: "Review the new energy shortfall and approve a diversion.",
    result: "Charging covers the extended delivery plus reserve.",
  },
  BATTERY_OVERHEATING: {
    name: "Battery overheating",
    description: "Van 001 has a 48°C battery; normal charging is blocked.",
    action: "Inspect the health warning and hold the vehicle for inspection.",
    result:
      "No charging plan is offered. Inspection and repair are not simulated.",
  },
  UNREACHABLE_CHARGER: {
    name: "Unreachable charger emergency",
    description: "Van 001 is stranded at a remote checkpoint with 0% battery.",
    action: "Inspect the assistance-required message.",
    result:
      "It stays at its actual location. No impossible plan or assistance dispatch is simulated.",
  },
  NORMAL_DAY: {
    name: "Normal day",
    description:
      "The fleet begins with enough energy for its active deliveries.",
    action: "Start the simulation and inspect a vehicle’s daily itinerary.",
    result:
      "Vehicles reach their customers. Later scheduled legs are not executed.",
  },
};
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
  return v.vin === dashboard?.primary_demo_vin
    ? '<span class="badge focus">Demo focus</span>'
    : "";
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
  const headings = {
    overview: [
      "Keep every delivery moving.",
      "A clear view of your fleet, and the next decision that matters.",
    ],
    vehicles: [
      "Every vehicle. The whole picture.",
      "Battery health, active delivery, and the planned day ahead.",
    ],
    chargers: [
      "The right stop starts here.",
      "Know your charging network, available ports, and the cost of energy.",
    ],
    plans: [
      "Make the next move with confidence.",
      "Compare feasible options, understand the trade-offs, and decide.",
    ],
  };
  byId("page-title").textContent = headings[tab][0];
  byId("page-description").textContent = headings[tab][1];
  byId("page-eyebrow").textContent =
    `FLEET OPERATIONS / ${tab === "plans" ? "PLANS & DECISIONS" : tab.toUpperCase()}`;
  render();
}
function render() {
  if (!dashboard) return;
  const sim = dashboard.simulator,
    demo = scenarios[dashboard.scenario];
  byId("sim-clock").textContent =
    sim.error ||
    `${sim.running ? `Running · ${sim.time_scale}×` : "Paused · ready to present"} · ${formatDate(sim.simulated_time)}, ${formatTime(sim.simulated_time)} IST`;
  byId("sim-clock").classList.toggle("error", !!sim.error);
  byId("scenario-guide").innerHTML = demo
    ? `<span class="guide-icon">◇</span><div><strong>${escapeHtml(demo.name)}</strong><p>${escapeHtml(demo.description)} ${escapeHtml(demo.action)}</p><p><b>Expected:</b> ${escapeHtml(demo.result)}</p></div><button data-select="${escapeHtml(dashboard.primary_demo_vin)}" data-select-tab="overview">Show demo focus ↗</button>`
    : "<p>Load a demonstration using the controls above.</p>";
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
      '<p class="empty">Select a vehicle to see its next step.</p>';
    return;
  }
  const plan = currentPlan(),
    isBlocked = ["BLOCKED", "EMERGENCY"].includes(v.manager_readiness);
  const action = plan
    ? actionButton("view", v.vin, "Review decision →", true)
    : v.manager_readiness === "NEEDS_CHARGING"
      ? actionButton(
          "compare",
          v.vin,
          dashboard.simulator.running
            ? "Compare & pause →"
            : "Compare charging options →",
          true,
        )
      : "";
  const stages = [
    "Review",
    "Approved",
    "Divert",
    "Charge",
    "Resume",
    "Delivered",
  ];
  const stage =
    v.manager_readiness === "COMPLETE"
      ? 5
      : v.operating_state === "RESUMING_TRIP"
        ? 4
        : v.operating_state === "CHARGING"
          ? 3
          : ["EN_ROUTE_TO_CHARGER", "WAITING_FOR_CHARGER"].includes(
                v.operating_state,
              )
            ? 2
            : plan?.status === "APPROVED"
              ? 1
              : 0;
  byId("selected-detail").innerHTML =
    `<p class="eyebrow">SELECTED VEHICLE</p><div class="detail-title"><h2>${escapeHtml(v.name)}</h2></div><p class="detail-id">${escapeHtml(v.vin)}</p><div>${focusBadge(v)} ${readinessBadge(v)}</div><div class="battery-readout"><strong>${number(v.soc_pct, "", 0)}</strong><span>% battery</span></div><div class="battery-bar ${v.soc_pct < 30 ? "low" : ""}"><i style="width:${Math.min(100, Math.max(0, v.soc_pct || 0))}%"></i></div><div class="detail-body"><div class="reason-box ${escapeHtml(v.manager_readiness)}">${escapeHtml(v.explanation)}</div><dl class="facts">${fact("Operational state", human(v.operating_state))}${fact("Delivery remaining", number(v.delivery_remaining_km, " km"))}${fact("Available range", number(v.current_range_km, " km"))}${fact("Safety reserve", number(v.reserve_range_km, " km", 0))}${fact("Range after reserve", number(v.range_margin_km, " km"))}${fact("Battery temperature", number(v.battery_temperature_c, "°C"))}${fact("Telemetry (simulation)", formatTime(v.telemetry_time) + " IST")}${fact("Telemetry lag", number(v.telemetry_lag_seconds, " sim seconds", 0))}</dl>${v.deadline_margin_minutes != null ? `<p class="footnote">${v.deadline_margin_minutes < 20 ? "⚠ Deadline risk: " : "Deadline check: "}${number(v.deadline_margin_minutes, " min")} direct-drive margin, before charging or waiting. Options include those delays.</p>` : ""}${!isBlocked ? `<div class="lifecycle">${stages.map((s, i) => `<span class="${i <= stage ? "done" : ""}">${s}</span>`).join("")}</div>` : ""}${action}<button class="detail-link" data-select="${escapeHtml(v.vin)}" data-select-tab="vehicles">Vehicle details &amp; itinerary ↗</button></div>`;
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
    byId("vehicle-profile").innerHTML =
      '<p class="empty">Choose a vehicle.</p>';
    return;
  }
  const depot = dashboard.depots.find((d) => d.depot_id === v.depot_id);
  byId("vehicle-profile").innerHTML =
    `<div class="profile-heading"><span class="vehicle-monogram">${escapeHtml(v.vin.slice(-3))}</span><div><p class="eyebrow">VEHICLE PROFILE</p><h2>${escapeHtml(v.name)}</h2><small>${escapeHtml(v.vin)}</small></div></div><dl class="facts specs">${fact("Battery capacity", number(v.battery_capacity_kwh, " kWh"))}${fact("Usable capacity", number(v.usable_capacity_kwh, " kWh"))}${fact("State of charge", number(v.soc_pct, "%"))}${fact("Battery health", number(v.soh_pct, "% SoH"))}${fact("Connector", v.connector_type)}${fact("Consumption", number(v.consumption_kwh_per_km, " kWh/km", 3))}${fact("Max charging power", number(v.max_charge_power_kw, " kW", 0))}${fact("Assigned depot", depot?.name || v.depot_id)}</dl><div>${readinessBadge(v)} ${badge(v.operating_state)} ${focusBadge(v)}</div><div class="reason-box ${escapeHtml(v.manager_readiness)}">${escapeHtml(v.explanation)}</div><div class="section-heading"><h2>Today’s delivery itinerary</h2><span class="badge neutral">${formatDate(v.itinerary?.[0]?.departure_time)} · IST</span></div><p class="section-note">Stored, ordered delivery assignments with connected stops. The active leg is simulated; later legs are schedule-only. This is not full-day fleet optimization.</p><ol class="itinerary">${
      (v.itinerary || [])
        .map((t) => {
          const current = t.trip_id === v.current_trip?.trip_id,
            done = t.status === "COMPLETED";
          return `<li class="${done ? "completed" : current ? "current" : ""}">${badge(done ? "COMPLETE" : current ? "APPROVED" : "neutral", done ? "Completed" : current ? "Current delivery" : "Upcoming · schedule only")}<h3>${escapeHtml(t.origin)} → ${escapeHtml(t.destination)}</h3><p>Departure ${formatTime(t.departure_time)} · Deadline ${formatTime(t.delivery_deadline)} IST · ${number(t.distance_km, " km")} · ${t.service_duration_minutes} min service</p><p>${current ? `${number(v.delivery_remaining_km, " km remaining")} · ${human(v.operating_state)}` : done ? "Arrived at customer." : "Planned after the preceding stop."}</p></li>`;
        })
        .join("") || "<li>No deliveries are scheduled.</li>"
    }</ol><button data-select="${escapeHtml(v.vin)}" data-select-tab="plans">Review charging decisions →</button>`;
}
function renderChargers() {
  const clock = dashboard.simulator.simulated_time;
  byId("charger-cards").innerHTML =
    dashboard.chargers
      .map((c) => {
        const max = Math.max(...c.hourly_prices.map((p) => p.price_per_kwh), 1);
        const hour = Number(
          new Date(clock).toLocaleString("en-GB", {
            hour: "2-digit",
            hour12: false,
            timeZone: c.timezone,
          }),
        );
        const active = c.reservations
          .filter((r) =>
            ["CONFIRMED", "VEHICLE_EN_ROUTE", "OCCUPIED"].includes(r.status),
          )
          .sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
        const shared = dashboard.chargers
          .filter(
            (s) =>
              s.depot_id === c.depot_id &&
              s.tariff_scope === "Shared depot tariff",
          )
          .map((s) => s.name)
          .join(" & ");
        return `<article class="workspace charger-card"><div class="station-top"><span class="station-icon">ϟ</span>${badge(c.status, c.status === "AVAILABLE" ? "✓ Healthy" : human(c.status))}</div><h2>${escapeHtml(c.name)}</h2><p class="footnote">${escapeHtml(c.charger_id)} · ${number(c.lat, "° N", 4)}, ${number(c.lon, "° E", 4)}</p><dl class="facts">${fact("Connector", c.connector_type)}${fact("Station power", number(c.available_kw, " kW", 0))}</dl><div class="port-summary"><div><strong>${c.free_ports}</strong>Free now</div><div><strong>${c.occupied_ports}</strong>Occupied</div><div><strong>${c.reserved_ports}</strong>Reserved now</div><div><strong>${c.port_count}</strong>Total ports</div></div><p class="eyebrow">CURRENT ELECTRICITY PRICE</p><p class="price">${money(c.current_price_per_kwh)} <small>INR / kWh</small></p><p class="footnote">${escapeHtml(c.tariff_scope)} · ${escapeHtml(c.timezone)}</p><div class="tariff-chart" role="img" aria-label="24 hour electricity prices; exact tariff windows in the table below">${c.hourly_prices.map((p) => `<div class="tariff-bar ${p.hour === hour ? "current" : ""}" style="height:${(p.price_per_kwh / max) * 100}%" title="${p.hour}:00 — INR ${p.price_per_kwh}/kWh"></div>`).join("")}</div><div class="chart-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div><table class="tariff-table"><tbody>${c.tariffs.map((t) => `<tr><td>${escapeHtml(t.start_time)}–${escapeHtml(t.end_time)}${t.start_time > t.end_time ? " (+1 day)" : ""}</td><td>${money(t.price_per_kwh)} / kWh</td></tr>`).join("") || `<tr><td>All day · base rate</td><td>${money(c.price_per_kwh)} / kWh</td></tr>`}</tbody></table><p class="footnote">${escapeHtml(c.tariff_scope === "Shared depot tariff" ? `Shared by ${shared}. These stations use the same electricity schedule.` : "Explicit demo discount for Solar Canopy; the scheduler uses this same station-specific schedule.")}</p><h3 style="margin-top:22px">Reservation windows</h3>${active.map((r) => `<div class="reservation-window"><strong>Port ${r.port_number} · ${escapeHtml(r.reservation_id === "SIM-CONGESTION-RESERVATION" ? "Seeded congestion example" : dashboard.vehicles.find((v) => v.vin === r.vin)?.name || r.vin)}</strong><br>${formatTime(r.start_time)}–${formatTime(r.end_time)} IST · ${escapeHtml(human(r.status))}</div>`).join("") || '<p class="footnote">No active or upcoming reservations.</p>'}</article>`;
      })
      .join("") ||
    '<div class="workspace empty">No chargers are available in this fleet.</div>';
}
function optionTradeoffs(option, options) {
  const advantages = [],
    disadvantages = [];
  const alternatives = options.filter((o) => o !== option);
  if (!alternatives.length)
    return [
      "The only evaluated feasible station.",
      "No other station meets the current constraints.",
    ];
  const cheapest = Math.min(...options.map((o) => o.electricity_cost)),
    fastest = Math.min(
      ...options.map(
        (o) => o.travel_minutes + o.wait_minutes + o.charging_minutes,
      ),
    ),
    shortest = Math.min(...options.map((o) => o.travel_distance_km));
  if (option.electricity_cost === cheapest) {
    const delta =
      Math.min(...alternatives.map((o) => o.electricity_cost)) -
      option.electricity_cost;
    advantages.push(
      delta > 0
        ? `Saves ${money(delta)} versus the next cheapest station.`
        : "Tied for the lowest charging cost.",
    );
  }
  if (
    option.travel_minutes + option.wait_minutes + option.charging_minutes ===
    fastest
  )
    advantages.push("Earliest charging completion of these stations.");
  if (option.travel_distance_km === shortest)
    advantages.push("Shortest journey to a charger.");
  if (option.electricity_cost > cheapest)
    disadvantages.push(
      `${money(option.electricity_cost - cheapest)} more than the cheapest station.`,
    );
  if (option.travel_distance_km > shortest)
    disadvantages.push(
      `${number(option.travel_distance_km - shortest, " km")} farther than the nearest option.`,
    );
  if (option.wait_minutes > 0)
    disadvantages.push(
      `${number(option.wait_minutes, " min")} waiting before charging.`,
    );
  return [
    advantages.join(" ") ||
      `${number(option.deadline_margin_minutes, " min")} beyond the safety buffer.`,
    disadvantages.slice(0, 2).join(" ") ||
      "No measured cost or travel disadvantage among these options.",
  ];
}
function renderPlans() {
  const v = selected(),
    plan = currentPlan();
  byId("decision-title").textContent = v?.name || "Select a vehicle";
  byId("decision-subtitle").textContent = v
    ? `${labels[v.manager_readiness]} · ${number(v.soc_pct, "%")} battery · ${number(v.delivery_remaining_km, " km remaining")}`
    : "Choose a vehicle from Overview or Vehicles.";
  byId("decision-actions").innerHTML = v
    ? plan?.status === "PROPOSED"
      ? actionButton("replace", plan.plan_id, "Refresh proposal") +
        actionButton("reject", plan.plan_id, "Reject proposal")
      : !plan && v.manager_readiness === "NEEDS_CHARGING"
        ? actionButton(
            "compare",
            v.vin,
            dashboard.simulator.running
              ? "Compare & pause"
              : "Compare charging options",
            true,
          )
        : ""
    : "";
  const options = plan?.evaluated_options || [];
  const excluded =
    noOptions?.vin === selectedVin && noOptions.run_id === dashboard.run_id
      ? noOptions
      : null;
  byId("option-cards").innerHTML = options.length
    ? options
        .map((o, i) => {
          const [advantage, disadvantage] = optionTradeoffs(o, options);
          return `<article class="option-card ${i === 0 ? "recommended" : ""}">${i === 0 ? badge("NORMAL", "✓ Recommended among evaluated feasible options") : badge("neutral", `Alternative ${i} · comparison only`)}<h3>${escapeHtml(stationName(o.charger_id))}</h3><small>Port ${o.port_number} · ${number(o.allocated_power_kw, " kW", 0)} allocated</small><p class="price">${money(o.electricity_cost)} <small>INR estimated</small></p><dl class="facts">${fact("Travel to charger", `${number(o.travel_distance_km, " km")} · ${number(o.travel_minutes, " min")}`)}${fact("Waiting time", number(o.wait_minutes, " min"))}${fact("Charging duration", number(o.charging_minutes, " min"))}${fact("Target SoC", number(o.target_soc_pct, "%"))}${fact("Charge window (IST)", `${formatTime(o.start_time)}–${formatTime(o.end_time)}`)}${fact("Buffered deadline margin", number(o.deadline_margin_minutes, " min"))}</dl><p class="tradeoff"><strong>ADVANTAGE</strong>${escapeHtml(advantage)}</p><p class="tradeoff cons"><strong>TRADE-OFF</strong>${escapeHtml(disadvantage)}</p>${i === 0 ? (plan.status === "PROPOSED" ? actionButton("approve", plan.plan_id, "Approve this diversion →", true) : `<p class="comparison-only">${escapeHtml(human(plan.status))} · ${plan.status === "APPROVED" ? "Start to watch the diversion" : "Vehicle is charging"}</p>`) : '<p class="comparison-only">Comparison only · no alternative approval</p>'}</article>`;
        })
        .join("")
    : `<div class="workspace empty" style="grid-column:1/-1">${escapeHtml(v ? (["BLOCKED", "EMERGENCY", "COMPLETE", "NO_DELIVERY"].includes(v.manager_readiness) ? v.explanation : v.manager_readiness === "NORMAL" ? "This delivery has sufficient energy. No charging decision is needed." : "Compare charging options to create a proposal for review.") : "Select a vehicle to begin.")}</div>`;
  byId("decision-reasoning").innerHTML = plan
    ? `<div class="reasoning"><h3>Why this recommendation?</h3><p>${escapeHtml(plan.reason)}</p><p>Calculated from the reviewed telemetry. Charging estimates include grid energy, taper, and the configured efficiency. Margin is after the 20-minute delivery safety buffer. ${options.length < 3 ? `Only ${options.length} distinct feasible station${options.length === 1 ? " is" : "s are"} available.` : "Three distinct feasible stations are shown."} Approval revalidates the reviewed option; changed conditions require a fresh review.</p>${plan.exclusions?.length ? `<h3>Why other stations were excluded</h3><ul class="exclusions">${plan.exclusions.map((e) => `<li><strong>${escapeHtml(stationName(e.charger_id))}:</strong> ${escapeHtml(e.reason)}</li>`).join("")}</ul>` : ""}<p>${plan.status === "APPROVED" ? "Next: press Start. The vehicle travels to its assigned charger, waits for its port window, charges, releases the port, then resumes the delivery." : "Algorithm-based explanation · no external AI agent."}</p></div>`
    : excluded
      ? `<div class="reasoning"><h3>No feasible charging option</h3><p>${escapeHtml(excluded.reason)}</p><ul class="exclusions">${(excluded.exclusions || []).map((e) => `<li><strong>${escapeHtml(stationName(e.charger_id))}:</strong> ${escapeHtml(e.reason)}</li>`).join("")}</ul></div>`
      : "";
  const expanded = new Set(
    [...byId("plans-body").querySelectorAll("details[open]")].map(
      (d) => d.dataset.plan,
    ),
  );
  byId("plans-body").innerHTML =
    dashboard.plans
      .map((p) => {
        const vehicle = dashboard.vehicles.find((v) => v.vin === p.vin);
        const outcome =
          p.status === "COMPLETED"
            ? vehicle?.operating_state === "AT_CUSTOMER"
              ? "Charged · port released · delivered"
              : "Charging completed · port released; delivery continues"
            : p.status === "CHARGING"
              ? "At the assigned charger; energy increasing"
              : p.status === "APPROVED"
                ? "Reserved; awaiting or executing diversion"
                : p.status === "PROPOSED"
                  ? "Awaiting manager decision"
                  : p.status === "CANCELLED"
                    ? "Cancelled · reservation released"
                    : "Rejected · no diversion";
        return `<tr><td><strong>${escapeHtml(vehicle?.name || p.vin)}</strong><small>${escapeHtml(stationName(p.charger_id))} · port ${p.port_number}</small></td><td>${badge(p.status)}</td><td>${formatTime(p.start_time)}–${formatTime(p.end_time)} IST<small>Delivery deadline ${formatTime(p.delivery_deadline)}</small></td><td>${number(p.target_soc_pct, "%")}<small>${money(p.estimated_cost)} INR</small></td><td>${escapeHtml(outcome)}<br>${actionButton("view", p.vin, "View vehicle")}${["PROPOSED", "APPROVED", "CHARGING"].includes(p.status) ? actionButton("cancel", p.plan_id, "Cancel plan") : ""}<details data-plan="${escapeHtml(p.plan_id)}" ${expanded.has(p.plan_id) ? "open" : ""}><summary>Decision explanation</summary>${escapeHtml(p.reason)}</details></td></tr>`;
      })
      .join("") ||
    '<tr><td colspan="5">No decisions yet. Select a vehicle needing energy, then compare charging options.</td></tr>';
}

// One camera and one SVG. Coordinates never move to separate station icons.
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
      (Math.max(...lats) - Math.min(...lats)) * 111.195 * 1.2,
      (Math.max(...lons) - Math.min(...lons)) *
        111.195 *
        Math.cos((lat * Math.PI) / 180) *
        1.2,
    ),
  };
}
function drawMap(svg, vehicles, chargers, depots) {
  const width = Math.max(300, svg.clientWidth || 900),
    height = Math.max(280, svg.clientHeight || 450);
  svg.setAttribute?.("viewBox", `0 0 ${width} ${height}`);
  if (!viewport) viewport = fitViewport(vehicles, chargers, depots);
  const { point, scale } = mapProjection(viewport, width, height),
    v = selected(),
    plan = currentPlan();
  const coord = (lat, lon) => {
    const p = point(lat, lon);
    return `${p.x},${p.y}`;
  };
  const line = (a, b, kind, label) => {
    const p = point(a.lat, a.lon),
      q = point(b.lat, b.lon);
    const mx = Math.max(20, Math.min(width - 130, (p.x + q.x) / 2)),
      my = Math.max(30, Math.min(height - 30, (p.y + q.y) / 2));
    return `<line class="map-route ${kind}" x1="${p.x}" y1="${p.y}" x2="${q.x}" y2="${q.y}"/>${label ? `<text class="map-label" x="${mx}" y="${my - 9}">${escapeHtml(label)}</text>` : ""}`;
  };
  let routes = "",
    destination = "";
  if (v?.lat != null && v.current_trip?.destination_lat != null) {
    const target = {
      lat: v.current_trip.destination_lat,
      lon: v.current_trip.destination_lon,
    };
    routes += line(
      v,
      target,
      "delivery",
      `${number(v.delivery_remaining_km, " km")} delivery`,
    );
    const p = point(target.lat, target.lon);
    destination = `<g><circle cx="${p.x}" cy="${p.y}" r="7" fill="#f6f8f1" stroke="#7c916c" stroke-width="2"/><text class="map-label" x="${p.x + 11}" y="${p.y - 9}">${escapeHtml(v.current_trip.destination)}</text></g>`;
    const c = chargers.find((c) => c.charger_id === plan?.charger_id);
    if (c) {
      routes += line(
        v,
        c,
        plan.status === "PROPOSED" ? "proposed" : "approved",
        `${number(v.navigation_phase === "TO_CHARGER" ? v.distance_to_destination_km : plan.travel_distance_km, " km")} ${plan.status === "PROPOSED" ? "proposed" : "diversion"}`,
      );
      routes += line(c, target, "return", "");
    }
  }
  const depotMarkers = depots
    .map((d) => {
      const p = point(d.lat, d.lon);
      return `<g><title>${escapeHtml(d.name)}</title><path d="M${p.x},${p.y - 15}l15,15l-15,15l-15,-15Z" fill="#577a8c" stroke="white" stroke-width="2"/><text class="map-label" x="${p.x + 19}" y="${p.y + 18}">Depot</text></g>`;
    })
    .join("");
  const chargerMarkers = chargers
    .map((c, i) => {
      const p = point(c.lat, c.lon);
      return `<g class="map-pin" tabindex="0" role="button" data-station="${escapeHtml(c.charger_id)}" aria-label="${escapeHtml(c.name)}: ${escapeHtml(c.status)}"><title>${escapeHtml(c.name)} · ${c.free_ports} free ports</title><rect x="${p.x - 8}" y="${p.y - 8}" width="16" height="16" rx="3" fill="${c.status === "FAULTY" ? "#b96451" : "#b38b45"}" stroke="white" stroke-width="2"/><text class="map-label" x="${p.x + 11}" y="${p.y - 10}">C${i + 1}</text></g>`;
    })
    .join("");
  const clusters = [];
  for (const vehicle of vehicles.filter(
    (n) => n.lat != null && n.vin !== selectedVin,
  )) {
    const p = point(vehicle.lat, vehicle.lon),
      cluster = clusters.find((c) => Math.hypot(c.x - p.x, c.y - p.y) < 17);
    if (cluster) cluster.members.push(vehicle);
    else clusters.push({ ...p, members: [vehicle] });
  }
  let markers = clusters
    .map(
      (c) =>
        `<g class="map-pin" tabindex="0" role="button" data-select="${escapeHtml(c.members[0].vin)}" aria-label="${escapeHtml(c.members.map((v) => v.name).join(", "))}"><title>${escapeHtml(c.members.map((v) => v.name).join(", "))}</title><circle cx="${c.x}" cy="${c.y}" r="${c.members.length > 1 ? 12 : 6}" fill="#759e7a" stroke="white" stroke-width="2"/>${c.members.length > 1 ? `<text class="marker-code" x="${c.x}" y="${c.y}">${c.members.length}</text>` : ""}</g>`,
    )
    .join("");
  if (v?.lat != null) {
    const p = point(v.lat, v.lon);
    markers += `<g class="map-pin" tabindex="0" role="button" data-select="${escapeHtml(v.vin)}" aria-label="Selected ${escapeHtml(v.name)}"><title>${escapeHtml(v.name)} · ${number(v.soc_pct, "%")} · ${escapeHtml(labels[v.manager_readiness])}</title><circle class="map-halo" cx="${p.x}" cy="${p.y}" r="23"/><circle cx="${p.x}" cy="${p.y}" r="10" fill="#245b45" stroke="white" stroke-width="2"/><text class="marker-code" x="${p.x}" y="${p.y}">${escapeHtml(v.vin.slice(-2))}</text></g>`;
  }
  const clusterLabels = clusters
    .filter(
      (c) =>
        c.members.length > 1 &&
        v?.lat != null &&
        Math.hypot(c.x - point(v.lat, v.lon).x, c.y - point(v.lat, v.lon).y) <
          22,
    )
    .map(
      (c) =>
        `<text class="map-label" x="${c.x + 26}" y="${c.y - 20}">+${c.members.length} vehicles here</text>`,
    )
    .join("");
  const raw = 100 / scale,
    base = 10 ** Math.floor(Math.log10(raw)),
    distance = raw / base >= 5 ? 5 * base : raw / base >= 2 ? 2 * base : base,
    bar = distance * scale;
  svg.innerHTML = `<defs><pattern id="grid" width="45" height="45" patternUnits="userSpaceOnUse"><path d="M45 0H0V45" fill="none" stroke="#dfe8dc" stroke-width=".6"/></pattern></defs><rect width="${width}" height="${height}" fill="#f1f5ee"/><rect width="${width}" height="${height}" fill="url(#grid)"/><text class="map-text" x="17" y="22">BENGALURU · DEMO REGION</text><path d="M${width - 25} 39V18l-4 7m4-7l4 7" fill="none" stroke="#7b8d75"/><text class="map-text" x="${width - 29}" y="53">N</text>${routes}${destination}${depotMarkers}${chargerMarkers}${markers}${clusterLabels}<rect x="10" y="${height - 35}" width="${bar + 25}" height="31" rx="4" fill="#f1f5ee" fill-opacity=".92"/><path d="M20 ${height - 16}v5h${bar}v-5" fill="none" stroke="#657e59" stroke-width="1.4"/><text class="map-text" x="20" y="${height - 22}">${number(distance, " km", distance < 1 ? 1 : 0)}</text>`;
  return coord;
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
    ? `${focusBadge(v)} <strong>${escapeHtml(v.name)}</strong> · ${number(v.soc_pct, "%")} · ${number(v.delivery_remaining_km, " km delivery remaining")} · ${escapeHtml(labels[v.manager_readiness])}<br><span class="muted">${dashboard.chargers.map((c, i) => `C${i + 1} ${escapeHtml(c.name)}`).join(" · ")}</span>`
    : "Select a vehicle from the queue.";
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
      throw error;
    }
  }, "Options ready. Simulation paused for review; approval does not start it.");
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
  }, "Demo loaded and paused. Its previous decisions were cleared; other fleet records were preserved.");
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
    "Paused. The simulation clock and telemetry are frozen until Start.",
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
