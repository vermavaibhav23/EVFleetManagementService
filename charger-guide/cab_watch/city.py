"""Harbour City is a fictional street grid. One map unit represents six metres."""
from heapq import heappop, heappush
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


# Street lengths are in metres. Each undirected road is listed once.
NODES = {
    "A": (78, 92), "B": (254, 52), "C": (430, 100), "D": (650, 52), "E": (853, 95),
    "F": (45, 205), "G": (195, 175), "H": (338, 195), "I": (532, 162),
    "J": (690, 195), "K": (875, 228), "L": (96, 305), "M": (260, 288),
    "N": (472, 275), "O": (615, 315), "P": (785, 310), "Q": (60, 415),
    "R": (235, 398), "S": (390, 378), "T": (587, 430), "U": (827, 402),
    "V": (125, 502), "W": (310, 492), "X": (483, 515), "Y": (703, 483),
    "Z": (875, 505),
}
ROADS = """
AB BC CD DE AF AG BG BH CH CI DI DJ EJ EK FG GH HI IJ JK FL GL GM HM HN IN
IO JO JP KP LM MN NO OP LQ LR MR MS NS NT OT PU QR RS ST TU QV RV RW SW SX
TX TY UY UZ VW WX XY YZ
""".split()
GRAPH = {node: {} for node in NODES}
for start, end in ROADS:
    x1, y1 = NODES[start]
    x2, y2 = NODES[end]
    metres = round(hypot(x2 - x1, y2 - y1) * METRES)
    GRAPH[start][end] = GRAPH[end][start] = metres

COLORS = [
    "#2164cb", "#a53685", "#00796b", "#be5422", "#664bac", "#a62844",
    "#11759c", "#637521", "#ab400f", "#594ba6", "#08805a", "#ad365e",
    "#38578c", "#885a0b", "#426d57", "#804991", "#2d7782", "#8c5436",
    "#536aaf", "#923b32",
]


def shortest_route(start, end):
    """Return the shortest path by total street length (Dijkstra)."""
    distance = {start: 0}
    parent = {}
    queue = [(0, start)]
    while queue:
        cost, node = heappop(queue)
        if node == end:
            path = [node]
            while node != start:
                node = parent[node]
                path.append(node)
            return path[::-1], cost
        if cost != distance[node]:
            continue
        for neighbour, weight in GRAPH[node].items():
            candidate = cost + weight
            if candidate < distance.get(neighbour, float("inf")):
                distance[neighbour] = candidate
                parent[neighbour] = node
                heappush(queue, (candidate, neighbour))
    raise ValueError("No road connects these intersections")


def map_point(lat, lon):
    return ((lon - ORIGIN[1]) * LON_SCALE / METRES,
            (ORIGIN[0] - lat) * LAT_SCALE / METRES)


def gps(point):
    x, y = point
    return ORIGIN[0] - y * METRES / LAT_SCALE, ORIGIN[1] + x * METRES / LON_SCALE


def offset_metres(route_index, lat, lon):
    return distance_from_path(ROUTES[route_index][3], lat, lon)


def distance_from_path(line, lat, lon):
    x, y = map_point(lat, lon)
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

