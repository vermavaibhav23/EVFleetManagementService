import math


def distance(a, b):
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(math.radians(b["lon"] - a["lon"]) / 2) ** 2
    )
    return 6371 * 2 * math.atan2(math.sqrt(h), math.sqrt(max(0, 1 - h)))


def point(lat, lon, km, bearing):
    a, b, d = math.radians(lat), math.radians(bearing), km / 6371
    c = math.asin(math.sin(a) * math.cos(d) + math.cos(a) * math.sin(d) * math.cos(b))
    e = math.radians(lon) + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(a), math.cos(d) - math.sin(a) * math.sin(c)
    )
    return {"lat": math.degrees(c), "lon": math.degrees(e)}
