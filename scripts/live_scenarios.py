import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import httpx

VIN = "SIM00000000000001"


async def main(args):
    out = Path(args.output)
    BASE = args.base_url.rstrip("/") + "/api/v1"
    evidence = []
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:

        async def req(method, path, **kwargs):
            r = await c.request(method, path, **kwargs)
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path}: {r.status_code} {r.text}")
            return r.json()

        try:
            for scenario in [
                "CHARGER_CONGESTION",
                "CHARGER_FAILURE",
                "BATTERY_OVERHEATING",
                "UNEXPECTED_LONG_TRIP",
                "NORMAL_DAY",
                "UNREACHABLE_CHARGER",
            ]:
                result = {"scenario": scenario}
                result["seed"] = await req(
                    "POST",
                    "/simulator/scenarios",
                    json={"scenario": scenario, "vehicle_count": 10, "seed": 42},
                )
                await req(
                    "POST",
                    "/simulator/start",
                    json={"tick_seconds": 1, "time_scale": 60},
                )
                start = perf_counter()
                while True:
                    event = (await req("GET", f"/vehicles/{VIN}/latest"))["vehicle"]
                    if event["seq"] >= 6:
                        break
                    assert perf_counter() - start < 120
                    await asyncio.sleep(1)
                await req("POST", "/simulator/stop")
                result["event"] = event
                result["manager"] = await req("GET", "/fleet/manager")
                r = await c.post(f"/charging/plans/{VIN}")
                result["plan_status"] = r.status_code
                result["plan_response"] = r.json()
                if scenario in [
                    "NORMAL_DAY",
                    "BATTERY_OVERHEATING",
                    "UNREACHABLE_CHARGER",
                ]:
                    assert r.status_code == 422, r.text
                    if scenario == "BATTERY_OVERHEATING":
                        assert event["battery_temperature_c"] >= 45
                else:
                    assert r.status_code == 201, r.text
                    plan = r.json()
                    approved = await req(
                        "POST", f"/charging/plans/{plan['plan_id']}/approve"
                    )
                    result["approved"] = approved
                    chargers = await req("GET", "/chargers")
                    chosen = next(
                        x for x in chargers if x["charger_id"] == approved["charger_id"]
                    )
                    assert chosen["status"] == "AVAILABLE"
                    if scenario == "CHARGER_FAILURE":
                        assert (
                            next(
                                x
                                for x in chargers
                                if x["charger_id"] == "SIM-CHARGER-CHEAP"
                            )["status"]
                            == "FAULTY"
                        )
                    await req(
                        "POST",
                        "/simulator/start",
                        json={"tick_seconds": 1, "time_scale": 60},
                    )
                    start = perf_counter()
                    states = set()
                    while True:
                        event = (await req("GET", f"/vehicles/{VIN}/latest"))["vehicle"]
                        states.add(event["operating_state"])
                        if event["operating_state"] in [
                            "CHARGING",
                            "READY",
                            "RESUMING_TRIP",
                        ]:
                            break
                        assert perf_counter() - start < 180, states
                        await asyncio.sleep(1)
                    result["states"] = sorted(states)
                    result["charging_event"] = event
                result["stop"] = await req("POST", "/simulator/stop")
                # A seed during a run must cancel the task and clear all plans/cache.
                await req(
                    "POST",
                    "/simulator/start",
                    json={"tick_seconds": 1, "time_scale": 60},
                )
                await req(
                    "POST",
                    "/simulator/scenarios",
                    json={"scenario": scenario, "vehicle_count": 10, "seed": 42},
                )
                status = await req("GET", "/simulator/status")
                assert not status["running"] and status["emitted_events"] == 0
                assert not await req("GET", f"/charging/plans?vin={VIN}")
                result["reset_verified"] = status
                result["health"] = await req("GET", "/health/ready")
                result["passed"] = True
                evidence.append(result)
                out.write_text(json.dumps(evidence, indent=2))
                print(scenario, "PASS", flush=True)
            # Repeat view/reject/cancel/regenerate against the deployed API.
            await req(
                "POST", "/simulator/scenarios", json={"vehicle_count": 10, "seed": 42}
            )
            p = await req("POST", f"/charging/plans/{VIN}")
            for _ in range(2):
                await req("POST", f"/charging/plans/{p['plan_id']}/reject")
            p = await req("POST", f"/charging/plans/{VIN}")
            for _ in range(2):
                await req("POST", f"/charging/plans/{p['plan_id']}/approve")
            for _ in range(2):
                await req("POST", f"/charging/plans/{p['plan_id']}/cancel")
            new = await req("POST", f"/charging/plans/{VIN}")
            assert new["plan_id"] != p["plan_id"]
            evidence.append({"plan_lifecycle_repeats": "PASS"})
            out.write_text(json.dumps(evidence, indent=2))
        finally:
            await req("POST", "/simulator/stop")
            await req(
                "POST", "/simulator/scenarios", json={"vehicle_count": 10, "seed": 42}
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
