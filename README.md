# EV Fleet Charging Management

Small FastAPI starter for a connected EV fleet platform. It is designed to connect one main API service to independently deployed Kafka, Redis, and MongoDB services on Railway.

## What is included

- FastAPI app with startup/shutdown lifecycle for MongoDB, Redis, and Kafka.
- `/health/live` and `/health/ready` endpoints to verify dependency connectivity.
- Basic EV telemetry ingestion endpoint that stores telemetry in MongoDB, caches latest vehicle state in Redis, and publishes the event to Kafka.
- Basic charger listing and recommendation endpoint.
- Kafka consumer loop that can react to telemetry events and create low-battery alerts.
- Dockerfile and Railway-friendly environment variables.

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Update `.env` with your Railway service URLs before running against deployed services.

For local containers:

```bash
copy .env.example .env
docker compose up --build
```

## Key endpoints

- `GET /health/live`
- `GET /health/ready`
- `POST /api/v1/telemetry`
- `GET /api/v1/vehicles/{vin}/latest`
- `GET /api/v1/vehicles/{vin}/alerts`
- `POST /api/v1/chargers`
- `GET /api/v1/chargers`
- `GET /api/v1/charging/recommendations/{vin}`

## Railway variables

Set these on the FastAPI Railway service:

```env
MONGODB_URI=mongodb://...
REDIS_URL=redis://...
KAFKA_BOOTSTRAP_SERVERS=...
KAFKA_SECURITY_PROTOCOL=PLAINTEXT
KAFKA_SASL_MECHANISM=
KAFKA_USERNAME=
KAFKA_PASSWORD=
```

If your Railway Kafka service exposes SASL credentials, set `KAFKA_SECURITY_PROTOCOL=SASL_SSL` or `SASL_PLAINTEXT` and fill the username/password/mechanism values from Railway.
