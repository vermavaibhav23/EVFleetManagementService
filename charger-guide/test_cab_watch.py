import unittest
from cab_watch.city import GRAPH, NODES, shortest_route, gps, distance_from_path


class CabGeometryTests(unittest.TestCase):
    def test_points_on_each_road(self):
        for start, neighbours in GRAPH.items():
            for end in neighbours:
                p, q = NODES[start], NODES[end]
                mid = ((p[0]+q[0])/2, (p[1]+q[1])/2)
                for point in (p, mid, q):
                    self.assertAlmostEqual(distance_from_path([p, q], *gps(point)), 0, places=5)

    def test_side_street_and_segment_endpoint(self):
        line = [(80,80), (625,80), (625,320)]
        self.assertAlmostEqual(distance_from_path(line, *gps((185,160))), 480, places=5)
        self.assertAlmostEqual(distance_from_path(line, *gps((45,80))), 210, places=5)

    def test_distance_to_dropoff(self):
        self.assertAlmostEqual(distance_from_path([NODES["A"]], *gps(NODES["A"])), 0)


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
