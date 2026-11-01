# EV Fleet Charging Scheduler

A final-year interview demo: optimize charging cost while meeting fixed delivery deadlines, battery reserve and shared charger availability. Vehicles are simulated; customer order is fixed.

**Live demo:** [EV Fleet Charging Scheduler](https://ev-fleet-interview.onrender.com/portal). The free instance can take 50 seconds or longer to wake after inactivity; open it before your interview.

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
- One saved run per scenario, linked to vehicles, plans and bookings through `run_id`. Open saved scenario preserves progress; Load / reset replaces only that scenario. One selected scenario runs at a time across this shared demo.
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

## Generative AI explanations

In **Plans & Decisions**, choose **Explain this plan with AI**. FastAPI selects the validated plan's charging stops, costs, energy and deadline summary and sends that compact JSON to Gemini. A completed unsuccessful search has an **Explain this result with AI** button; timeouts are distinguished from infeasibility within the bounded model.

The MILP solver calculates the plan; Gemini explains it. The model receives no tools and cannot approve, book, or change the simulation. No coordinates, account credentials or free-form user prompts are sent in the facts. Explanations are advisory, may contain mistakes, and appear alongside a deterministic summary for comparison. The backend rejects results if the selected plan becomes stale while inference is running. Text is escaped before display.

Set `GEMINI_API_KEY` in Render's secret environment, with `GEMINI_MODEL=gemini-3.1-flash-lite`. Never put the key in JavaScript or Git. Google's [pricing](https://ai.google.dev/gemini-api/docs/pricing) lists a free tier for this model; account quotas apply, and free-tier prompts can be used to improve Google's products. Use synthetic demo data and a project without paid billing for the free deployment.

The API uses a 12-second timeout, no automatic retries, one inference at a time, a bounded 10-minute cache, and default limits of 3 uncached calls per minute / 20 per rolling day. Limits and cache are per API process and reset on restart; they are demo quota controls, not a billing cap. On missing configuration, quota exhaustion, blocked/malformed output or provider errors, the UI clearly labels the deterministic fallback **Plan facts · AI unavailable**. Ordinary planning works without an AI key.

API: `POST /api/v1/vehicles/{vin}/explanation` with `{ "run_id": "...", "plan_id": "..." }` or `{ "run_id": "...", "job_id": "..." }`. The server reads the selected record from PostgreSQL; the client cannot provide model instructions or plan facts. No new database table or background worker is needed.

## Free deployment
Use one Free PostgreSQL service and one Free Kafka service on Aiven, with topic `vehicle.telemetry.v4`, two partitions and at most three days retention. Select ongoing free plans, not paid plans funded by trial credits. Aiven can power off inactive free services.

Deploy branch `codex/interview-render-aiven` to a Free Render web service using the Dockerfile and `/api/v1/health/ready`. `render.yaml` records the deployment. Supply `.env.example` values in Render. Use the Aiven SASL endpoint, `SASL_SSL`, `SCRAM-SHA-256`, and the project CA in `KAFKA_SSL_CA_PEM` and `DATABASE_SSL_CA_PEM`. Never commit secrets.

The live service was connected as a public Git repository. For later code changes, push this branch and choose **Manual Deploy → Deploy latest commit** in Render; this connection does not automatically deploy Git pushes.

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
Shared dashboard selection (not separate user accounts); no physical devices; straight-line map and deterministic road fixtures; bounded search; fixed delivery order; conservative site-power reservations. Reset replaces only the selected scenario's data. The advanced v3 implementation remains in Git history.

## Saved scenario schema (v5)
`fleet_run` has `run_id` as its primary key, a unique `scenario`, `active`, `revision`, and `data`. There is no `singleton` column. The partial unique index on `active` permits many saved rows and at most one selected row. Runs are created on first load; three loaded scenarios produce three rows.

Vehicles, stations, depots, planning jobs, journey plans and reservations use `(run_id, id)` as their primary key. This allows each scenario to contain its own `SIM-001` and `SIM-C1`. Switching pauses the previous run and cancels its unfinished searches; committed plans and bookings remain. Pending telemetry still finishes against its own saved run. Reset assigns a fresh run ID, so delayed messages from the replaced run are rejected.

Schema v5 migrates existing data at startup. Deploy the API and its workers together; older v4 workers are incompatible with this schema. `GET /api/v1/simulator/runs` lists saved scenarios; `POST /api/v1/simulator/scenarios/{scenario}/select` opens one without resetting it.
