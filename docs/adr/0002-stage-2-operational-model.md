# ADR 0002: Stage 2 operational model

## Status

Accepted

## Context

Stage 1 accepted telemetry and raised a low-SoC alert, but it did not know the vehicle's battery characteristics, assigned trips, charger reservations, tariff schedule, or departure deadline. A low battery percentage alone cannot determine whether a delivery is at risk.

## Decision

Stage 2 keeps energy in kWh as the calculation source of truth and derives operator-facing distance values. Readiness is based on the next committed trip plus a reserve. Battery-health flags are independent from trip readiness.

Charging plans are deadline-first and cost-optimized. The scheduler rejects incompatible, unreachable, reserved, or deadline-breaking options before comparing total operational score. Approved plans create a reservation for one explicit charger port.

The application remains a single Railway-friendly FastAPI deployment for Stage 2. Pure domain functions isolate readiness, pricing, interval overlap, scheduling, and simulation physics from MongoDB and Kafka integration.

## Consequences

- Fleet managers see understandable range and margin values without sacrificing energy correctness.
- The complete workflow can be tested manually with deterministic scenarios.
- MongoDB remains suitable for the Stage 2 operational scale.
- The in-process simulator accepts up to 1,000 vehicles; this is an input limit, not measured capacity. The historical 100,000-events/second target remains unvalidated. See `../demo-audit.md` for measured deployment results and the architecture still needed for scale.
