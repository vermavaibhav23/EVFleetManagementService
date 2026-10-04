/* Complete journey review and minute-exact IST day charts. */
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const money = (value) => `₹${Number(value || 0).toFixed(2)}`;
const time = (value) =>
  value
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(value))
    : "—";
const slotTime = (value) =>
  new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
const dateIST = (value) =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
const minutePosition = (value, start) =>
  Math.max(0, Math.min(1440, (new Date(value) - new Date(start)) / 60000));
const bookingStyle = (row) =>
  row.plan_status === "REJECTED"
    ? "rejected"
    : row.status === "RELEASING" || row.status === "ACTIVE"
      ? "active"
      : row.status === "COMPLETED"
        ? "completed"
        : row.plan_status === "PROPOSED"
          ? "proposed"
          : ["CANCELLED", "SUPERSEDED", "INTERRUPTED"].includes(
                row.plan_status,
              ) || row.status === "CANCELLED"
            ? "cancelled"
            : "confirmed";

function stationChart(station, rates, bookings, day) {
  const x = (t) => 110 + (minutePosition(t, day.start) / 1440) * 1200;
  const maxRate = Math.max(1, ...rates.map((r) => r.price));
  const y = (p) => 90 - (p / maxRate) * 55;
  const height = 160 + station.port_count * 42;
  let svg = `<svg class="chart" viewBox="0 0 1330 ${height}" role="img" aria-label="${escapeHTML(station.name)} daily tariff and every port"><defs><pattern id="stripe-${escapeHTML(station.charger_id)}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#b2dacf"/><line x1="0" y1="0" x2="0" y2="6" stroke="#167562" stroke-width="2"/></pattern></defs><text x="0" y="38">₹ / grid kWh</text>`;
  for (let h = 0; h <= 24; h += 2) {
    const pos = 110 + (h / 24) * 1200;
    svg += `<line x1="${pos}" x2="${pos}" y1="25" y2="${height - 25}" stroke="#e4ebef"/><text x="${pos}" y="${height - 6}" text-anchor="middle">${String(h).padStart(2, "0")}:00</text>`;
  }
  for (const r of rates)
    svg += `<rect x="${x(r.start)}" y="${y(r.price)}" width="${x(r.end) - x(r.start)}" height="${90 - y(r.price)}" fill="#dcecf0"/><line x1="${x(r.start)}" x2="${x(r.end)}" y1="${y(r.price)}" y2="${y(r.price)}" stroke="#17657a" stroke-width="3"><title>${time(r.start)}–${time(r.end)} · ${money(r.price)}/kWh</title></line>`;
  for (let port = 1; port <= station.port_count; port++) {
    const top = 112 + (port - 1) * 42;
    svg += `<text x="0" y="${top + 20}">Port ${port}</text><rect x="110" y="${top}" width="1200" height="30" fill="#f3f6f7"/>`;
    for (const b of bookings.filter(
      (b) => b.charger_id === station.charger_id && b.port === port,
    )) {
      const style = bookingStyle(b);
      const fill = {
        proposed: "#fff",
        confirmed: "#4d819d",
        active: `url(#stripe-${station.charger_id})`,
        completed: "#c3d2d9",
        cancelled: "#eef0f1",
        rejected: "#eef0f1",
      }[style];
      svg += `<rect x="${x(b.start)}" y="${top + 2}" width="${Math.max(2, x(b.end) - x(b.start))}" height="26" fill="${fill}" stroke="${["cancelled", "rejected"].includes(style) ? "#87949a" : "#316e80"}" stroke-dasharray="${["proposed", "cancelled", "rejected"].includes(style) ? "4 3" : "none"}"><title>${escapeHTML(b.vin)} · ${style} · ${time(b.start)}–${time(b.end)} · ${money(b.cost)}</title></rect>`;
    }
  }
  if (
    new Date(day.clock) >= new Date(day.start) &&
    new Date(day.clock) <= new Date(day.end)
  )
    svg += `<line x1="${x(day.clock)}" x2="${x(day.clock)}" y1="15" y2="${height - 25}" stroke="#bd4637" stroke-width="2"/><text x="${x(day.clock)}" y="12" text-anchor="middle">${time(day.clock)}</text>`;
  return svg + "</svg>";
}

const human = (value) =>
  String(value || "")
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (c) => c.toUpperCase());
const number = (value, suffix = "", digits = 1) =>
  value == null ? "—" : `${Number(value).toFixed(digits)}${suffix}`;
const soc = (v) =>
  v.capacity_kwh * v.soh_pct > 0
    ? Math.min(100, (100 * v.energy_kwh) / ((v.capacity_kwh * v.soh_pct) / 100))
    : 0;
const escapeHtml = escapeHTML;
function journeyRoute(vehicle, plan, depots) {
  if (!vehicle) return [];
  if (plan && ["PROPOSED", "APPROVED", "EXECUTING"].includes(plan.status))
    return [
      vehicle,
      ...plan.operations.filter(
        (o) => !["COMPLETED", "CANCELLED"].includes(o.status),
      ),
    ];
  const stops = vehicle.deliveries
    .filter((d) => d.status !== "COMPLETED")
    .sort((a, b) => a.sequence - b.sequence);
  const depot = depots[vehicle.depot_id];
  return [
    vehicle,
    ...stops,
    ...(depot &&
    !stops.some((d) => d.is_return) &&
    vehicle.state !== "COMPLETED"
      ? [depot]
      : []),
  ];
}
function readiness(v, plans) {
  if (v.state === "COMPLETED") return "COMPLETE";
  if (v.state === "ASSISTANCE" || v.energy_kwh <= 0) return "EMERGENCY";
  if (v.health_fault || v.temperature_c >= 60) return "BLOCKED";
  const p = plans[v.plan_id];
  return p && ["APPROVED", "EXECUTING"].includes(p.status)
    ? "NORMAL"
    : "NEEDS_CHARGING";
}
if (typeof module !== "undefined")
  module.exports = {
    escapeHTML,
    money,
    time,
    dateIST,
    minutePosition,
    bookingStyle,
    stationChart,
    journeyRoute,
    readiness,
    soc,
  };
