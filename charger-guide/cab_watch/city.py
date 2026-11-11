"""Harbour City is a fictional street grid. One map unit represents six metres."""
from heapq import heappop, heappush
from math import cos, hypot, radians

METRES = 6
ORIGIN = (13.10, 80.20)
LAT_SCALE = 111320
LON_SCALE = LAT_SCALE * cos(radians(ORIGIN[0]))
LIMIT = 200
# Street lengths are in metres. Each undirected road is listed once.
NODES = {
    "A": [78, 92], "B": [254, 52], "C": [430, 100], "D": [650, 52], "E": [853, 95],
    "F": [45, 205], "G": [195, 175], "H": [338, 195], "I": [532, 162],
    "J": [690, 195], "K": [875, 228], "L": [96, 305], "M": [260, 288],
    "N": [472, 275], "O": [615, 315], "P": [785, 310], "Q": [60, 415],
    "R": [235, 398], "S": [390, 378], "T": [587, 430], "U": [827, 402],
    "V": [125, 502], "W": [310, 492], "X": [483, 515], "Y": [703, 483],
    "Z": [875, 505],
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


def distance_from_path(line, lat, lon):
    x, y = map_point(lat, lon)
    if len(line) == 1:
        return hypot(x - line[0][0], y - line[0][1]) * METRES
    distances = []
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        distances.append(hypot(x - ax - t * dx, y - ay - t * dy))
    return min(distances) * METRES
