"""Optional end-to-end ownership check against a running CabWatch instance."""

import os
import secrets
import time
import unittest
from uuid import uuid4

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
            depot_counts = [
                sum(c["parked_depot_id"] == d["id"] for c in cars) for d in city.DEPOTS
            ]
            self.assertEqual(sorted(depot_counts), [12, 12, 13, 13])
            self.assertEqual(client.get("me/fleet", headers=b).json()["vehicles"], [])
            other=client.put("me/fleet",headers=b,json={"count":2}).json()["vehicles"]
            self.assertTrue(plates.isdisjoint(v["id"] for v in other))
            client.put("me/fleet",headers=b,json={"count":1}).raise_for_status()
            tid = str(uuid4())
            path = "me/trips/" + tid
            route = dict(pickup="G", dropoff="K")
            trip = client.put(path, json=route, headers=a)
            trip.raise_for_status()
            plan = trip.json()["planned_legs"]
            self.assertEqual(set(plan), {"to_pickup", "customer_ride", "to_depot"})
            self.assertEqual(plan["customer_ride"], ["G","H","I","J","K"])
            self.assertEqual(plan["to_pickup"], ["A","G"])
            self.assertEqual(plan["to_depot"], ["K","E"])
            self.assertEqual(client.put(path,json=route,headers=a).json()["planned_legs"],plan)
            self.assertEqual(client.put(path, json=route, headers=b).status_code, 409)
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
            gps = {"readings": [dict(trip_id=tid, sequence=1, leg=0, lat=lat, lon=lon)]}
            self.assertEqual(
                client.post("me/gps", headers=b, json=gps).status_code, 422
            )
            self.assertEqual(
                client.post("me/gps", headers=a, json=gps).status_code, 202
            )
            for _ in range(60):
                state = client.get("me/state", headers=a).json()
                if state["alerts"]:
                    break
                time.sleep(0.4)
            self.assertTrue(state["alerts"])
            alert = state["alerts"][0]["id"]
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
            # Cross the two leg boundaries, then park at E after the G-to-K trip.
            for sequence,leg,node in [(2,1,"G"),(3,2,"K"),(4,2,"E")]:
                lat,lon=city.gps(city.NODES[node])
                reading=dict(trip_id=tid, sequence=sequence, leg=leg, lat=lat, lon=lon)
                reply=client.post("me/gps",headers=a,json={"readings":[reading]})
                self.assertEqual(reply.status_code,202)
            for _ in range(60):
                final=client.get("me/fleet",headers=a).json()
                if final["trips"][0]["completed"]:break
                time.sleep(.4)
            self.assertTrue(final["trips"][0]["completed"])
            parked=next(v for v in final["vehicles"] if v["id"]==busy["id"])
            self.assertEqual((parked["status"],parked["parked_depot_id"]),("available","northeast"))
            self.assertEqual(final["trips"][0]["depot"],"Northwest Depot")
