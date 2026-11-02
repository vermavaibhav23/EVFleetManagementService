"""Harbour City is a fictional street grid. One map unit represents six metres."""
from math import cos, hypot, radians

WIDTH, HEIGHT = 960, 560
XS = [80, 185, 295, 405, 515, 625, 735, 850]
YS = [80, 160, 240, 320, 400, 480]
METRES = 6
ORIGIN = (13.10, 80.20)
LAT_SCALE = 111320
LON_SCALE = LAT_SCALE * cos(radians(ORIGIN[0]))
LIMIT = 200
REQUIRED = 3
TOPIC = "cab.location"
ROUTES = [
    ("Medical College", "Central Station", "#2663da", [(80,80),(625,80),(625,320)]),
    ("Harbour Market", "City Library", "#a13e96", [(850,160),(405,160),(405,400)]),
    ("Arts Quarter", "Marina Gate", "#007a6c", [(80,240),(735,240),(735,480)]),
    ("Civic Square", "North Arcade", "#b95012", [(850,320),(515,320),(515,80)]),
    ("West Gardens", "Museum Row", "#6547bc", [(80,400),(295,400),(295,160)]),
    ("Ferry Terminal", "Foundry Lane", "#a12d43", [(850,480),(185,480),(185,320)]),
]


def map_point(lat, lon):
    return ((lon - ORIGIN[1]) * LON_SCALE / METRES,
            (ORIGIN[0] - lat) * LAT_SCALE / METRES)


def gps(point):
    x, y = point
    return ORIGIN[0] - y * METRES / LAT_SCALE, ORIGIN[1] + x * METRES / LON_SCALE


def offset_metres(route_index, lat, lon):
    x, y = map_point(lat, lon)
    line = ROUTES[route_index][3]
    distances = []
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        distances.append(hypot(x - ax - t * dx, y - ay - t * dy))
    return min(distances) * METRES


def next_streak(distance, previous, consecutive):
    if distance <= LIMIT:
        return 0
    return (previous if consecutive else 0) + 1

