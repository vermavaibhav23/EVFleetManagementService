# EV Charger Guide

A fleet operator gets one dashboard alert when a simulated van reaches 25% battery. The alert recommends the closest working charger with a matching plug and a free port that the van can reach with 2 kWh left. Distances are measured on one simulated road. At a common driving speed, the closest eligible charger has the shortest travel time. Availability is observed at the time of the reading.

## Run locally

From this directory with Docker Desktop running:

```sh
docker compose up --build -d
```

Open http://localhost:18080. To enable the Gemini agent, set GEMINI_API_KEY in the shell before starting Compose. Without a key, the dashboard shows the rule-based recommendation and candidate list. Use `docker compose down` to stop this demo. Its Kafka and PostgreSQL volumes belong to the ev-charger-guide Compose project.

## Try the interview demo

1. Under Van-A choose **Low battery**. The simulator publishes 8 kWh for a 40 kWh battery. After the 2 kWh reserve, 6 kWh is available for travel. At 0.25 kWh/km that gives 24 km of range.
2. West is closer but occupied. North has the wrong plug. Central is 10 km away and qualifies. East is 30 km away. The dashboard raises one alert for Central.
3. Click **Investigate alert**. Gemini can request battery facts and charger comparisons, inspect their results, then explain the choice. The dashboard shows the tools called and the calculated evidence. Each investigation allows up to three model turns. Tool errors, missing credentials and incomplete investigations return a labeled rule-based summary.
4. Choose **Critical battery**. The same alert now requests assistance. Choose **Recovered** to close it.

## What happens to one reading

The FastAPI simulator publishes to Kafka topic `charger-guide.readings.v1`, keyed by van ID. The consumer locks that van's PostgreSQL row. A sequence number at or below the last accepted number is a replay or a stale reading and is ignored. For a newer reading, the consumer writes the battery and alert in one transaction, then commits its Kafka offset. If it restarts between those commits, the sequence check makes replay harmless. A partial unique index allows only one open alert per van.

| Table | Fields |
|---|---|
| vehicles | id, connector, capacity_kwh, kwh_per_km, battery_kwh, road_km, last_sequence |
| chargers | id, connector, road_km, online, free_ports |
| alerts | id, vehicle_id, charger_id (empty if assistance needed), reason, opened_at, resolved_at |

The Python eligibility rule determines the recommendation saved in PostgreSQL. The agent has read-only tools scoped to that alert. Open the separate charger-guide directory as the project when presenting this edition.

Run `python -m unittest -v` here with requirements installed. The recommendation algorithm is in decision.py, the Gemini loop in agent.py, and the Kafka-to-PostgreSQL flow in main.py.


## Hosted demo

[Open the Render dashboard](https://ev-charger-guide.onrender.com/). Render runs the web process and consumer. Aiven hosts PostgreSQL and Kafka. The first visit can take longer while the web service wakes up. Both vans, their battery readings and station availability are simulated.

To repeat the end-to-end check on your own running demo, run `python smoke_demo.py http://localhost:18080` from this directory. It sends readings for Van-A, checks that one alert changes from a charger recommendation to assistance, requests an agent review, closes the alert and restores the initial battery level. Each run creates alert history. The response identifies whether Gemini or the rule-based fallback answered.
