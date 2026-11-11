const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

export function makeCab(trip, network) {
  const nodes = trip.route.map(node => network.nodes[node]);
  const point = [...nodes[0]];
  const motion = trip.motion;
  return {...trip, nodes, point, mode:motion?.mode ?? 'route', index:motion?.index ?? 0,
    trail:motion?.mode === 'detour' ? restoreTrail(motion, trip.route, network) : [],
    to:motion?.target, recovery:motion?.recovery?.slice(), sequence:0,
    offset:0, confirmed:0, network};
}

export function motionSnapshot(cab) {
  switch (cab.mode) {
    case 'route':
    case 'turn requested':
      return {mode:cab.mode, index:cab.index};
    case 'detour':
      return {mode:cab.mode, trail:[...cab.trail], target:cab.to};
    case 'returning':
      return {mode:cab.mode, recovery:[...cab.recovery]};
    case 'arrived':
      return {mode:cab.mode};
  }
  throw new Error('Unknown cab movement: ' + cab.mode);
}

function neighbours(network, node) {
  return network.roads.filter(road => road.includes(node)).map(road => road.replace(node, ''));
}

function pick(choices) {
  return choices[Math.floor(Math.random() * choices.length)];
}

function resumeAt(cab, node) {
  cab.index = cab.route.indexOf(node);
  cab.mode = cab.index === cab.route.length - 1 ? 'arrived' : 'route';
}

export function divert(cab) {
  if (cab.mode !== 'route') return;
  if (cab.index + 1 < cab.route.length - 1) cab.mode = 'turn requested';
}

function chooseDetourRoad(cab, node) {
  const index = cab.route.indexOf(node);
  const plannedNext = index < 0 ? undefined : cab.route[index + 1];
  const roads = neighbours(cab.network, node);
  const alternatives = roads.filter(next => next !== plannedNext);
  cab.trail = [...cab.trail, node];
  cab.to = pick(alternatives.length ? alternatives : roads);
  cab.mode = 'detour';
}

// Older checkpoints only saved the current road. Find a connected prefix
// once for those cabs; all newly recorded turns have an exact trail.
function restoreTrail(motion, route, network) {
  const trail = motion.trail ?? [motion.source];
  if (route.includes(trail[0])) return [...trail];
  const pending = [trail[0]];
  const parent = new Map([[trail[0], null]]);
  for (const node of pending) {
    if (route.includes(node)) {
      const prefix = [];
      for (let at=node; parent.get(at) !== null; at=parent.get(at)) prefix.push(at);
      return [...prefix, ...trail];
    }
    for (const next of neighbours(network, node)) {
      if (parent.has(next)) continue;
      parent.set(next, node);
      pending.push(next);
    }
  }
  throw new Error('Saved detour is disconnected from its route.');
}

export function rejoin(cab) {
  if (cab.mode === 'turn requested') {
    cab.mode = 'route';
  } else if (cab.mode === 'detour') {
    cab.recovery = [...cab.trail].reverse();
    cab.trail = [];
    cab.mode = 'returning';
  }
}

export function advance(cab) {
  if (cab.mode === 'arrived') return false;
  const onRoute = cab.mode === 'route' || cab.mode === 'turn requested';
  const next = onRoute ? cab.route[cab.index + 1] :
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
      if (queued && cab.mode === 'route') chooseDetourRoad(cab, next);
    } else if (cab.mode === 'returning' && cab.recovery.length === 1) {
      resumeAt(cab, next);
    } else if (cab.mode === 'returning') {
      cab.recovery.shift();
    } else {
      chooseDetourRoad(cab, next);
    }
  }
  cab.sequence++;
  return true;
}

