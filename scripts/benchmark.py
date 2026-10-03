"""Reproducible local benchmark. No HTTP or production writes."""

import argparse
import json
import sys
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.domain import LoadRequest
from app.services.optimizer import optimize
from app.services.runner import priority
from app.services.seed import seed
from app.services.validation import validate


def run(scenario, count, seconds):
    doc = seed(
        LoadRequest(
            scenario=scenario,
            vehicle_count=count,
            seed=42,
            start_time="2026-10-04T08:00:00+05:30",
        )
    )
    doc["policy"]["solver_seconds"] = seconds
    started = monotonic()
    rows = []
    for vin in sorted(doc["vehicles"], key=lambda v: priority(doc, v)):
        result = optimize(doc, vin)
        row = {k: v for k, v in result.items() if k != "plan"}
        row["vin"] = vin
        if "plan" in result:
            plan = deepcopy(result["plan"])
            assert not validate(doc, plan)
            plan["status"] = "APPROVED"
            doc["plans"][plan["plan_id"]] = plan
            row.update(cost=plan["total_cost"], solver=plan["solver"])
        rows.append(row)
    return dict(
        scenario=scenario,
        vehicles=count,
        per_vehicle_seconds=seconds,
        elapsed_seconds=monotonic() - started,
        statuses=dict(Counter(r["status"] for r in rows)),
        results=rows,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", nargs="+", type=int, default=[12, 24])
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--output", default="benchmark.json")
    args = parser.parse_args()
    results = []
    for count in args.counts:
        for scenario in ("NORMAL_DAY", "EDGE_CASE_DAY"):
            result = run(scenario, count, args.seconds)
            results.append(result)
            print(
                scenario,
                count,
                result["statuses"],
                round(result["elapsed_seconds"], 2),
                flush=True,
            )
            Path(args.output).write_text(
                json.dumps(
                    dict(
                        recorded_at=datetime.now(timezone.utc).isoformat(),
                        results=results,
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
