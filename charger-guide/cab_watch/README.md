# Cabwatch · Harbour City

Choose 1–6 cabs and click **New simulation**, then **Start**. Each cab has a different pickup, destination, number and color. A solid line shows its planned route. A dashed trail shows where the cab has actually reported. Select a cab from the map or roster. **Take wrong turn** diverts it at an intersection. **Return to route** brings it back via the same street.

The browser simulates the GPS device using a fictional city grid. Every tick advances five simulation seconds. GPS uses latitude and longitude projected from a local map with six metres per map unit. Trips stop at their destination. The simulator pauses when you pause it or close the page. Each new simulation has a private random ID and independent trips. Old runs are eligible for cleanup after a day.

## What happens to one reading

1. The device submits trip ID, sequence, latitude and longitude to FastAPI.
2. The producer publishes JSON to the Kafka topic `cab.location`, keyed by trip ID.
3. A consumer measures the shortest distance to any segment of the planned route. Three consecutive readings over 200 m open one incident. A reading within 200 m resolves it. Duplicate or older sequence numbers do not advance the detector. The consumer commits its offset after the database transaction.
4. The map displays the consumed position. The rule uses coordinates alone, independently of the simulation controls. GPS shows route deviation; the dispatcher checks the circumstances.
5. **Explain alert** lets Gemini request the stored evidence. If the model or its quota is unavailable, the UI labels the rule-based explanation.

PostgreSQL has three tables under `cab_watch`: `trips` holds the run ID, route reference and detector state, `gps_readings` stores ordered coordinates and offsets, and `alerts` records opening and closing readings. A unique index permits one open alert per trip.

## Run

From the parent `charger-guide` directory run `docker compose up --build`. Open http://localhost:18080/cab-watch/. Cabwatch shares the existing FastAPI host, Kafka connection, PostgreSQL pool and optional Gemini configuration. It uses its own URL, topic, consumer group and schema. To enable Gemini locally, export `GEMINI_API_KEY` before starting Compose.

[Hosted demo](https://ev-charger-guide.onrender.com/cab-watch/)

