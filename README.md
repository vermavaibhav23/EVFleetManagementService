# EV Fleet Charging Scheduler

A final-year interview demo: optimize charging cost while meeting fixed delivery deadlines, battery reserve and shared charger availability. Vehicles are simulated; customer order is fixed.

## Architecture
```
Simulator → HTTP telemetry API → Kafka (vehicle ID key)
                                  ↓ one consumer
                        PostgreSQL current state + history
                                  ↓
                 SQL planning job → isolated MILP solver
                                  ↓
                  Manager approval → atomic booking → simulator
```

FastAPI serves the HTML/CSS/JavaScript dashboard. PostgreSQL owns all data. Kafka has one telemetry topic and one consumer. SQL jobs replace Kafka planning messages; MongoDB is removed. Simulator outbox records keep retries durable. History, state and receipt IDs commit before offsets are acknowledged.

## Scope
- 4–10 vehicles, default 4; three focused scenarios.
- Fixed customer order, nearest four compatible stations, at most one charging stop between deliveries. Several charges across a whole journey remain possible.
- One hard battery reserve and hard delivery deadlines; no emergency reserve or late-delivery exceptions. A vehicle already at a charger can charge in place below reserve, but cannot drive below reserve.
- One recommended plan per search, explicit manager approval, latest-state revalidation and atomic reservations.
- Duplicate/out-of-order telemetry handling and stale proposal checks.
- Sequential fleet allocation, not a proof of globally optimal fleet cost.

## Five-minute interview demo
1. Open `/portal`, select Everyday charging choices, 4 vehicles, then Load / reset.
2. Review Van A1: small expensive top-up, larger cheap charge later. Approve and step the clock.
3. Load Shared chargers & disruptions. Inspect the occupied North port; trigger East failure and review replanning.
4. Load Delivery delays & replanning. Trigger unloading delay or increased consumption; show that original deadlines stay fixed and infeasible routes cannot be approved.

Explain Kafka buffering, independent validation of solver output, and atomic charger reservations. `python scripts/benchmark.py` produces a deterministic cost comparison; solver timings are not distributed-system throughput.

## Free deployment
Use one Free PostgreSQL service and one Free Kafka service on Aiven, with topic `vehicle.telemetry.v4`, two partitions and at most three days retention. Select ongoing free plans, not paid plans funded by trial credits. Aiven can power off inactive free services.

Deploy branch `codex/interview-render-aiven` to a Free Render web service using the Dockerfile and `/api/v1/health/ready`. `render.yaml` records the deployment. Supply `.env.example` values in Render. Use the Aiven SASL endpoint, `SASL_SSL`, `SCRAM-SHA-256`, and the project CA in `KAFKA_SSL_CA_PEM` and `DATABASE_SSL_CA_PEM`. Never commit secrets.

One consumer and one SQL dispatcher run in the API process. Solves run one at a time in a child process. The simulator pauses after five minutes without dashboard reads; no new heartbeats are generated while idle. Render can sleep and take about a minute to wake. This is an interactive demo, not continuous monitoring. Check peak memory against the free instance's 512 MB limit.

## Development and checks
Python 3.13:
```sh
pip install -r requirements.txt -r requirements-dev.txt
# Copy .env.example to .env and fill in test service connections.
uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m pytest -q
node --test tests/test_frontend.cjs
ruff check app tests scripts
```
Ordinary tests use no external services. Cloud tests require a disposable SQL database and dedicated precreated Kafka topic. See `docs/verification.md` for executed checks.

## Limits
Single shared simulation; no physical devices; straight-line map and deterministic road fixtures; bounded search; fixed delivery order; conservative site-power reservations. Reset replaces demo data. The advanced v3 implementation remains in Git history.
