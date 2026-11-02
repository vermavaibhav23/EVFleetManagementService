# EV Fleet Charger Guide

An interview demo for fleet operators. When one of the vans reports a low battery, the system checks which charging stations it can safely reach and opens an alert. A Gemini agent can inspect the battery and candidate stations to explain the recommendation.

[Open the project and demo walkthrough](charger-guide/README.md) · [Architecture and database diagram](charger-guide/fleet-guide.excalidraw)

Run the demo with `docker compose up --build` from the `charger-guide` directory. The dashboard listens on port 18080.
