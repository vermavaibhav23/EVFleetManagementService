import unittest
from cab_watch.city import (GRAPH, NODES, shortest_route, ROUTES, gps, next_streak,
                            offset_metres, distance_from_path, depot_allocation,
                            nearest_available)


class CabGeometryTests(unittest.TestCase):
    def test_every_route_and_corner(self):
        for index, (_, _, _, line) in enumerate(ROUTES):
            for p, q in zip(line, line[1:]):
                mid = ((p[0]+q[0])/2, (p[1]+q[1])/2)
                for point in (p, mid, q):
                    self.assertAlmostEqual(offset_metres(index, *gps(point)), 0, places=5)

    def test_side_street_and_segment_endpoint(self):
        self.assertAlmostEqual(offset_metres(0, *gps((185,160))), 480, places=5)
        self.assertAlmostEqual(offset_metres(0, *gps((45,80))), 210, places=5)

    def test_boundary_gap_and_recovery(self):
        self.assertEqual(next_streak(199.99,0,True),0)
        self.assertEqual(next_streak(200,0,True),1)
        self.assertEqual(next_streak(201,2,True),3)
        self.assertEqual(next_streak(201,2,False),1)


class RoutingTests(unittest.TestCase):
    def test_every_pair_has_a_shortest_path_along_actual_roads(self):
        self.assertEqual(set(NODES), set("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        # Independent all-pairs reference for the weighted graph.
        cost = {(a,b): (0 if a == b else GRAPH[a].get(b, float("inf")))
                for a in NODES for b in NODES}
        for k in NODES:
            for a in NODES:
                for b in NODES:
                    cost[a,b] = min(cost[a,b], cost[a,k] + cost[k,b])
        for a in NODES:
            for b in NODES:
                path, metres = shortest_route(a,b)
                self.assertEqual((path[0],path[-1]), (a,b))
                self.assertEqual(sum(GRAPH[u][v] for u,v in zip(path,path[1:])), metres)
                self.assertEqual(metres, cost[a,b])


class FleetTests(unittest.TestCase):
    def test_every_fleet_size_is_split_as_evenly_as_possible(self):
        for count in range(1, 21):
            allocation = depot_allocation(count, 4)
            self.assertEqual(len(allocation), 4)
            self.assertEqual(sum(allocation), count)
            self.assertLessEqual(max(allocation) - min(allocation), 1)
        self.assertEqual(depot_allocation(10, 4), [3, 3, 2, 2])

    def test_nearest_available_car_uses_road_distance(self):
        vehicles = [
            {"vehicle_index": 0, "status": "busy", "current_node": "Z"},
            {"vehicle_index": 1, "status": "available", "current_node": "A"},
            {"vehicle_index": 2, "status": "available", "current_node": "E"},
        ]
        self.assertIs(nearest_available(vehicles, "E"), vehicles[2])
        self.assertIsNone(nearest_available(vehicles[:1], "E"))

    def test_zero_length_leg_has_a_measurable_offset(self):
        self.assertAlmostEqual(distance_from_path([NODES["A"]], *gps(NODES["A"])), 0)

