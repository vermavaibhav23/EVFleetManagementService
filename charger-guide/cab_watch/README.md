# CabWatch

CabWatch helps a dispatcher spot when a simulated cab leaves its planned route. [Try the live demo](https://ev-charger-guide.onrender.com/cab-watch/).

Start with an empty city map. Choose **Create trip**, click a pickup and a different drop-off (A–Z), and the cab starts automatically. The dropdowns also support keyboard selection. Add up to 20 trips per fleet, including multiple cabs on the same route. Select a cab from the roster to bring its route and marker to the front. Thin grey lines are roads, thick coloured lines are planned routes, and dashed trails show reported positions.

Intersections and road lengths form a weighted adjacency list. FastAPI runs Dijkstra when the trip is created and saves the ordered node IDs in PostgreSQL. The consumer measures each GPS reading against that saved route. **Take wrong turn** diverts the selected cab onto a connected side road. **Return to route** sends it back to the junction where it left.

The browser simulates the GPS device on a fictional road network. Every tick advances five simulation seconds. GPS uses latitude and longitude projected from a local map with six metres per map unit. Trips stop at their destination. The simulator pauses when you pause it or close the page. Each new simulation has a private random ID and independent trips. Old runs are eligible for cleanup after a day.

## What happens to one reading

1. The device submits trip ID, sequence, latitude and longitude to FastAPI.
2. The producer publishes JSON to the Kafka topic `cab.location`, keyed by trip ID.
3. A consumer measures the shortest distance to any segment of the planned route. Three consecutive readings over 200 m open one incident. A reading within 200 m resolves it. Duplicate or older sequence numbers do not advance the detector. The consumer commits its offset after the database transaction.
4. The map displays the consumed position. The rule uses coordinates alone, independently of the simulation controls. GPS shows route deviation; the dispatcher checks the circumstances.
5. **Explain alert** lets Gemini request the stored evidence. If the model or its quota is unavailable, the UI labels the rule-based explanation.

PostgreSQL has three tables under `cab_watch`: `trips` holds the run ID, planned route and detector state, `gps_readings` stores ordered coordinates and offsets, and `alerts` records opening and closing readings. A unique index permits one open alert per trip.

## Run locally

Install Docker with Docker Compose. From the parent charger-guide directory, run:

```sh
docker compose up --build
```

Open [the dashboard](http://localhost:18080/cab-watch/). Compose starts the API, PostgreSQL and Kafka. Optionally set `GEMINI_API_KEY` before starting Compose to enable model-generated explanations.

## Test the detector

```sh
python -m unittest -v test_cab_watch
```
