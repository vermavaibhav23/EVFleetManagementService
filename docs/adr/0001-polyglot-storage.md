# ADR 0001: Polyglot Storage For EV Fleet Telemetry

## Status

Accepted

## Context

Fleet telemetry is high-volume, append-heavy, and latency-sensitive. The hackathon problem statement expects real-time stream processing and historical analytics rather than a single database doing every job.

## Decision

- Kafka carries telemetry and alert events between services.
- MongoDB stores telemetry, chargers, and alerts for the MVP because document writes fit fast-changing vehicle event shapes.
- Redis stores latest vehicle state for low-latency dashboard reads.

## Consequences

The API can answer latest-state reads without scanning telemetry history, while Kafka leaves room for additional consumers such as analytics, billing, and route optimization services.

