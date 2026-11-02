import asyncio
import unittest

from agent import investigate
from decision import assess, drive_step


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.van = dict(connector="CCS2", battery_kwh=8, kwh_per_km=.25, road_km=0)
        station = dict(connector="CCS2", online=True, free_ports=1)
        self.stations = [
            dict(station, id="Busy", road_km=1, free_ports=0),
            dict(station, id="Wrong", road_km=2, connector="Type2"),
            dict(station, id="Broken", road_km=3, online=False),
            dict(station, id="Edge", road_km=24),
            dict(station, id="Far", road_km=24.01),
        ]

    def test_exact_reserve_boundary_and_filters(self):
        facts = assess(self.van, self.stations)
        self.assertEqual(facts["charger"], "Edge")
        self.assertEqual(facts["safe_km"], 24)
        self.assertEqual(len([c for c in facts["options"] if c["reason"] == "eligible"]), 1)
        self.van["battery_kwh"] = 2
        self.assertIsNone(assess(self.van, self.stations)["charger"])

    def test_drive_arrival_assistance_and_turnaround(self):
        van = dict(self.van, capacity_kwh=40, road_km=23.5)
        station = [self.stations[3]]
        position, battery, direction = drive_step(van, station, 1)
        self.assertEqual((position, battery), (24, 7.875))
        van.update(road_km=position, battery_kwh=battery)
        self.assertEqual(drive_step(van, station, direction)[:2], (24, battery))
        van.update(road_km=0, battery_kwh=2)
        self.assertEqual(drive_step(van, station, 1)[:2], (0, 2))
        van.update(road_km=32, battery_kwh=26)
        self.assertEqual(drive_step(van, station, 1), (31, 25.75, -1))

    def test_agent_executes_selected_tools_and_returns_evidence(self):
        steps = iter(["compare_chargers", "read_battery", None])
        async def ask(contents, tools):
            step = next(steps)
            if step:
                return {"role": "model", "parts": [{"functionCall": {"name": step, "args": {}}}]}
            self.assertEqual(contents[-1]["parts"][0]["functionResponse"]["response"]["safe_range_km"], 24)
            return {"role": "model", "parts": [{"text": "Edge is reachable."}]}
        result = asyncio.run(investigate(self.van, assess(self.van, self.stations), ask))
        self.assertEqual(result["trace"], ["compare_chargers", "read_battery"])

    def test_unknown_tool_is_refused(self):
        async def ask(contents, tools):
            return {"parts": [{"functionCall": {"name": "book_port"}}]}
        with self.assertRaises(ValueError):
            asyncio.run(investigate(self.van, assess(self.van, self.stations), ask))

