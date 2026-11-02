RESERVE_KWH = 2
LOW_FRACTION = 0.25


def assess(van, chargers):
    """Distances in this demo are measured along one simulated road."""
    usable = max(0, van["battery_kwh"] - RESERVE_KWH)
    safe_km = usable / van["kwh_per_km"]
    options = []
    for station in chargers:
        distance = abs(station["road_km"] - van["road_km"])
        if station["connector"] != van["connector"]:
            reason = "wrong connector"
        elif not station["online"]:
            reason = "offline"
        elif station["free_ports"] == 0:
            reason = "all ports occupied"
        elif distance * van["kwh_per_km"] + RESERVE_KWH > van["battery_kwh"]:
            reason = "would breach the battery reserve"
        else:
            reason = "eligible"
        options.append(dict(charger=station["id"], distance_km=distance, reason=reason))
    options.sort(key=lambda item: (item["distance_km"], item["charger"]))
    choice = next((item for item in options if item["reason"] == "eligible"), None)
    reason = (f'{choice["charger"]} is the nearest eligible charger at {choice["distance_km"]:g} km.'
              if choice else "No available compatible charger is within safe range. Request assistance.")
    return dict(safe_km=round(safe_km, 2), options=options,
                charger=choice["charger"] if choice else None, reason=reason)

