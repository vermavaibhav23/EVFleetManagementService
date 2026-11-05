const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

export function makeCab(trip, network) {
  const legs = trip.planned_legs ? ["to_pickup","customer_ride","to_depot"].map(name => {
    const stops = trip.planned_legs[name];
    return {stops, path:stops.map(node => network.nodes[node])};
  }) : trip.legs || [{stops:trip.stops, path:trip.path}];
  const point = [...legs[0].path[0]];
  const cab = {...trip, legs, fullStops:trip.stops, stops:legs[0].stops,
    nodes:legs[0].path, leg:0, index:0, point, mode:'route', sequence:0,
    streak:0, offset:0, confirmed:0, trail:[point], network};
  while (cab.stops.length === 1 && cab.leg < legs.length - 1) nextLeg(cab);
  return cab;
}

function neighbours(network, node) {
  return network.roads.filter(road => road.includes(node)).map(road => road.replace(node, ''));
}

function pick(choices) {
  return choices[Math.floor(Math.random() * choices.length)];
}

function nextLeg(cab) {
  cab.leg++;
  cab.stops = cab.legs[cab.leg].stops;
  cab.nodes = cab.legs[cab.leg].path;
  cab.index = 0;
  cab.mode = 'route';
}

function resumeAt(cab, node) {
  cab.index = cab.stops.indexOf(node);
  cab.mode = 'route';
  while (cab.index === cab.stops.length - 1 && cab.leg < cab.legs.length - 1) {
    nextLeg(cab);
  }
  if (cab.index === cab.stops.length - 1) cab.mode = 'arrived';
}

export function divert(cab) {
  if (cab.mode !== 'route' || (cab.legs.length > 1 && cab.leg !== 1)) return;
  if (cab.index + 1 < cab.stops.length - 1) cab.mode = 'turn requested';
}

function takeQueuedTurn(cab) {
  const node = cab.stops[cab.index];
  const exits = neighbours(cab.network,node).filter(n =>
    n !== cab.stops[cab.index-1] && n !== cab.stops[cab.index+1]);
  if (!exits.length) { divert(cab); return; }
  cab.from = node;
  cab.to = pick(exits);
  cab.mode = 'detour';
}

// Start Dijkstra at the current point on the road. Score each possible rejoin
// by connector distance PLUS the remaining planned leg, not proximity alone.
function recoveryPath(cab) {
  const {nodes} = cab.network;
  const remaining = new Map();
  let suffix = 0;
  for (let i=cab.stops.length-1;i>=0;i--) {
    if (i<cab.stops.length-1) suffix += distance(nodes[cab.stops[i]],nodes[cab.stops[i+1]]);
    remaining.set(cab.stops[i],suffix);
  }
  const costs = new Map();
  const parent = new Map();
  const open = new Set([cab.from, cab.to]);
  const settled = new Set();
  let best, bestTotal = Infinity, bestConnector = Infinity;
  const epsilon = 1e-8;
  for (const end of open) costs.set(end, distance(cab.point, nodes[end]));
  while (open.size) {
    let node;
    for (const candidate of open) {
      if (node === undefined || costs.get(candidate) < costs.get(node)) node = candidate;
    }
    const connector = costs.get(node);
    if (connector>bestTotal+epsilon) break;
    if (remaining.has(node)) {
      const total = connector+remaining.get(node);
      // For equal full journeys, rejoin sooner rather than staying off-route.
      if (total<bestTotal-epsilon || (Math.abs(total-bestTotal)<=epsilon && connector<bestConnector)) {
        best=node;bestTotal=total;bestConnector=connector;
      }
    }
    open.delete(node);
    settled.add(node);
    for (const next of neighbours(cab.network, node)) {
      if (settled.has(next)) continue;
      const cost = costs.get(node) + distance(nodes[node], nodes[next]);
      if (cost >= (costs.get(next) ?? Infinity)) continue;
      costs.set(next, cost);
      parent.set(next, node);
      open.add(next);
    }
  }
  if (best!==undefined) {
    const path = [best];
    while (parent.has(best)) {
      best=parent.get(best);
      path.unshift(best);
    }
    return path;
  }
  throw new Error('No road leads back to the planned route.');
}

export function rejoin(cab) {
  if (cab.mode === 'turn requested') {
    cab.mode = 'route';
  } else if (cab.mode === 'detour') {
    cab.recovery = recoveryPath(cab);
    cab.mode = 'returning';
  }
}

export function advance(cab) {
  if (cab.mode === 'arrived') return false;
  const onRoute = cab.mode === 'route' || cab.mode === 'turn requested';
  const next = onRoute ? cab.stops[cab.index + 1] :
    cab.mode === 'returning' ? cab.recovery[0] : cab.to;
  const target = cab.network.nodes[next];
  const remaining = distance(cab.point, target);
  if (remaining > 14) {
    cab.point = cab.point.map((v, i) => v + (target[i] - v) * 14 / remaining);
  } else {
    cab.point = [...target];
    if (onRoute) {
      const queued = cab.mode === 'turn requested';
      const leg = cab.leg;
      resumeAt(cab, next);
      if (queued && cab.leg === leg && cab.mode === 'route') takeQueuedTurn(cab);
    } else if (cab.mode === 'returning' && cab.recovery.length === 1) {
      resumeAt(cab, next);
    } else if (cab.mode === 'returning') {
      cab.recovery.shift();
    } else {
      const exits = neighbours(cab.network, next).filter(n => n !== cab.from);
      const sideRoads = exits.filter(n => !cab.stops.some((v,i) => i>0 &&
        ((v===n && cab.stops[i-1]===next) || (v===next && cab.stops[i-1]===n))));
      cab.to = sideRoads.length ? pick(sideRoads) : exits.length ? pick(exits) : cab.from;
      cab.from = next;
    }
  }
  cab.sequence++;
  return true;
}

