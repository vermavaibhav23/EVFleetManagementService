const api = "/api/v1";
const byId = (id) => document.getElementById(id);
const formatTime = (value) => value ? new Date(value).toLocaleString() : "—";
const number = (value, suffix = "") => value == null ? "—" : `${Number(value).toFixed(1)}${suffix}`;
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let dashboard = null;
let connected = false;
let busy = false;
let epoch = 0;
let refreshInFlight = null;
let refreshAgain = false;

async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, {headers: {"Content-Type":"application/json"}, signal: AbortSignal.timeout(20000), ...options});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map(item => `${item.loc?.slice(1).join(" ")}: ${item.msg}`).join("; ") : response.status === 503 ? "A service dependency is unavailable. Reconnecting…" : `Request failed (${response.status}). Try again.`;
    throw new Error(message);
  }
  return payload;
}
function setMessage(text, error = false) {
  byId("message").textContent = text;
  byId("message").classList.toggle("error", error);
}
function updateControls() {
  document.querySelectorAll("button[data-action]").forEach(button => button.disabled = busy || !connected);
  byId("seed-button").disabled = busy || !connected;
  byId("start-button").disabled = busy || !connected || !dashboard?.overview.vehicles || dashboard?.simulator.running;
  byId("stop-button").disabled = busy || !connected || !dashboard?.simulator.running;
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
  })().finally(() => { refreshInFlight = null; });
  return refreshInFlight;
}
async function loadDashboard() {
  const version = epoch;
  try {
    const [overview, fleet, alerts, plans, simulator, chargers, depots, health] = await Promise.all([
      request("/fleet/overview"),request("/fleet/vehicles?limit=200"),request("/alerts?limit=100"),request("/charging/plans?limit=100"),request("/simulator/status"),request("/chargers"),request("/depots"),request("/health/ready"),
    ]);
    if (version !== epoch || busy) return;
    dashboard = {overview,fleet,alerts,plans,simulator,chargers,depots,health};
    if (!connected && byId("message").classList.contains("error")) setMessage("Connection restored. Live data is up to date.");
    connected = true;
    byId("live-dot").classList.add("online");
    byId("system-label").textContent = "API connected";
    byId("metric-vehicles").textContent = overview.vehicles;
    byId("metric-critical").textContent = overview.critical_alerts;
    byId("metric-alerts").textContent = overview.open_alerts;
    byId("metric-plans").textContent = overview.active_charging_plans;
    byId("metric-chargers").textContent = `${overview.available_chargers} / ${overview.chargers}`;
    byId("sim-clock").textContent = simulator.error ? simulator.error : `${simulator.running ? `Running at ${simulator.time_scale}×` : "Stopped"} · ${simulator.emitted_events} events · ${formatTime(simulator.simulated_time)}`;
    byId("sim-clock").classList.toggle("error",!!simulator.error);
    renderVehicles(fleet.vehicles,plans);
    renderMap(fleet.vehicles,chargers,depots);
    renderAlerts(alerts);
    renderPlans(plans);
  } catch (error) {
    if (version !== epoch) return;
    connected = false;
    byId("live-dot").classList.remove("online");
    byId("system-label").textContent = "Reconnecting · data may be stale";
    setMessage(error.name === "TimeoutError" ? "The API took too long to respond. Retrying automatically." : error.message, true);
  } finally { updateControls(); }
}
function actionButton(action,id,label,primary=false) {
  return `<button class="table-action ${primary ? "primary" : ""}" data-action="${action}" data-id="${escapeHtml(id)}">${label}</button>`;
}
function renderVehicles(vehicles, plans) {
  const priority = {CRITICAL:0,CHARGE_SOON:1,UNKNOWN:2,SAFE:3};
  const active = new Map(plans.filter(p => ["PROPOSED","APPROVED","CHARGING"].includes(p.status)).map(p => [p.vin,p]));
  byId("vehicles-body").innerHTML = [...vehicles].sort((a,b) => (priority[a.readiness]??9)-(priority[b.readiness]??9)).map(v => {
    const plan = active.get(v.vin);
    const health = (v.health_flags || []).join(", ");
    const blocked = (v.health_flags || []).some(flag => ["HIGH_BATTERY_TEMPERATURE","LOW_BATTERY_TEMPERATURE","DIAGNOSTIC_TROUBLE_CODE"].includes(flag));
    const action = plan ? actionButton("view",v.vin,"View plan") : blocked ? "Inspect battery warning" : ["CRITICAL","CHARGE_SOON"].includes(v.readiness) ? actionButton("generate",v.vin,"Generate plan") : "—";
    return `<tr><td><strong>${escapeHtml(v.name)}</strong><br><small>${escapeHtml(v.vin)}</small></td>
    <td><span class="state-label ${escapeHtml(v.operating_state)}">${escapeHtml(v.operating_state)}</span>${health ? `<br><small class="error">${escapeHtml(health)}</small>` : ""}</td>
    <td>${escapeHtml(v.navigation_target || "—")}<br><small>${number(v.distance_to_destination_km," km")} · ${number(v.eta_minutes," min")}</small></td>
    <td>${number(v.soc_pct,"%")}</td><td>${number(v.current_range_km," km")}</td><td>${number(v.post_trip_range_km," km")}</td><td>${number(v.range_margin_km," km")}</td>
    <td><span class="badge ${escapeHtml(v.readiness)}">${escapeHtml(v.readiness)}</span></td><td>${action}</td></tr>`;
  }).join("") || '<tr><td colspan="9">No fleet loaded. Seed a scenario to begin.</td></tr>';
  byId("fleet-count").textContent = vehicles.length < dashboard.fleet.total ? `Showing ${vehicles.length} of ${dashboard.fleet.total} vehicles` : `${vehicles.length} vehicles · scroll tables horizontally on small screens`;
}
// Both views use the same physical coordinates. The detail view has its own scale.
function drawMap(svg, vehicles, chargers, depots, detail) {
  const width = Math.max(320, svg.clientWidth || 1000), height = Math.max(280, svg.clientHeight || 420);
  svg.setAttribute?.("viewBox", `0 0 ${width} ${height}`);
  const depot = depots[0];
  const near = v => !detail || !depot || Math.hypot((v.lat-depot.lat)*111,(v.lon-depot.lon)*108)<12;
  const nodes = [
    ...depots.map((v,i) => ({...v,key:`D${i+1}`,kind:"depot",name:v.name})),
    ...chargers.map((v,i) => ({...v,key:`C${i+1}`,kind:"charger",name:`${v.name}: ${v.status}, ${v.free_ports} free ports`})),
    ...vehicles.filter(v => v.lat!=null && near(v)).map((v,i) => ({...v,key:`V${vehicles.indexOf(v)+1}`,kind:"vehicle",name:`${v.name}: ${v.operating_state}, ${number(v.soc_pct,"%")}`})),
    ...vehicles.filter(v => v.destination_lat!=null && !detail).map(v => ({lat:v.destination_lat,lon:v.destination_lon,key:"",kind:"destination",name:v.navigation_target})),
  ];
  if (!nodes.length) { svg.innerHTML=""; return; }
  const cos = Math.cos((depot?.lat || nodes[0].lat)*Math.PI/180);
  const xs = nodes.map(n=>n.lon*cos), ys=nodes.map(n=>n.lat);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const spanX=Math.max(.015,maxX-minX),spanY=Math.max(.015,maxY-minY);
  const scale=Math.min((width-80)/spanX,(height-80)/spanY);
  const project=(lat,lon)=>({x:width/2+(lon*cos-(minX+maxX)/2)*scale,y:height/2-(lat-(minY+maxY)/2)*scale});
  const routes=vehicles.filter(v=>v.lat!=null&&v.destination_lat!=null&&(!detail||near(v))).map(v=>{
    const a=project(v.lat,v.lon),b=project(v.destination_lat,v.destination_lon);
    return `<line class="map-route ${v.navigation_phase==="TO_CHARGER"?"charger-route":"delivery-route"}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`;
  }).join("");
  const placed=[];
  const markers=nodes.map(n=>{
    const real=project(n.lat,n.lon);
    let p={...real};
    // Screen-space displacement only; leader lines preserve the geographic point.
    for(let attempt=0;attempt<300&&placed.some(q=>Math.hypot(p.x-q.x,p.y-q.y)<29);attempt++) {
      const radius=18*Math.sqrt(attempt+1),angle=attempt*2.4;
      p={x:Math.max(22,Math.min(width-22,real.x+Math.cos(angle)*radius)),y:Math.max(22,Math.min(height-22,real.y+Math.sin(angle)*radius))};
    }
    placed.push(p);
    const shape=n.kind==="charger"?`<rect class="map-charger" x="${p.x-13}" y="${p.y-13}" width="26" height="26" rx="3"/>`:n.kind==="depot"?`<path class="map-depot" d="M ${p.x} ${p.y-16} L ${p.x+16} ${p.y} L ${p.x} ${p.y+16} L ${p.x-16} ${p.y} Z"/>`:`<circle class="map-${n.kind}" cx="${p.x}" cy="${p.y}" r="${n.kind==="destination"?5:13}"/>`;
    return `<g tabindex="0" role="button" data-map-name="${escapeHtml(n.name)}" aria-label="${escapeHtml(n.name)}"><title>${escapeHtml(n.name)}</title><line class="marker-leader" x1="${real.x}" y1="${real.y}" x2="${p.x}" y2="${p.y}"/>${shape}<text class="marker-code" x="${p.x}" y="${p.y+3}">${n.key}</text></g>`;
  }).join("");
  svg.innerHTML=`<rect width="${width}" height="${height}" fill="#eef3f2"/>${routes}${markers}`;
}
function renderMap(vehicles,chargers,depots) {
  byId("map-empty").hidden=vehicles.length+chargers.length>0;
  drawMap(byId("fleet-map"),vehicles,chargers,depots,false);
  drawMap(byId("depot-map"),vehicles,chargers,depots,true);
  byId("map-key").innerHTML=[...depots.map((d,i)=>`<li><b>D${i+1}</b> ${escapeHtml(d.name)}</li>`),...chargers.map((c,i)=>`<li><b>C${i+1}</b> ${escapeHtml(c.name)} · ${c.free_ports}/${c.port_count} ports free · ${escapeHtml(c.status)}</li>`)].join("");
}
function renderAlerts(alerts) {
  byId("alerts-body").innerHTML=alerts.map(a=>`<tr><td><span class="badge ${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span></td><td>${escapeHtml(a.vin)}</td><td>${escapeHtml(a.status)}</td><td>${escapeHtml(a.message)}</td><td>${formatTime(a.updated_at)}</td></tr>`).join("")||'<tr><td colspan="5">No alerts.</td></tr>';
}
function renderPlans(plans) {
  byId("plans-body").innerHTML=plans.map(p=>`<tr data-vin="${escapeHtml(p.vin)}"><td>${escapeHtml(p.vin)}</td><td>${escapeHtml(p.charger_id)} · port ${p.port_number}</td>
    <td>${number(p.travel_distance_km," km to charger")}<br><small>Arrival ${formatTime(p.estimated_arrival_time)}<br>${number(p.remaining_delivery_km," km to customer")}</small></td>
    <td>${formatTime(p.start_time)}<br><small>Ready ${formatTime(p.predicted_ready_time)}<br>Delivery deadline ${formatTime(p.delivery_deadline)}</small></td>
    <td>${number(p.target_soc_pct,"%")}</td><td>₹${Number(p.estimated_cost).toFixed(2)}<br><small>${number(p.grid_energy_kwh," grid kWh")} × ₹${number(p.average_price_per_kwh)}<br>92% charging efficiency</small></td>
    <td><span class="badge ${escapeHtml(p.status)}">${escapeHtml(p.status)}</span><details><summary>Why this plan?</summary>${escapeHtml(p.reason)}</details></td>
    <td><div class="actions">${p.status==="PROPOSED"?actionButton("approve",p.plan_id,"Approve",true)+actionButton("reject",p.plan_id,"Reject"):""}${["PROPOSED","APPROVED","CHARGING"].includes(p.status)?actionButton("cancel",p.plan_id,"Cancel"):"—"}</div></td></tr>`).join("")||'<tr><td colspan="8">No charging plans.</td></tr>';
}
async function perform(operation,message) {
  if(busy) return;
  busy=true; epoch++; updateControls(); setMessage("Working…");
  try { await operation(); setMessage(message); }
  catch(error) { setMessage(error.message,true); }
  finally { busy=false; epoch++; await refresh(true); updateControls(); }
}
function viewPlan(vin) {
  byId("plans-section").scrollIntoView({behavior:"smooth",block:"start"});
  const row=[...byId("plans-body").querySelectorAll("tr")].find(r=>r.dataset.vin===vin);
  if(row) { row.classList.add("attention"); setTimeout(()=>row.classList.remove("attention"),2000); }
}
byId("seed-button").addEventListener("click",()=>{
  const count=Number(byId("vehicle-count").value);
  if(!Number.isInteger(count)||count<1||count>1000) {setMessage("Choose between 1 and 1000 vehicles.",true);return;}
  perform(()=>request("/simulator/scenarios",{method:"POST",body:JSON.stringify({scenario:byId("scenario").value,vehicle_count:count,seed:42})}),"Scenario reset. Simulator stopped; fleet is ready.");
});
byId("start-button").addEventListener("click",()=>perform(()=>request("/simulator/start",{method:"POST",body:JSON.stringify({tick_seconds:1,time_scale:60})}),"Simulation started at 60×."));
byId("stop-button").addEventListener("click",()=>perform(()=>request("/simulator/stop",{method:"POST"}),"Simulation stopped. Telemetry is frozen; Start resumes."));
byId("refresh-button").addEventListener("click",()=>refresh(true));
document.addEventListener("click",async event=>{
  const marker=event.target.closest("[data-map-name]");
  if(marker) setMessage(marker.dataset.mapName);
  const button=event.target.closest("button[data-action]");
  if(!button) return;
  const {action,id}=button.dataset;
  if(action==="view") {viewPlan(id);return;}
  const path=action==="generate"?`/charging/plans/${encodeURIComponent(id)}`:`/charging/plans/${encodeURIComponent(id)}/${action}`;
  await perform(()=>request(path,{method:"POST"}),action==="generate"?"Plan ready for review.":`Plan ${action} completed.`);
  if(action==="generate") viewPlan(id);
});
document.addEventListener("keydown",event=>{if(event.key==="Enter"&&event.target.dataset.mapName)setMessage(event.target.dataset.mapName);});
document.defaultView?.addEventListener("resize",()=>{if(dashboard)renderMap(dashboard.fleet.vehicles,dashboard.chargers,dashboard.depots);});
updateControls();
refresh();
setInterval(()=>refresh(),2000);
