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
            depot_counts = [
                sum(c["depot_id"] == d["id"] for c in cars) for d in city.DEPOTS
            ]
            self.assertEqual(sorted(depot_counts), [12, 12, 13, 13])
            self.assertEqual(client.get("me/fleet", headers=b).json()["vehicles"], [])
            tid = str(uuid4())
            path = "me/trips/" + tid
            route = dict(pickup="G", dropoff="K")
            trip = client.put(path, json=route, headers=a)
            trip.raise_for_status()
            self.assertEqual(client.put(path, json=route, headers=b).status_code, 409)
            repeated = client.put("me/fleet", headers=a, json={"count": 50}).json()
            self.assertEqual(
                [c["id"] for c in cars], [c["id"] for c in repeated["vehicles"]]
            )
            self.assertEqual(repeated["trips"][0]["id"], tid)
            self.assertTrue(any(c["status"] == "busy" for c in repeated["vehicles"]))
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
