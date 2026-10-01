"""Shared piecewise charging curve used by simulation and planning."""


def charge_for_seconds(soc, target, capacity, power, efficiency, seconds):
    initial = soc
    if capacity <= 0 or power <= 0:
        return soc, 0.0
    for ceiling, taper in ((80, 1.0), (90, 0.6), (100, 0.3)):
        end = min(ceiling, target)
        if end <= soc:
            continue
        rate = power * efficiency * taper / capacity * 100 / 3600
        used = min(seconds, (end - soc) / rate)
        soc += rate * used
        seconds -= used
        if seconds <= 0:
            break
    return soc, (soc - initial) * capacity / 100


def charging_seconds(soc, target, capacity, power, efficiency):
    seconds = 0.0
    for ceiling, taper in ((80, 1.0), (90, 0.6), (100, 0.3)):
        end = min(ceiling, target)
        if end > soc:
            seconds += (
                (end - soc) / 100 * capacity / (power * efficiency * taper) * 3600
            )
            soc = end
    return seconds
