const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

export function makeCab(trip, network) {
  const legs = trip.legs || [{stops:trip.stops, path:trip.path}];
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
  const node = cab.stops[cab.index], ahead = cab.stops[cab.index+1];
  if (distance(cab.point, cab.network.nodes[node]) > 0) {
    cab.from = ahead;
    cab.to = node;
  } else {
    const exits = neighbours(cab.network, node);
    const sides = exits.filter(n => n !== ahead);
    cab.from = node;
    cab.to = pick(sides.length ? sides : exits);
  }
  cab.mode = 'detour';
}

// Start Dijkstra at the cab's position between two intersections. The cost
// of reaching either endpoint includes only the remaining part of that road.
function recoveryPath(cab) {
  const {nodes} = cab.network;
  const eligible = new Set(cab.stops);
  const costs = new Map();
  const parent = new Map();
  const open = new Set([cab.from, cab.to]);
  for (const end of open) costs.set(end, distance(cab.point, nodes[end]));
  while (open.size) {
    let node;
    for (const candidate of open) {
      if (node === undefined || costs.get(candidate) < costs.get(node)) node = candidate;
    }
    if (eligible.has(node)) {
      const path = [node];
      while (parent.has(node)) {
        node = parent.get(node);
        path.unshift(node);
      }
      return path;
    }
    open.delete(node);
    for (const next of neighbours(cab.network, node)) {
      const cost = costs.get(node) + distance(nodes[node], nodes[next]);
      if (cost >= (costs.get(next) ?? Infinity)) continue;
      costs.set(next, cost);
      parent.set(next, node);
      open.add(next);
    }
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
      resumeAt(cab, next);
      if (queued && cab.mode !== 'arrived') divert(cab);
    } else if (cab.mode === 'returning' && cab.stops.includes(next)) {
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

