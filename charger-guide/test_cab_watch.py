import unittest
from cab_watch.city import ROUTES, gps, next_streak, offset_metres


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
        self.assertEqual(next_streak(200,2,True),0)
        self.assertEqual(next_streak(201,2,True),3)
        self.assertEqual(next_streak(201,2,False),1)

