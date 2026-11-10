"""Run with CABWATCH_TEST_URL pointing at a test instance of /cab-watch."""
from concurrent.futures import ThreadPoolExecutor
import os
import secrets
import unittest
from uuid import uuid4

import httpx
from cab_watch import city


@unittest.skipUnless(os.getenv("CABWATCH_TEST_URL"), "Requires running test instance")
class AccountFlow(unittest.TestCase):
    def test_direct_trip_retry_alert_recovery_and_dropoff(self):
        with httpx.Client(base_url=os.environ["CABWATCH_TEST_URL"].rstrip("/") + "/", timeout=60) as client:
            def account():
                reply = client.post("auth/register", json={
                    "name": "Trip check", "email": f"ride-{uuid4().hex}@example.invalid",
                    "password": secrets.token_urlsafe(20)})
                reply.raise_for_status()
                return {"Authorization": "Bearer " + reply.json()["access_token"]}

            a, b = account(), account()
            self.assertEqual(client.get("me/trips", headers=a).json()["trips"], [])
            self.assertEqual(client.get("me/trips").status_code, 401)
            request = {"pickup": "G", "dropoff": "K"}
            headers = {**a, "Idempotency-Key": str(uuid4())}
            with ThreadPoolExecutor(max_workers=2) as pool:
                replies = list(pool.map(lambda _: client.post("me/trips", headers=headers, json=request), range(2)))
            self.assertEqual(sorted(r.status_code for r in replies), [200, 201])
            trip = replies[0].json()
            self.assertEqual(trip["id"], replies[1].json()["id"])
            self.assertEqual(trip["route"], ["G","H","I","J","K"])
            self.assertEqual(trip["metres"], 4143)
            self.assertRegex(trip["vehicle_id"], r"^CB#[0-9]{3,}$")
            self.assertEqual(client.post("me/trips", headers=headers, json={"pickup":"A","dropoff":"K"}).status_code, 409)
            self.assertEqual(client.get("me/trips", headers=b).json()["trips"], [])
            other = client.post("me/trips", headers={**b, "Idempotency-Key": str(uuid4())}, json=request)
            self.assertEqual(other.status_code, 201)
            self.assertNotEqual(other.json()["vehicle_id"], trip["vehicle_id"])

            def sample(seq, node, mode, owner=a):
                lat, lon = city.gps(city.NODES[node])
                return client.post("me/gps", headers=owner, json={"readings": [{
                    "trip_id":trip["id"], "sequence":seq, "lat":lat, "lon":lon,
                    "motion":{"index":0,"mode":mode}}]})

            self.assertEqual(sample(1,"G","route",b).status_code,422)
            self.assertIsNone(sample(1,"G","route").json()["results"][0]["alert"])
            self.assertEqual(sample(2,"D","detour").json()["results"][0]["alert"]["opened_sequence"],2)
            self.assertEqual(sample(3,"I","route").json()["results"][0]["alert"]["closed_sequence"],3)
            self.assertFalse(sample(4,"K","detour").json()["results"][0]["completed"])
            self.assertTrue(sample(5,"K","arrived").json()["results"][0]["completed"])
            restored = client.get("me/trips",headers=a).json()
            self.assertTrue(restored["trips"][0]["completed"])
            self.assertEqual(restored["trips"][0]["last_sequence"],5)
            self.assertEqual(len(restored["alerts"]),1)
            self.assertEqual(client.get("me/trips",headers=b).json()["alerts"],[])
            another = client.post("me/trips",headers={**a,"Idempotency-Key":str(uuid4())},json=request)
            self.assertEqual(another.status_code,201)
            self.assertEqual(another.json()["vehicle_id"],trip["vehicle_id"])
            self.assertEqual(len(client.get("me/trips",headers=a).json()["trips"]),2)
