"""Measure the deployed simulator, including real dependencies and dashboard load."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import httpx


async def main(args):
    results = []
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/") + "/api/v1", timeout=90
    ) as client:

        async def call(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        try:
            for count in args.vehicles:
                await call(
                    "POST",
                    "/simulator/scenarios",
                    json={"scenario": "NORMAL_DAY", "vehicle_count": count, "seed": 42},
                )
                await call(
                    "POST",
                    "/simulator/start",
                    json={"tick_seconds": 1, "time_scale": 60},
                )
                await asyncio.sleep(5)
                initial = await call("GET", "/simulator/status")
                started = perf_counter()
                samples, latencies = [], []
                while perf_counter() - started < args.seconds:
                    sample_start = perf_counter()
                    status, health, fleet = await asyncio.gather(
                        call("GET", "/simulator/status"),
                        call("GET", "/health/ready"),
                        call("GET", "/fleet/vehicles?limit=200"),
                    )
                    latencies.append(perf_counter() - sample_start)
                    assert status["running"] and not status["error"], status
                    samples.append(
                        {
                            "elapsed": perf_counter() - started,
                            "events": status["emitted_events"],
                            "lag": health["consumer"]["lag"],
                            "vehicles_returned": len(fleet["vehicles"]),
                        }
                    )
                    await asyncio.sleep(max(0, 2 - (perf_counter() - sample_start)))
                final = await call("POST", "/simulator/stop")
                elapsed = perf_counter() - started
                latencies.sort()
                result = {
                    "vehicles": count,
                    "tick_seconds": 1,
                    "time_scale": 60,
                    "duration_seconds": round(elapsed, 2),
                    "events": final["emitted_events"] - initial["emitted_events"],
                    "events_per_second": round(
                        (final["emitted_events"] - initial["emitted_events"]) / elapsed,
                        2,
                    ),
                    "dashboard_p95_seconds": round(
                        latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
                        3,
                    ),
                    "max_consumer_lag": max(s["lag"] for s in samples),
                    "samples": samples,
                }
                results.append(result)
                Path(args.output).write_text(
                    json.dumps(results, indent=2), encoding="utf-8"
                )
                print({k: v for k, v in result.items() if k != "samples"}, flush=True)
        finally:
            await call("POST", "/simulator/stop")
            await call(
                "POST",
                "/simulator/scenarios",
                json={
                    "scenario": "LOW_BATTERY_BEFORE_TRIP",
                    "vehicle_count": 10,
                    "seed": 42,
                },
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--vehicles", type=int, nargs="+", default=[10, 50])
    asyncio.run(main(parser.parse_args()))