if (typeof document !== "undefined") {
  let fleet = null,
    selected = null,
    selectedPlan = null,
    acknowledged = false,
    reserveException = false,
    busy = false,
    refreshing = false,
    epoch = 0,
    dayData = null,
    activeTab = "overview",
    viewport = null,
    mapDrag = null,
    suppressMapClick = false;
  const $ = (id) => document.getElementById(id);
  const selectedVehicle = () => fleet?.vehicles[selected];
  const labels = {
    NORMAL: "Journey approved",
    NEEDS_CHARGING: "Needs planning / review",
    EMERGENCY: "Energy emergency",
    BLOCKED: "Health blocked",
    COMPLETE: "Returned to depot",
  };
  const badge = (status, label) =>
    `<span class="badge ${escapeHTML(status)}">${escapeHTML(label || labels[status] || human(status))}</span>`;
  const fact = (label, value) =>
    `<div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value)}</dd></div>`;
  const currentPlan = () => {
    const chosen = fleet?.plans[selectedPlan],
      v = selectedVehicle();
    if (
      chosen?.vin === selected &&
      ["PROPOSED", "APPROVED", "EXECUTING"].includes(chosen.status)
    )
      return chosen;
    return (
      fleet?.plans[v?.plan_id] ||
      Object.values(fleet?.plans || {})
        .filter((p) => p.vin === selected && p.status === "PROPOSED")
        .sort((a, b) => a.total_cost - b.total_cost)[0]
    );
  };
  async function api(path, body, method = "POST") {
    const response = await fetch(
      `/api/v1${path}`,
      body === undefined
        ? {}
        : {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
    );
    const data = await response.json();
    if (!response.ok)
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail),
      );
    return data;
  }
  function message(text, error = false) {
    $("message").textContent = text;
    $("message").classList.toggle("error", error);
  }
  function updateControls() {
    $("seed-button").disabled = busy;
    $("start-button").disabled = busy || !fleet || fleet.running;
    $("stop-button").disabled = busy || !fleet || !fleet.running;
    $("step").disabled = busy || !fleet || fleet.running;
    $("plan-fleet").disabled =
      busy ||
      !fleet ||
      Object.values(fleet.jobs).some((j) =>
        ["RUNNING", "QUEUED"].includes(j.status),
      );
    document.querySelectorAll("#review button, #scenario-actions button").forEach((b) => {
      b.disabled = busy || b.dataset.locked === "true";
    });
  }
  async function action(fn, success = "Updated.") {
    if (busy) return;
    busy = true;
    epoch++;
    updateControls();
    message("Updating fleet…");
    try {
      const result = await fn();
      message(typeof result === "string" ? result : success);
    } catch (error) {
      message(error.message, true);
    } finally {
      busy = false;
      epoch++;
      await refresh(true);
      updateControls();
    }
  }
  function selectVehicle(vin, tab) {
    if (!fleet?.vehicles[vin]) return;
    if (selected !== vin) {
      selectedPlan = null;
      acknowledged = false;
      reserveException = false;
    }
    selected = vin;
    if (tab) switchTab(tab);
    else render(true);
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
    $("page-title").textContent = {
      overview: "Overview",
      vehicles: "Vehicles",
      chargers: "Chargers",
      plans: "Plans & Decisions",
    }[tab];
    render(true);
    if (tab === "chargers") loadDay().catch((e) => message(e.message, true));
  }
  function vehicleCard(v) {
    return `<button class="vehicle-card ${v.vin === selected ? "selected" : ""}" data-select="${escapeHTML(v.vin)}" aria-pressed="${v.vin === selected}"><span class="row"><strong>${escapeHTML(v.name)}</strong><span class="soc">${number(soc(v), "%", 0)}</span></span><small>${human(v.state)} · ${number(v.energy_kwh, " kWh")}</small>${badge(readiness(v, fleet.plans))}</button>`;
  }
  function renderOverview() {
    const vehicles = Object.values(fleet.vehicles),
      attention = vehicles.filter(
        (v) => !["NORMAL", "COMPLETE"].includes(readiness(v, fleet.plans)),
      );
    const metrics = [
      [
        "Journey approved",
        vehicles.filter(
          (v) =>
            readiness(v, fleet.plans) === "NORMAL" && v.state !== "CHARGING",
        ).length,
        "✓",
        "",
      ],
      [
        "Needs review",
        vehicles.filter((v) => readiness(v, fleet.plans) === "NEEDS_CHARGING")
          .length,
        "ϟ",
        "warm",
      ],
      [
        "Emergency / blocked",
        vehicles.filter((v) =>
          ["EMERGENCY", "BLOCKED"].includes(readiness(v, fleet.plans)),
        ).length,
        "!",
        "red",
      ],
      [
        "Charging",
        vehicles.filter((v) => v.state === "CHARGING").length,
        "↯",
        "blue",
      ],
      [
        "Returned to depot",
        vehicles.filter((v) => v.state === "COMPLETED").length,
        "✓",
        "",
      ],
    ];
    $("metrics").innerHTML = metrics
      .map(
        ([label, count, icon, kind]) =>
          `<div class="metric ${kind}"><div><span>${label}</span><strong>${String(count).padStart(2, "0")}</strong></div><i class="metric-icon" aria-hidden="true">${icon}</i></div>`,
      )
      .join("");
    $("attention-count").textContent = attention.length;
    const query = $("vehicle-search").value.toLowerCase(),
      filter = $("readiness-filter").value;
    const priority = {
      EMERGENCY: 0,
      BLOCKED: 1,
      NEEDS_CHARGING: 2,
      NORMAL: 3,
      COMPLETE: 4,
    };
    const filtered = vehicles
      .filter(
        (v) =>
          (v.name + v.vin).toLowerCase().includes(query) &&
          (filter === "all" ||
            (filter === "attention"
              ? attention.includes(v)
              : readiness(v, fleet.plans) === filter)),
      )
      .sort(
        (a, b) =>
          priority[readiness(a, fleet.plans)] -
            priority[readiness(b, fleet.plans)] || a.vin.localeCompare(b.vin),
      );
    $("attention-list").innerHTML =
      filtered.map(vehicleCard).join("") ||
      '<p class="empty">No matching vehicles.</p>';
    $("fleet-count").textContent =
      `${filtered.length} of ${vehicles.length} vehicles · ordered by readiness urgency`;
    $("alerts-list").innerHTML =
      attention
        .map(
          (v) =>
            `<div class="alert-row">${badge(readiness(v, fleet.plans))}<div><strong>${escapeHTML(v.name)}</strong><p>${escapeHTML(v.incident || (v.health_fault ? "Health fault requires attention." : "Review a complete journey before dispatch."))}</p></div><button data-select="${escapeHTML(v.vin)}" data-select-tab="plans">Review journey →</button></div>`,
        )
        .join("") ||
      '<p class="empty">All journeys are approved or complete.</p>';
    renderSelected();
    renderMap();
  }
  function renderSelected() {
    const v = selectedVehicle();
    if (!v) {
      $("selected-detail").innerHTML = '<p class="empty">Select a vehicle.</p>';
      return;
    }
    const p = currentPlan(),
      destination = journeyRoute(v, p, fleet.depots)[1],
      deliveries = v.deliveries.filter((d) => !d.is_return),
      nextDelivery = deliveries.find((d) => d.status !== "COMPLETED");
    const disclosureOpen = $("selected-detail").querySelector("details")?.open;
    $("selected-detail").innerHTML =
      `<h2>${escapeHTML(v.name)}</h2><div class="status-row">${badge(readiness(v, fleet.plans))} ${badge("neutral", human(v.state))}</div><div class="battery-readout"><strong>${number(soc(v), "", 0)}</strong><span>% battery</span></div><div class="battery-bar ${soc(v) < 25 ? "low" : ""}"><i style="width:${soc(v)}%"></i></div><div class="detail-body"><dl class="facts">${fact(p?.status === "PROPOSED" ? "Proposed next stop" : "Next stop", destination?.name || "Journey complete")}${fact("Distance to stop", destination ? number(geographicDistance(v, destination), " km") : "—")}${fact("Next arrival deadline", nextDelivery ? time(nextDelivery.deadline) + " IST" : "—")}${fact("Deliveries completed", deliveries.filter((d) => d.status === "COMPLETED").length + " / " + deliveries.length)}</dl>${v.starting_context ? `<p class="short-reason"><strong>${escapeHTML(human(v.case))}</strong><br>Starting snapshot: ${escapeHTML(v.starting_context)}</p>` : ""}<p class="short-reason">${escapeHTML(v.incident || (p ? `${human(p.status)} · ${money(p.total_cost)} whole-journey estimate.` : "Find a journey through every delivery and back to the depot."))}</p><button class="primary" data-select="${escapeHTML(v.vin)}" data-select-tab="plans">Review journey</button><details ${disclosureOpen ? "open" : ""}><summary>Battery & route details</summary><dl class="facts">${fact("Energy", number(v.energy_kwh, " kWh"))}${fact("Normal reserve", number(fleet.policy.reserve_kwh, " kWh"))}${fact("Temperature", number(v.temperature_c, "°C"))}${fact("Connector", v.connector)}</dl></details><button class="detail-link" data-select="${escapeHTML(v.vin)}" data-select-tab="vehicles">View timetable →</button></div>`;
  }
  function renderVehicles() {
    const query = $("directory-search").value.toLowerCase(),
      vehicles = Object.values(fleet.vehicles),
      v = selectedVehicle();
    $("vehicle-total").textContent = `${vehicles.length} vehicles`;
    $("vehicle-directory").innerHTML =
      vehicles
        .filter((v) => (v.name + v.vin).toLowerCase().includes(query))
        .map(vehicleCard)
        .join("") || '<p class="empty">No matching vehicles.</p>';
    if (!v) return;
    $("vehicle-profile").innerHTML =
      `<div class="profile-heading"><span class="vehicle-monogram">${escapeHTML(v.vin.slice(-3))}</span><div><h2>${escapeHTML(v.name)}</h2><p>${number(soc(v), "%")} battery · ${human(v.state)}</p></div></div><div class="section-heading"><h2>Fixed delivery timetable</h2><span>${dateIST(fleet.clock)} · IST</span></div><ol class="itinerary">${v.deliveries
        .filter((d) => !d.is_return)
        .sort((a, b) => a.sequence - b.sequence)
        .map(
          (d) =>
            `<li><strong>${escapeHTML(d.name)}</strong> ${badge("neutral", human(d.status))}<p>Ready ${time(d.ready_at)} · accepts ${time(d.accepts_at)} · arrival deadline ${time(d.deadline)}<br>Service ${d.service_minutes} min${d.arrived_at ? " · arrived " + time(d.arrived_at) : ""}${d.completed_at ? " · completed " + time(d.completed_at) : ""}</p></li>`,
        )
        .join(
          "",
        )}<li><strong>Return to ${escapeHTML(fleet.depots[v.depot_id]?.name || "depot")}</strong><p>The journey is complete only after this return.</p></li></ol><button class="primary" data-select="${escapeHTML(v.vin)}" data-select-tab="plans">Review complete journey</button>`;
  }
  function renderJobs() {
    $("jobs").innerHTML =
      Object.values(fleet.jobs)
        .slice(-3)
        .reverse()
        .map((j) => {
          const active = ["RUNNING", "QUEUED"].includes(j.status);
          const label = active
            ? "Searching for journey options…"
            : j.status === "COMPLETED"
              ? "Search finished"
              : j.status === "CANCELLED"
                ? "Search cancelled"
                : "Search could not finish";
          return `<div class="statusline"><b>${label}</b> · ${escapeHTML(j.vin || "Fleet")} ${active ? `<button data-job="${j.job_id}">Cancel search</button>` : ""}${(j.results || []).map((r) => `<p>${escapeHTML(r.vin)} · ${escapeHTML(r.message || "Review the result below.")}</p>`).join("")}<details><summary>Technical details</summary><pre>${escapeHTML(JSON.stringify({ id: j.job_id, status: j.status, error: j.error, results: j.results }, null, 2))}</pre></details></div>`;
        })
        .join("") || '<p class="empty">No searches yet.</p>';
    $("history").innerHTML =
      fleet.history
        .slice(-20)
        .reverse()
        .map(
          (h) =>
            `<div class="history-row"><span>${time(h.at)} IST</span><span>${escapeHTML(h.vin || h.kind)}</span><span>${escapeHTML(h.message)}</span></div>`,
        )
        .join("") || '<p class="empty">No operational events yet.</p>';
  }
  function render(force = false) {
    if (!fleet) return;
    $("sim-clock").textContent =
      `${fleet.running ? "Running · " + fleet.speed + "×" : "Paused"} · ${dateIST(fleet.clock)}, ${time(fleet.clock)} IST`;
    $("scenario-guide").innerHTML =
      `<span class="badge neutral">Scenario</span><strong>${escapeHTML(fleet.scenario_title || "Legacy run")}</strong><span class="muted">${escapeHTML(fleet.scenario_description || "Choose one of the four new scenarios, then Load / reset.")}</span>`;
    $("scenario-actions").innerHTML = (fleet.scenario_actions || []).length
      ? `<strong>Test one incident at a time</strong><span class="muted">Each action pauses the clock for review. Reload to replay.</span><div class="incident-buttons">${fleet.scenario_actions.map(a => `<div><button data-incident="${escapeHTML(a.event_id)}" data-locked="${Boolean(a.blocked_reason)}" ${a.blocked_reason ? "disabled" : ""}>${escapeHTML(a.label)} · ${escapeHTML(fleet.vehicles[a.vin].name)}</button>${a.blocked_reason ? `<small>${escapeHTML(a.blocked_reason)}</small>` : ""}</div>`).join("")}</div>` : "";
    $("freshness").textContent =
      `${Object.keys(fleet.vehicles).length} vehicles · ${fleet.running ? "Live simulation" : "Telemetry frozen while paused"} · updated ${new Date().toLocaleTimeString()}`;
    if (activeTab === "overview") renderOverview();
    if (activeTab === "vehicles") renderVehicles();
    if (activeTab === "plans") {
      renderReview(force);
      renderJobs();
    }
    updateControls();
  }
  function renderMap() {
    if (!fleet) return;
    const vehicles = Object.values(fleet.vehicles),
      clock = new Date(fleet.clock);
    const chargers = Object.values(fleet.stations).map((s) => ({
      ...s,
      occupied_ports: new Set(
        (fleet.reservations || [])
          .filter(
            (b) =>
              b.charger_id === s.charger_id &&
              new Date(b.start) <= clock &&
              clock < new Date(b.end),
          )
          .map((b) => b.port),
      ).size,
      waiting_count: vehicles.filter(
        (v) =>
          ["QUEUING"].includes(v.state) &&
          fleet.plans[v.plan_id]?.operations[v.operation_index]?.charger_id ===
            s.charger_id,
      ).length,
    }));
    $("map-empty").hidden = vehicles.length + chargers.length > 0;
    drawMap($("fleet-map"), vehicles, chargers, Object.values(fleet.depots));
    const v = selectedVehicle(),
      p = currentPlan(),
      destination = journeyRoute(v, p, fleet.depots)[1];
    $("map-selection").textContent = v
      ? `${v.name} · ${human(v.state)} · ${number(soc(v), "%")} · ${p?.status === "PROPOSED" ? "Proposed: " : ""}${destination?.name || "Journey complete"}`
      : "Select a vehicle.";
  }
  async function refresh(force = false) {
    if (refreshing && !force) return;
    refreshing = true;
    const ticket = epoch;
    try {
      const result = await api("/fleet");
      if (ticket !== epoch) return;
      if (fleet?.run_id !== result.run_id) {
        selected = null;
        selectedPlan = null;
        viewport = null;
        acknowledged = false;
        reserveException = false;
        $("day").value = "";
        const supported = [...$("scenario").options].some(o => o.value === result.scenario);
        $("scenario").value = supported ? result.scenario : "EVERYDAY_CHOICES";
        $("vehicle-count").value = Math.max(4, Object.keys(result.vehicles).length);
        $("seed").value = result.seed;
        $("start").value =
          dateIST(result.start_time) + "T" + time(result.start_time);
        $("speed").value = String(result.speed);
      }
      fleet = result;
      if (!fleet.vehicles[selected]) selected = Object.keys(fleet.vehicles)[0];
      $("system-label").textContent = "Fleet connected";
      $("live-dot").classList.add("online");
      render(force);
      if (activeTab === "chargers") await loadDay();
    } catch (e) {
      if (ticket !== epoch) return;
      $("system-label").textContent = fleet
        ? "Connection interrupted"
        : "Load a scenario";
      $("live-dot").classList.remove("online");
      message(e.message, true);
      if (!fleet) {
        $("sim-clock").textContent = "No scenario loaded";
        $("attention-list").innerHTML =
          '<p class="empty">Choose a scenario and select Load / reset.</p>';
      }
    } finally {
      refreshing = false;
      updateControls();
    }
  }
  const clockRequest = () => ({
    run_id: fleet.run_id,
    speed: Number($("speed").value),
  });
  $("seed-button").onclick = () => {
    const count = Number($("vehicle-count").value),
      min = 4;
    if (
      !Number.isInteger(count) ||
      count < min ||
      count > 100 ||
      !$("start").value ||
      !Number.isInteger(Number($("seed").value))
    ) {
      message(
        `Choose ${min}–100 vehicles, an integer seed and an IST start time.`,
        true,
      );
      return;
    }
    action(
      () =>
        api("/simulator/load", {
          scenario: $("scenario").value,
          vehicle_count: count,
          seed: Number($("seed").value),
          start_time: $("start").value + ":00+05:30",
        }),
      "Scenario loaded with your selected fleet size · paused. Review vehicle roles and incident controls before starting.",
    );
  };
  $("scenario").onchange = () => {
    $("vehicle-count").min = 4;
    if (Number($("vehicle-count").value) < 4) $("vehicle-count").value = 4;
  };
  $("start-button").onclick = () =>
    action(
      () => api("/simulator/start", clockRequest()),
      "Simulation started.",
    );
  $("stop-button").onclick = () =>
    action(() => api("/simulator/pause", clockRequest()), "Simulation paused.");
  $("step").onclick = () =>
    action(
      () => api("/simulator/tick", { ...clockRequest(), seconds: 300 }),
      "Advanced five simulated minutes.",
    );
  $("plan-fleet").onclick = () =>
    action(async () => {
      await api("/journeys/plan", { alternatives: false });
      switchTab("plans");
    }, "Fleet planning queued. Review and approve each complete journey.");
  $("refresh-button").onclick = () => refresh(true);
  $("vehicle-search").oninput = $("readiness-filter").onchange = () => {
    if (fleet) renderOverview();
  };
  $("directory-search").oninput = () => {
    if (fleet) renderVehicles();
  };
  $("day").onchange = () => loadDay().catch((e) => message(e.message, true));
  for (const [id, delta] of [
    ["prev-day", -1],
    ["next-day", 1],
  ])
    $(id).onclick = () => {
      if (!$("day").value) return;
      const date = new Date($("day").value + "T12:00:00Z");
      date.setUTCDate(date.getUTCDate() + delta);
      $("day").value = date.toISOString().slice(0, 10);
      loadDay().catch((e) => message(e.message, true));
    };
  $("review").onchange = (e) => {
    if (e.target.name === "alternative") {
      selectedPlan = e.target.value;
      acknowledged = false;
      renderReview(true);
    }
    if (e.target.id === "ack") acknowledged = e.target.checked;
    if (e.target.id === "reserve-exception")
      reserveException = e.target.checked;
  };
  document.addEventListener("click", (e) => {
    if (suppressMapClick && e.target.closest("svg")) {
      suppressMapClick = false;
      return;
    }
    const tab = e.target.closest("[data-tab]");
    if (tab) {
      switchTab(tab.dataset.tab);
      return;
    }
    const choice = e.target.closest("[data-select]");
    if (choice) {
      selectVehicle(choice.dataset.select, choice.dataset.selectTab);
      return;
    }
    const station = e.target.closest("[data-station]");
    if (station) {
      switchTab("chargers");
      return;
    }
    const map = e.target.closest("[data-map]");
    if (map && fleet) {
      if (map.dataset.map === "fit")
        viewport = fitViewport(
          Object.values(fleet.vehicles),
          Object.values(fleet.stations),
          Object.values(fleet.depots),
        );
      if (map.dataset.map === "in" && viewport)
        viewport.spanKm = Math.max(0.5, viewport.spanKm / 1.2);
      if (map.dataset.map === "out" && viewport)
        viewport.spanKm = Math.min(1000, viewport.spanKm * 1.2);
      renderMap();
      return;
    }
    const b = e.target.closest("button");
    if (!b || busy || !fleet) return;
    if (b.dataset.incident) {
      action(async () => {
        const response = await api(`/simulator/actions/${encodeURIComponent(b.dataset.incident)}`, {run_id: fleet.run_id});
        return response.message;
      });
      return;
    }
    if (b.dataset.plan !== undefined && selected)
      action(
        () =>
          api(`/vehicles/${encodeURIComponent(selected)}/journeys/plan`, {
            recovery: b.dataset.plan === "recovery",
            reserve_exception:
              b.dataset.plan === "recovery" && reserveException,
            alternatives: true,
          }),
        "Planning queued. The current journey remains in place until a replacement is approved.",
      );
    if (b.dataset.approve)
      action(async () => {
        const response = await api(
          `/journeys/${encodeURIComponent(b.dataset.approve)}/approve`,
          {
            run_id: fleet.run_id,
            version: fleet.plans[b.dataset.approve].version,
            acknowledge_recovery: acknowledged,
          },
        );
        return (
          response.message ||
          "Journey approved. All requested slots booked together."
        );
      }, "Complete journey approved.");
    if (b.dataset.refresh) {
      const plan = fleet.plans[b.dataset.refresh];
      action(
        () =>
          api(`/vehicles/${encodeURIComponent(plan.vin)}/journeys/plan`, {
            recovery: plan.recovery,
            reserve_exception: plan.reserve_kwh < fleet.policy.reserve_kwh,
            alternatives: true,
          }),
        "Searching the latest available slots. Review the new option before booking.",
      );
    }
    if (b.dataset.reject)
      action(
        () =>
          api(`/journeys/${encodeURIComponent(b.dataset.reject)}/reject`, {
            run_id: fleet.run_id,
            version: fleet.plans[b.dataset.reject].version,
          }),
        "Option rejected. No charging slots booked.",
      );
    if (b.dataset.cancel)
      action(
        () =>
          api(`/journeys/${encodeURIComponent(b.dataset.cancel)}/cancel`, {
            run_id: fleet.run_id,
            version: fleet.plans[b.dataset.cancel].version,
          }),
        "Remaining journey cancelled; physical release is retained.",
      );
    if (b.dataset.job)
      action(
        () =>
          api(`/jobs/${encodeURIComponent(b.dataset.job)}/cancel`, {
            run_id: fleet.run_id,
          }),
        "Planner job cancelled.",
      );
  });
  document.addEventListener("keydown", (e) => {
    if (
      ["Enter", " "].includes(e.key) &&
      e.target.matches?.("svg [role=button]")
    ) {
      e.preventDefault();
      e.target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
  });
  $("fleet-map").addEventListener("pointerdown", (e) => {
    if (!viewport) return;
    mapDrag = { x: e.clientX, y: e.clientY, camera: { ...viewport } };
    suppressMapClick = false;
  });
  $("fleet-map").addEventListener("pointermove", (e) => {
    if (!mapDrag) return;
    const dx = e.clientX - mapDrag.x,
      dy = e.clientY - mapDrag.y;
    if (Math.hypot(dx, dy) < 4) return;
    suppressMapClick = true;
    const { scale } = mapProjection(
      mapDrag.camera,
      Math.max(300, $("fleet-map").clientWidth || 900),
      Math.max(280, $("fleet-map").clientHeight || 450),
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
  document.addEventListener("pointerup", () => (mapDrag = null));
  $("fleet-map").addEventListener("pointercancel", () => {
    mapDrag = null;
    suppressMapClick = false;
  });
  $("fleet-map").addEventListener(
    "wheel",
    (e) => {
      if (!viewport) return;
      e.preventDefault();
      viewport.spanKm = Math.max(
        1,
        Math.min(
          150,
          viewport.spanKm *
            Math.exp(
              Math.max(
                -20,
                Math.min(20, e.deltaY * (e.deltaMode === 1 ? 12 : 1)),
              ) * (e.ctrlKey ? 0.0008 : 0.002),
            ),
        ),
      );
      renderMap();
    },
    { passive: false },
  );
  window.addEventListener("resize", () => {
    if (activeTab === "overview") renderMap();
  });
  updateControls();
  refresh();
  setInterval(() => {
    if (!busy) refresh();
  }, 3000);

  function renderReview() {
    if (!selected || !fleet.vehicles[selected]) return;
    const open = new Set(
      [...$("review").querySelectorAll("details[open]")].map(
        (d) => d.dataset.detail,
      ),
    );
    const focused = document.activeElement?.closest("#review")
      ? document.activeElement.id
      : null;
    const v = fleet.vehicles[selected];
    const plans = Object.values(fleet.plans)
      .filter((p) => p.vin === selected)
      .sort((a, b) => {
        const rank = (p) =>
          p.review?.can_approve
            ? 0
            : ["APPROVED", "EXECUTING"].includes(p.status)
              ? 1
              : 2;
        return (
          rank(a) - rank(b) ||
          (rank(a) === 0
            ? a.total_cost - b.total_cost
            : new Date(b.created_at) - new Date(a.created_at))
        );
      });
    const available = plans.filter((p) => p.review?.can_approve);
    if (!available.some((p) => p.plan_id === selectedPlan)) {
      const replacement = available[0]?.plan_id || null;
      if (replacement !== selectedPlan) acknowledged = false;
      selectedPlan = replacement;
    }
    let html = `<h3>${escapeHTML(v.name)} · ${human(v.state)}</h3>${v.starting_context ? `<p class="muted">Starting snapshot: ${escapeHTML(v.starting_context)}</p>` : ""}<p>${escapeHTML(v.incident || "Compare the complete journey, then approve its charging slots.")}</p>
      <div class="controls"><button data-plan="normal">Find on-time options</button><button data-plan="recovery">Review options allowing delays</button>
      <label><input id="reserve-exception" type="checkbox" ${reserveException ? "checked" : ""}> Allow reduced reserve in delayed options</label></div>
      <p class="muted">Nothing is booked until approval. All slots are checked again before booking.</p><div class="journey-options">`;
    for (const plan of plans) {
      const r = plan.review;
      if (!r) continue;
      const locked =
        !r.can_approve &&
        !["APPROVED", "EXECUTING", "COMPLETED"].includes(r.state);
      const chosen = selectedPlan === plan.plan_id;
      const label = r.can_approve
        ? plan.plan_id === available[0]?.plan_id
          ? "Recommended - lowest cost shown"
          : "Alternative"
        : {
            OUTDATED: "Needs updating",
            REJECTED: "Rejected by manager",
            SUPERSEDED: "Not selected / replaced",
            INTERRUPTED: "Journey interrupted",
            CANCELLED: "Cancelled",
            APPROVED: "Approved",
            EXECUTING: "In progress",
            COMPLETED: "Journey finished",
          }[r.state] || human(r.state);
      html += `<article class="plan ${locked ? "plan-locked" : ""} ${chosen ? "chosen" : ""}"><div class="plan-head"><div><h3>${r.can_approve ? `<label><input type="radio" name="alternative" value="${plan.plan_id}" ${chosen ? "checked" : ""}> ${label}</label>` : label}</h3><small>${plan.recovery ? "Allows delivery delays" : "On-time journey"}</small></div><div class="price">${money(plan.total_cost)}<small>${locked ? "earlier estimate" : "total journey charging cost"}</small></div></div>
        <ul class="plan-pointers">${r.reason ? `<li><b>${escapeHTML(r.reason)}</b></li>` : ""}<li>${locked ? "Earlier estimate: " : ""}${escapeHTML(r.delivery_summary)}</li><li>Return ${time(r.return_at)} IST with ${r.return_pct}% battery.</li><li>Reserve floor: ${r.reserve_pct}% (${plan.reserve_kwh} kWh).</li></ul>`;
      const booked = ["APPROVED", "EXECUTING"].includes(plan.status);
      html += `<h4>${booked ? "Booked charging slots" : locked ? "Earlier requested slots" : r.state === "COMPLETED" ? "Charging slots used" : "Slots requested on approval"}</h4>`;
      if (!r.slots.length)
        html += `<p class="muted">${locked ? "This earlier option required no charging slots." : "No charging slots needed. Existing battery covers the remaining deliveries and depot return."}</p>`;
      for (const slot of r.slots)
        html += `<div class="requested-slot"><b>${escapeHTML(slot.station)} · Port ${slot.port}</b><p>${dateIST(slot.start)} · ${slotTime(slot.start)} → ${dateIST(slot.end) !== dateIST(slot.start) ? dateIST(slot.end) + " " : ""}${slotTime(slot.end)} IST <small>Includes connection and release</small></p><ul class="plan-pointers"><li><b>Charge ${slot.arrival_pct}% → ${slot.target_pct}%</b> · add ${slot.battery_added_kwh} kWh.</li><li>${escapeHTML(slot.reason)}</li>${slot.carry_reason ? `<li>${escapeHTML(slot.carry_reason)}</li>` : ""}<li>Buy ${slot.grid_kwh.toFixed(2)} grid kWh · ${money(slot.cost)}.</li></ul></div>`;
      if (r.can_approve)
        html += `<p class="slot-check">Available in the latest check; rechecked together when you approve.</p>`;
      if (r.can_approve && chosen) {
        if (plan.recovery)
          html += `<label class="ack"><input id="ack" type="checkbox" ${acknowledged ? "checked" : ""}> I accept the displayed delays and ${r.reserve_pct}% reserve floor.</label>`;
        html += `<div class="actions"><button class="primary" data-approve="${plan.plan_id}">${r.slots.length ? "Approve journey & book " + r.slots.length + " slot" + (r.slots.length === 1 ? "" : "s") : "Approve journey"}</button><button data-reject="${plan.plan_id}">Reject option</button></div>`;
      }
      if (r.state === "OUTDATED")
        html += `<button class="primary" data-refresh="${plan.plan_id}">Refresh options</button>`;
      if (booked)
        html += `<button data-cancel="${plan.plan_id}">Cancel remaining journey</button>`;
      html += `<details class="journey-details" data-detail="${plan.plan_id}" ${open.has(plan.plan_id) ? "open" : ""}><summary>View journey details</summary><div class="table-wrap"><table><thead><tr><th>Stop</th><th>Arrive</th><th>Finish</th><th>Deadline</th><th>Battery kWh</th><th>Cost</th></tr></thead><tbody>`;
      for (const op of plan.operations)
        html += `<tr><td>${escapeHTML(op.name)}<small>${human(op.status)}</small></td><td>${time(op.arrival)}</td><td>${time(op.end)}</td><td>${time(op.deadline)}${op.lateness_minutes > 0.001 ? `<br>${op.lateness_minutes.toFixed(1)} min late` : ""}</td><td>${op.energy_arrival.toFixed(2)} → ${op.energy_end.toFixed(2)}</td><td>${money(op.cost)}</td></tr>`;
      html += `</tbody></table></div><small>Estimate calculated for this option; later changes require a fresh review.</small></details></article>`;
    }
    const latest = Object.values(fleet.jobs)
      .slice()
      .reverse()
      .find((j) => (j.results || []).some((r) => r.vin === selected));
    for (const row of (latest?.results || []).filter(
      (r) => r.vin === selected && !r.plan_id,
    ))
      html += `<article class="plan plan-locked"><h3>No usable option</h3><ul class="plan-pointers"><li>${escapeHTML(row.message || "Refresh options to search again.")}</li><li>No charging slots booked.</li></ul></article>`;
    if (!plans.length && !latest)
      html +=
        '<p class="empty">Choose Find on-time options to compare journeys.</p>';
    html += "</div>";
    $("review").innerHTML = html;
    if (focused && $(focused)) $(focused).focus({ preventScroll: true });
  }

  async function loadDay() {
    if (!fleet) return;
    if (!$("day").value) $("day").value = dateIST(fleet.clock);
    const requestedRun = fleet.run_id,
      requestedDay = $("day").value;
    const result = await api(`/day-view?day=${requestedDay}`);
    if (fleet.run_id !== requestedRun || $("day").value !== requestedDay)
      return;
    dayData = result;
    $("charts").innerHTML = Object.values(dayData.stations)
      .map(
        (s) =>
          `<article class="station"><div class="section-head"><h3>${escapeHTML(s.name)}</h3><span class="badge neutral">${escapeHTML(s.status)} · ${s.power_kw} kW · ${s.port_count} ports</span></div><div class="chart-scroll">${stationChart(s, dayData.tariffs[s.charger_id], dayData.bookings, dayData)}</div><div class="rates" aria-label="Exact price intervals">${dayData.tariffs[s.charger_id].map((r) => `<span>${time(r.start)}–${new Date(r.end).getTime() === new Date(dayData.end).getTime() ? "24:00" : time(r.end)} · <b>${money(r.price)}/kWh</b></span>`).join("")}</div><div class="table-wrap"><table><thead><tr><th>Port</th><th>Vehicle / option</th><th>Start → end</th><th>Status</th></tr></thead><tbody>${
            dayData.bookings
              .filter((b) => b.charger_id === s.charger_id)
              .map(
                (b) =>
                  `<tr><td>${b.port}</td><td>${escapeHTML(b.vin)} / ${b.plan_id.slice(0, 8)}</td><td>${time(b.start)} → ${time(b.end)}</td><td>${bookingStyle(b)} · ${b.status}</td></tr>`,
              )
              .join("") ||
            `<tr><td colspan="4">All ports have no bookings on this day.</td></tr>`
          }</tbody></table></div></article>`,
      )
      .join("");
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
    const v = selectedVehicle();
    const nodes = [...vehicles, ...chargers, ...depots].filter(
      (n) => n.lat != null && n.lon != null,
    );
    for (const van of vehicles)
      for (const t of van.deliveries || [])
        if (t.status !== "COMPLETED" && t.lat != null)
          nodes.push({ lat: t.lat, lon: t.lon });
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
      v = selectedVehicle(),
      plan = currentPlan();
    const line = (a, b, kind) => {
      const p = point(a.lat, a.lon),
        q = point(b.lat, b.lon);
      return `<line class="map-route ${kind}" ${kind === "approved" ? 'marker-end="url(#charger-arrow)"' : ""} x1="${p.x}" y1="${p.y}" x2="${q.x}" y2="${q.y}"/>`;
    };
    const route = journeyRoute(v, plan, fleet?.depots || {});
    let routes = route
      .slice(1)
      .map((node, i) =>
        line(
          route[i],
          node,
          plan?.status === "PROPOSED"
            ? "proposed"
            : plan && ["APPROVED", "EXECUTING"].includes(plan.status)
              ? "approved"
              : "delivery",
        ),
      )
      .join("");
    const customers = new Map();
    for (const van of vehicles)
      for (const t of van.deliveries || []) {
        if (t.status === "COMPLETED" || t.lat == null || t.is_return) continue;
        const k = `${t.lat.toFixed(4)},${t.lon.toFixed(4)}`;
        if (!customers.get(k)?.selected)
          customers.set(k, {
            lat: t.lat,
            lon: t.lon,
            name: t.name,
            selected: van.vin === selected,
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
        return `<g class="map-pin" tabindex="0" role="button" data-station="${escapeHtml(c.charger_id)}" aria-label="${escapeHtml(c.name)}"><rect x="${p.x - 12}" y="${p.y - 15}" width="24" height="30" rx="6" fill="${c.status !== "AVAILABLE" ? "#d85658" : "#efb444"}" stroke="white" stroke-width="2"/><text x="${p.x}" y="${p.y + 5}" text-anchor="middle" fill="#593800" font-size="20">ϟ</text><text class="map-label" text-anchor="${p.x > width * 0.6 ? "end" : "start"}" x="${p.x > width * 0.6 ? p.x - 17 : p.x + 17}" y="${p.y - 14}">${escapeHtml(c.name)}</text><text class="map-label station-count" text-anchor="${p.x > width * 0.6 ? "end" : "start"}" x="${p.x > width * 0.6 ? p.x - 17 : p.x + 17}" y="${p.y + 2}">${c.occupied_ports}/${c.port_count} occupied · ${c.waiting_count || 0} waiting</text></g>`;
      })
      .join("");
    const placed = [];
    const sorted = [...vehicles.filter((v) => v.lat != null)].sort(
      (a, b) => (a.vin === selected ? 1 : 0) - (b.vin === selected ? 1 : 0),
    );
    const markers = sorted
      .map((van) => {
        const p = point(van.lat, van.lon);
        let x = p.x,
          y = p.y;
        const atStation = chargers.some(
          (c) => geographicDistance(van, c) < 0.05,
        );
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
        const charging = van.state === "CHARGING",
          waiting = ["QUEUING", "WAITING_WINDOW", "RELEASING"].includes(
            van.state,
          ),
          emergency = ["ASSISTANCE"].includes(van.state),
          focus = van.vin === selected;
        const fill = charging
          ? "#17a66a"
          : waiting
            ? "#8a939e"
            : emergency
              ? "#df5a62"
              : van.state === "WAITING_REVIEW"
                ? "#ee964b"
                : "#338de0";
        return `<g class="map-pin vehicle-pin ${charging ? "charging-glow" : ""}" tabindex="0" role="button" data-select="${escapeHtml(van.vin)}" aria-label="${escapeHtml(van.name + " " + human(van.state))}"><title>${escapeHtml(van.name)} · ${human(van.state)} · ${number(soc(van), "%")}</title>${Math.hypot(x - p.x, y - p.y) > 2 ? `<line x1="${p.x}" y1="${p.y}" x2="${x}" y2="${y}" stroke="${fill}" stroke-width="1" opacity=".45"/><circle cx="${p.x}" cy="${p.y}" r="2" fill="${fill}"/>` : ""}${focus ? `<rect x="${x - 19}" y="${y - 14}" width="38" height="28" rx="9" fill="none" stroke="#183d61" stroke-width="2"/>` : ""}<rect class="vehicle-body" x="${x - 14}" y="${y - 9}" width="28" height="18" rx="6" fill="${fill}" stroke="white" stroke-width="2"/><rect x="${x - 9}" y="${y - 12}" width="5" height="3" rx="1" fill="#354052"/><rect x="${x + 4}" y="${y + 9}" width="5" height="3" rx="1" fill="#354052"/><text class="marker-code" x="${x}" y="${y}">${escapeHtml(van.vin.slice(-2))}</text></g>`;
      })
      .join("");
    const bar = 5 * scale;
    svg.innerHTML = `<defs><marker id="charger-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#2563eb"/></marker><pattern id="grid" width="46" height="46" patternUnits="userSpaceOnUse"><path d="M46 0H0V46" fill="none" stroke="#d6e5ee" stroke-width=".6"/></pattern></defs><rect width="${width}" height="${height}" fill="#f1f7fc"/><rect width="${width}" height="${height}" fill="url(#grid)"/>${routes}${pins}${depotPins}${stationPins}${markers}<path d="M18 ${height - 20}v5h${bar}v-5" fill="none" stroke="#526b81"/><text class="map-text" x="18" y="${height - 27}">5 km</text>`;
  }
}
