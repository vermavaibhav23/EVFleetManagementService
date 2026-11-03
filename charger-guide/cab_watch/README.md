# CabWatch

CabWatch is a simulated fleet dispatch and route-monitoring dashboard built with FastAPI, Apache Kafka, and PostgreSQL. The browser acts as the GPS device on a fictional A–Z road network; no real vehicles or locations are tracked.

[Open the Render dashboard](https://ev-charger-guide.onrender.com/cab-watch/). This copy is hosted alongside EV Charger Guide and uses its Aiven PostgreSQL and Kafka services.

## Dispatch a fleet

Choose **1–20 cars** and select **New fleet**. Four depots sit at intersections A, E, V, and Z. Cars are distributed as evenly as possible in that order: for example, ten cars become 3, 3, 2, and 2. The map shows the number of available cars at each depot.

Choose **Create trip**, then click a pickup and a different drop-off. FastAPI selects the **available car with the shortest road distance to the pickup**, breaking ties by car number. Its journey has three planned legs:

1. Home depot → pickup.
2. Pickup → drop-off.
3. Drop-off → home depot.

The car becomes available for another trip only after its return GPS reading is saved. The same vehicle ID can therefore appear on several trips in one simulation. If every car is busy, trip creation waits until one returns. A new fleet gets a private session ID; its vehicles and trips are independent of other simulations.

Intersections and road lengths form a weighted adjacency list. FastAPI runs Dijkstra for the three legs and saves their ordered node IDs in PostgreSQL. The browser moves each cab along these roads. **Take wrong turn** sends the selected car down a side road; it keeps choosing connected roads until it meets the active leg again. **Return to route** finds the shortest road path from its current position, via either end of the current road, to an eligible remaining junction.

## One GPS reading

1. The browser submits a trip ID, sequence, leg number, latitude, and longitude to FastAPI. Every tick advances five simulated seconds, with six metres per map unit.
2. The producer publishes one JSON message per moving car to Kafka topic `cab.location`, keyed by trip ID. The topic has two partitions.
3. The consumer measures each reading against **the planned route for its current leg**, not the whole round trip. Three consecutive readings more than 200 m away open one alert. A reading within 200 m resolves it. Duplicate or older sequences do not advance the detector.
4. The consumer saves the GPS reading and detector state in one PostgreSQL transaction, then commits the Kafka offset. A replay after a crash is skipped using the saved sequence number.
5. The dashboard polls for the confirmed position, moves the marker, and shows the alert. **Explain alert** asks Gemini to explain stored evidence on demand. If the model or quota is unavailable, the UI labels its rule-based explanation.

PostgreSQL has five tables under `cab_watch`: `depots` stores fixed depot nodes; `vehicles` stores the cars in a fleet and their availability; `trips` stores vehicle assignment, the three planned legs, and detector state; `gps_readings` stores coordinates and offsets; and `alerts` stores opening and closing readings. Each alert also stores its three opening measurements so it can still be explained after older GPS rows expire. A unique index permits one open alert per trip.

## Data retention

A background cleanup starts with the consumer and runs on startup and every six hours while the service is running. By default it removes raw GPS readings for completed trips after **7 days**, while keeping each trip's latest reading for the status view. It keeps trip and alert history for **90 days** and simulation-specific vehicle records for **365 days**; depots are not deleted. A vehicle is only removed when no retained trip references it. Active-trip readings are not pruned by the short GPS window. These are demo defaults, not a substitute for a real fleet's archival or legal retention policy.

Set `GPS_RETENTION_DAYS`, `TRIP_RETENTION_DAYS`, `FLEET_RETENTION_DAYS`, and `RETENTION_INTERVAL_HOURS` to positive integers to change the policy. Deleting a trip cascades to its GPS readings and alerts. Back up or export any history that must outlive the configured period.

## Run locally

Install Docker with Docker Compose. From the parent `charger-guide` directory:

```sh
docker compose up --build
```

Open [the dashboard](http://localhost:18080/cab-watch/). Compose starts FastAPI, PostgreSQL, and Kafka. Optionally set `GEMINI_API_KEY` before starting Compose to enable model-generated explanations.

## Tests

```sh
python -m unittest -v test_cab_watch test_retention
node --test test_motion.mjs
```
