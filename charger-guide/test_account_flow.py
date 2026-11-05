"""Optional end-to-end ownership check against a running CabWatch instance."""

from concurrent.futures import ThreadPoolExecutor
import os
import secrets
import time
import unittest
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
from cab_watch import city


@unittest.skipUnless(
    os.getenv("CABWATCH_TEST_URL"),
    "Set CABWATCH_TEST_URL to run against a test deployment",
)
class AccountFlow(unittest.TestCase):
    def test_accounts_cannot_share_or_modify_each_others_fleet(self):
        with httpx.Client(
            base_url=os.environ["CABWATCH_TEST_URL"].rstrip("/") + "/", timeout=40
        ) as client:

            def account():
                password = secrets.token_urlsafe(24)
                email = f"cabwatch-check-{uuid4().hex}@example.invalid"
                result = client.post(
                    "auth/register",
                    json=dict(name="Fleet check", email=email, password=password),
                )
                result.raise_for_status()
                return dict(
                    Authorization="Bearer " + result.json()["access_token"]
                ), dict(email=email, password=password)

            self.assertEqual(client.get("me/fleet").status_code, 401)
            a, credentials = account()
            b, _ = account()
            resp = client.put("me/fleet", headers=a, json={"count": 50})
            resp.raise_for_status()
            cars = resp.json()["vehicles"]
            self.assertEqual(len(cars), 50)
            self.assertRegex(cars[0]["id"], r"^KA[0-9]{2}[A-Z]{2}[0-9]{4}$")
            plates = {c["id"] for c in cars}
            self.assertEqual(len(plates),50)
            depots = client.get("network").json()["depots"]
            by_node = {d["node"]: str(UUID(d["id"])) for d in depots}
            depot_counts = [
                sum(c["parked_depot_id"] == d["id"] for c in cars) for d in depots
            ]
            self.assertEqual(sorted(depot_counts), [12, 12, 13, 13])
            counts = dict(zip((d["id"] for d in depots), depot_counts))
            self.assertEqual(resp.json()["parked_counts"], counts)
            self.assertEqual(client.get("me/state", headers=b).json()["parked_counts"], dict.fromkeys(counts, 0))
            self.assertEqual(client.get("me/fleet", headers=b).json()["vehicles"], [])
            other=client.put("me/fleet",headers=b,json={"count":2}).json()["vehicles"]
            self.assertTrue(plates.isdisjoint(v["id"] for v in other))
            client.put("me/fleet",headers=b,json={"count":1}).raise_for_status()
            path = "me/trips"
            key = str(uuid4())
            headers = {**a, "Idempotency-Key": key}
            route = dict(pickup="G", dropoff="K")
            with ThreadPoolExecutor(max_workers=2) as executor:
                replies = list(executor.map(lambda _: client.post(path, json=route, headers=headers), range(2)))
            self.assertEqual(sorted(r.status_code for r in replies), [200, 201])
            trip = replies[0]
            tid = trip.json()["id"]
            self.assertEqual(replies[1].json()["id"], tid)
            self.assertNotEqual(tid, key)
            self.assertEqual(client.post(path, json=dict(pickup="A", dropoff="K"), headers=headers).status_code, 409)
            independent = client.post(path, json=route, headers={**b, "Idempotency-Key": key})
            self.assertEqual(independent.status_code, 201)
            self.assertNotEqual(independent.json()["id"], tid)
            counts[by_node["A"]] -= 1
            self.assertEqual(trip.json()["parked_counts"], counts)
            self.assertEqual(trip.json()["departure_depot_id"], by_node["A"])
            self.assertEqual(trip.json()["arrival_depot_id"], by_node["E"])
            plan = trip.json()["planned_legs"]
            self.assertEqual(set(plan), {"to_pickup", "customer_ride", "to_depot"})
            self.assertEqual(plan["customer_ride"], ["G","H","I","J","K"])
            self.assertEqual(plan["to_pickup"], ["A","G"])
            self.assertEqual(plan["to_depot"], ["K","E"])
            self.assertEqual(client.put(path+"/"+tid,json=route,headers=a).json()["planned_legs"],plan)
            self.assertEqual(client.put(path+"/"+tid, json=route, headers=b).status_code, 409)
            repeated = client.put("me/fleet", headers=a, json={"count": 50}).json()
            self.assertEqual(
                [c["id"] for c in cars], [c["id"] for c in repeated["vehicles"]]
            )
            self.assertEqual(repeated["trips"][0]["id"], tid)
            busy = next(c for c in repeated["vehicles"] if c["status"] == "busy")
            self.assertEqual(busy["id"],trip.json()["vehicle_id"])
            self.assertIsNone(busy["parked_depot_id"])
            self.assertIsNone(busy["current_node"])
            lat, lon = city.gps(city.NODES["Z"])
            capture_time = datetime.now(timezone.utc)
            gps = {"readings": [dict(trip_id=tid, sequence=1, leg=0, lat=lat, lon=lon, captured_at=capture_time.isoformat())]}
            self.assertEqual(
                client.post("me/gps", headers=b, json=gps).status_code, 422
            )
            future = dict(gps["readings"][0], sequence=2)
            future["lat"], future["lon"] = city.gps(city.NODES["A"])
            gap = client.post("me/gps", headers=a, json={"readings": [future]})
            self.assertEqual(gap.status_code, 409)
            self.assertEqual(gap.json()["detail"]["expected_sequence"], 1)
            self.assertEqual(client.get("me/fleet", headers=a).json()["trips"][0]["last_sequence"], 0)
            self.assertEqual(
                client.post("me/gps", headers=a, json=gps).status_code, 200
            )
            for _ in range(60):
                state = client.get("me/state", headers=a).json()
                if state["alerts"]:
                    break
                time.sleep(0.4)
            self.assertTrue(state["alerts"])
            incident = state["alerts"][0]
            self.assertNotIn("opening_samples",incident)
            stale=dict(gps["readings"][0])
            stale["lat"], stale["lon"]=city.gps(city.NODES["A"])
            retry=client.post("me/gps", headers=a, json={"readings":[stale]})
            self.assertFalse(retry.json()["results"][0]["applied"])
            self.assertEqual(retry.json()["results"][0]["alert"]["id"], incident["id"])
            alert_state=client.get("me/state?alerts_only=true", headers=a).json()
            self.assertEqual(set(alert_state), {"alerts"})
            self.assertIsNone(alert_state["alerts"][0]["closed_sequence"])
            alert = incident["id"]
            explanation = client.post(f"me/alerts/{alert}/explain",headers=a)
            explanation.raise_for_status()
            facts = explanation.json()["facts"]
            self.assertEqual(facts["opened_sequence"],incident["opened_sequence"])
            self.assertEqual(facts["peak_m"],round(incident["peak_metres"]))
            self.assertEqual(
                client.post(f"me/alerts/{alert}/explain", headers=b).status_code, 404
            )
            self.assertEqual(client.get("me/state", headers=b).json()["alerts"], [])
            self.assertEqual(
                client.post(
                    "auth/login", json=dict(credentials, password="incorrect-password")
                ).status_code,
                401,
            )
            token = client.post("auth/login", json=credentials).json()["access_token"]
            new_headers = dict(Authorization="Bearer " + token)
            restored = client.get("me/fleet", headers=new_headers).json()
            self.assertEqual(restored["vehicles"], repeated["vehicles"])
            self.assertEqual(restored["trips"][0]["last_sequence"], 1)
            self.assertEqual(datetime.fromisoformat(restored["trips"][0]["captured_at"]), capture_time)
            # Cross the two leg boundaries, then park at E after the G-to-K trip.
            for sequence,leg,node in [(2,1,"G"),(3,2,"K"),(4,2,"E")]:
                lat,lon=city.gps(city.NODES[node])
                reading=dict(trip_id=tid, sequence=sequence, leg=leg, lat=lat, lon=lon)
                reply=client.post("me/gps",headers=a,json={"readings":[reading]})
                self.assertEqual(reply.status_code,200)
            for _ in range(60):
                final=client.get("me/fleet",headers=a).json()
                if final["trips"][0]["completed"]:break
                time.sleep(.4)
            self.assertTrue(final["trips"][0]["completed"])
            self.assertEqual(final["alerts"][0]["opened_sequence"], 1)
            self.assertEqual(final["alerts"][0]["closed_sequence"], 2)
            counts[by_node["E"]] += 1
            self.assertEqual(final["parked_counts"], counts)
            parked=next(v for v in final["vehicles"] if v["id"]==busy["id"])
            self.assertEqual((parked["status"],parked["parked_depot_id"]),("available",by_node["E"]))
            self.assertEqual(final["trips"][0]["depot"],"Northwest Depot")
