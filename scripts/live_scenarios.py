"""Smoke-test the published scenario catalogue using disposable SIM records."""

import argparse
import asyncio
import json
from pathlib import Path
from time import monotonic

import httpx

VIN = "SIM00000000000001"


async def main(args):
    evidence = []
    out = Path(args.output)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/") + "/api/v1", timeout=90
    ) as client:

        async def req(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        async def seed(sid, variant="offline"):
            await req(
                "POST",
                "/simulator/scenarios",
                json={
                    "scenario": sid,
                    "variant": variant,
                    "vehicle_count": 10,
                    "seed": 42,
                },
            )

        try:
            catalog = (await req("GET", "/fleet/manager"))["scenario_catalog"]
            assert len(catalog) == 15 and len({s["group"] for s in catalog}) == 4
            for entry in catalog:
                sid = entry["id"]
                await seed(sid)
                d = await req("GET", f"/charging/recommendations/{VIN}")
                p = d.get("plan")
                no_plan = {
                    "NORMAL_DAY",
                    "NORMAL_LATER",
                    "NONFINAL_PRIORITY",
                    "NONFINAL_CONTINUATION",
                    "NONFINAL_CONFLICT",
                    "FINAL_PRIORITY",
                }
                assert bool(p) == (sid not in no_plan), (sid, d)
                result = {
                    "scenario": sid,
                    "passed": True,
                    "station": p["charger_id"] if p else None,
                    "target": p["target_soc_pct"] if p else None,
                }
                if sid == "CHARGER_RELAXED":
                    assert p["charger_id"] == "SIM-CHARGER-CHEAP"
                if sid == "CHARGER_CONGESTION":
                    assert p["charger_id"] == "SIM-CHARGER-FAST"
                if sid in {
                    "NONFINAL_PRIORITY",
                    "NONFINAL_CONTINUATION",
                    "FINAL_PRIORITY",
                    "NONFINAL_CONFLICT",
                }:
                    choice = await req("GET", f"/charging/manager-decisions/{VIN}")
                    assert choice["delay_available"]
                    assert choice["deliver_now_available"] == (
                        sid != "NONFINAL_CONFLICT"
                    )
                    result["manager_choices"] = {
                        k: choice[k]
                        for k in ["delay_available", "deliver_now_available"]
                    }
                evidence.append(result)
                out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
                print(sid, "PASS", flush=True)
            for variant in ["offline", "faulty", "incompatible", "all_unavailable"]:
                await seed("CHARGER_FAILURE", variant)
                d = await req("GET", f"/charging/recommendations/{VIN}")
                assert bool(d.get("plan")) == (variant != "all_unavailable")
                if variant == "all_unavailable":
                    v = next(
                        v
                        for v in (await req("GET", "/fleet/manager"))["vehicles"]
                        if v["vin"] == VIN
                    )
                    assert v["manager_readiness"] == "EMERGENCY"
                evidence.append({"variant": variant, "passed": True})
            # Approval and plan review leave a running fleet running.
            await seed("CHARGER_RELAXED")
            p = await req("POST", f"/charging/plans/{VIN}")
            await req(
                "POST", "/simulator/start", json={"tick_seconds": 1, "time_scale": 60}
            )
            await req("POST", f"/charging/plans/{p['plan_id']}/approve")
            assert (await req("GET", "/simulator/status"))["running"]
            deadline = monotonic() + 120
            while monotonic() < deadline:
                e = (await req("GET", f"/vehicles/{VIN}/latest"))["vehicle"]
                if e["operating_state"] in {
                    "EN_ROUTE_TO_CHARGER",
                    "WAITING_FOR_CHARGER",
                    "CHARGING",
                }:
                    assert any(
                        s["stage"] == "decision" for s in e["journey_progress"]["steps"]
                    )
                    evidence.append(
                        {"live_progress": e["operating_state"], "passed": True}
                    )
                    break
                await asyncio.sleep(2)
            else:
                raise AssertionError("Approved vehicle did not progress")
            await req("POST", "/simulator/stop")
            # Both exceptional manager choices execute, not just record a label.
            for action, state in [
                ("accept-delay", "EN_ROUTE_TO_CHARGER"),
                ("deliver-now", "DRIVING"),
            ]:
                await seed("NONFINAL_CONTINUATION")
                d = await req("GET", f"/charging/manager-decisions/{VIN}")
                body = {
                    k: d[k]
                    for k in [
                        "simulation_run_id",
                        "trip_id",
                        "telemetry_sequence",
                        "decision_token",
                    ]
                }
                await req(
                    "POST", f"/charging/manager-decisions/{VIN}/{action}", json=body
                )
                await req(
                    "POST",
                    "/simulator/start",
                    json={"tick_seconds": 1, "time_scale": 60},
                )
                deadline = monotonic() + 120
                while monotonic() < deadline:
                    e = (await req("GET", f"/vehicles/{VIN}/latest"))["vehicle"]
                    if e["operating_state"] == state:
                        break
                    await asyncio.sleep(2)
                else:
                    raise AssertionError((action, e))
                evidence.append(
                    {"manager_choice": action, "state": state, "passed": True}
                )
                await req("POST", "/simulator/stop")
            await req("GET", "/health/ready")
            out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            print(
                "Station variations, running approval and both manager choices PASS",
                flush=True,
            )
        finally:
            await req("POST", "/simulator/stop")
            await seed("NONFINAL_RELAXED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
