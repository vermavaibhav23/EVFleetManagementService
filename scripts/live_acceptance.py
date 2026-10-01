"""Exercise ONLY the disposable SIM fleet. Writes a JSON evidence artifact.

python scripts/live_acceptance.py --base-url https://... --output evidence.json
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from time import perf_counter

import httpx

VIN = "SIM00000000000001"


async def main(args):
    evidence = {
        "started_at": datetime.now().astimezone().isoformat(),
        "base_url": args.base_url,
        "requests": [],
        "samples": [],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/") + "/api/v1", timeout=40
    ) as client:

        async def request(method, path, **kwargs):
            started = perf_counter()
            response = await client.request(method, path, **kwargs)
            evidence["requests"].append(
                {
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                }
            )
            save()
            response.raise_for_status()
            return response.json()

        try:
            evidence["deployment"] = await request("GET", "/health/live")
            evidence["health_before"] = await request("GET", "/health/ready")
            await request("POST", "/simulator/stop")
            evidence["seed"] = await request(
                "POST",
                "/simulator/scenarios",
                json={
                    "scenario": "LOW_BATTERY_BEFORE_TRIP",
                    "vehicle_count": 10,
                    "seed": 42,
                },
            )
            initial = (await request("GET", f"/vehicles/{VIN}/latest"))["vehicle"]
            await request(
                "POST", "/simulator/start", json={"tick_seconds": 1, "time_scale": 60}
            )
            await request(
                "POST", "/simulator/start", json={"tick_seconds": 1, "time_scale": 60}
            )
            await asyncio.sleep(3)
            moving = (await request("GET", f"/vehicles/{VIN}/latest"))["vehicle"]
            assert moving["soc_pct"] < initial["soc_pct"]
            assert (moving["lat"], moving["lon"]) != (initial["lat"], initial["lon"])
            assert moving["route_remaining_km"] < initial["route_remaining_km"]
            evidence["movement"] = {"initial": initial, "moving": moving}
            plan = await request("POST", f"/charging/plans/{VIN}")
            repeated = await request("POST", f"/charging/plans/{VIN}")
            assert plan["plan_id"] == repeated["plan_id"]
            approved = await request(
                "POST", f"/charging/plans/{plan['plan_id']}/approve"
            )
            await request("POST", f"/charging/plans/{plan['plan_id']}/approve")
            evidence["approved_plan"] = approved
            print(
                "Approved",
                approved["charger_id"],
                approved["target_soc_pct"],
                flush=True,
            )
            start = perf_counter()
            seen = set()
            while perf_counter() - start < 900:
                latest, status = await asyncio.gather(
                    request("GET", f"/vehicles/{VIN}/latest"),
                    request("GET", "/simulator/status"),
                )
                event = latest["vehicle"]
                assert status["running"], status
                evidence["samples"].append(
                    {
                        "elapsed": round(perf_counter() - start, 1),
                        "event": event,
                        "simulator": status,
                    }
                )
                state = event["operating_state"]
                if state not in seen:
                    print(
                        state,
                        "soc",
                        event["soc_pct"],
                        "distance",
                        event.get("distance_to_destination_km"),
                        "events",
                        status["emitted_events"],
                        flush=True,
                    )
                    seen.add(state)
                save()
                if state == "AT_CUSTOMER":
                    break
                await asyncio.sleep(0.5)
            history = await request("GET", f"/telemetry?vin={VIN}&limit=1000")
            evidence["history"] = history
            states = {e["operating_state"] for e in history}
            required = {
                "PARKED",
                "DRIVING",
                "EN_ROUTE_TO_CHARGER",
                "WAITING_FOR_CHARGER",
                "CHARGING",
                "READY",
                "RESUMING_TRIP",
                "AT_CUSTOMER",
            }
            assert required <= states, required - states
            evidence["states_verified"] = sorted(states)
            evidence["reservations"] = await request("GET", f"/reservations?vin={VIN}")
            evidence["plans"] = await request("GET", f"/charging/plans?vin={VIN}")
            assert evidence["plans"][0]["status"] == "COMPLETED"
            assert evidence["reservations"][0]["status"] == "COMPLETED"
            # Physical invariants over the complete recorded sequence.
            events = sorted(history, key=lambda e: (e["ts"], e["seq"]))
            for before, after in zip(events, events[1:]):
                if after["soc_pct"] > before["soc_pct"]:
                    assert after["operating_state"] in {"CHARGING", "READY"}
                if after["operating_state"] == "CHARGING":
                    assert (
                        after["is_plugged_in"] and after["plan_id"] == plan["plan_id"]
                    )
                    assert after["port_number"] == approved["port_number"]
                    assert after["distance_to_destination_km"] == 0
                if (
                    before.get("navigation_target")
                    and before.get("navigation_target")
                    == after.get("navigation_target")
                    and after["operating_state"]
                    in {"DRIVING", "EN_ROUTE_TO_CHARGER", "RESUMING_TRIP"}
                ):
                    assert (
                        after["distance_to_destination_km"]
                        <= before["distance_to_destination_km"] + 0.01
                    )
            stopped = await request("POST", "/simulator/stop")
            frozen = (await request("GET", f"/vehicles/{VIN}/latest"))["vehicle"]
            await asyncio.sleep(3)
            later = await request("GET", "/simulator/status")
            assert (
                not later["running"]
                and later["emitted_events"] == stopped["emitted_events"]
            )
            assert (
                frozen["event_id"]
                == (await request("GET", f"/vehicles/{VIN}/latest"))["vehicle"][
                    "event_id"
                ]
            )
            evidence["stop_verified"] = later
            evidence["health_after"] = await request("GET", "/health/ready")
            evidence["passed"] = True
            print("Full live journey PASS", flush=True)
        finally:
            await request("POST", "/simulator/stop")
            save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
