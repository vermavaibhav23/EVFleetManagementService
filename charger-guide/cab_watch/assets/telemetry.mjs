// Keep unacknowledged readings across refreshes in this browser tab.
export class TelemetryOutbox {
  constructor(storage, owner) {
    this.storage = storage;
    this.key = 'cabwatch.telemetry.' + owner;
    this.batches = JSON.parse(storage.getItem(this.key) || '[]');
  }

  get length() { return this.batches.length; }
  first() { return this.batches[0]?.slice(0, 25); }

  hasLegacyReadings() {
    return this.batches.some(batch => batch.some(point => 'leg' in point));
  }

  // One-time upgrade of readings persisted by the previous dashboard.
  upgrade(trips) {
    const saved = new Map(trips.map(trip => [trip.id, trip]));
    const batches = this.batches.map(batch => batch.flatMap(point => {
      if (!('leg' in point)) return [point];
      const {leg, ...reading} = point;
      const trip = saved.get(point.trip_id);
      if (!trip || trip.completed || point.sequence <= trip.last_sequence || leg === 2) return [];
      // A migrated pre-pickup checkpoint now starts at pickup.
      if (trip.lat === null && trip.motion?.mode === 'route') return [];
      return [reading];
    }));
    this.replace(batches.filter(batch => batch.length));
  }

  append(batch) {
    if (this.length >= 2) throw Error('Telemetry buffer is full.');
    this.replace([...this.batches, batch]);
  }

  acknowledge() {
    const remaining = this.batches[0].slice(25);
    this.replace(remaining.length ? [remaining, ...this.batches.slice(1)] : this.batches.slice(1));
  }

  replace(batches) {
    this.storage.setItem(this.key, JSON.stringify(batches));
    this.batches = batches;
  }
}

export function retryDelay(error, attempts) {
  if (error.status && ![408, 429, 500, 502, 503, 504].includes(error.status)) return null;
  return Math.max(error.retryAfter || 0, Math.min(30000, 1000 * 2 ** Math.min(attempts, 5)));
}
