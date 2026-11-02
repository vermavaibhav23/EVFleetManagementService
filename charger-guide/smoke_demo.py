import sys
import time

import httpx


base = sys.argv[1]
with httpx.Client(base_url=base, timeout=90) as client:
    def van():
        response = client.get("/fleet")
        response.raise_for_status()
        fleet = response.json()
        assert {v["id"] for v in fleet} == {"Van-A", "Van-B"}
        return next(v for v in fleet if v["id"] == "Van-A")

    def send(level):
        response = client.post(f"/demo/Van-A/{level}")
        response.raise_for_status()
        sequence = response.json()["sequence"]
        for _ in range(20):
            row = van()
            if row["last_sequence"] == sequence:
                assert row["battery_kwh"] == level
                print(level, row["alert_id"], row["reason"], flush=True)
                return row
            time.sleep(0.5)
        raise AssertionError("Kafka reading did not appear")

    assert client.get("/").is_success
    initial = van()
    try:
        low = send(8)
        assert "Central" in low["reason"] and low["alert_id"]
        response = client.post(f"/alerts/{low['alert_id']}/review")
        response.raise_for_status()
        review = response.json()
        assert review["facts"]["safe_km"] == 24 and review["facts"]["charger"] == "Central"
        print("Review:", review["mode"], review["trace"], review["summary"], flush=True)
        if review["mode"] == "Gemini review":
            assert sorted(review["trace"]) == ["compare_chargers", "read_battery"]
        assert send(10)["alert_id"] == low["alert_id"]
        critical = send(2)
        assert critical["alert_id"] == low["alert_id"] and "assistance" in critical["reason"]
        assert send(26)["alert_id"] is None
        assert client.post(f"/alerts/{low['alert_id']}/review").status_code == 404
        print("Alert lifecycle passed.", flush=True)
    finally:
        if van()["battery_kwh"] != initial["battery_kwh"]:
            send(int(initial["battery_kwh"]))
