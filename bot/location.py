from enum import Enum


class Location(Enum):
    AT_SUPPLY = "AT_SUPPLY"
    AT_DEMAND = "AT_DEMAND"
    ABOVE_SUPPLY = "ABOVE_SUPPLY"
    BELOW_DEMAND = "BELOW_DEMAND"
    MID_RANGE = "MID_RANGE"


def location_block(location, signal, zone_confidence=0, mode="CASHFLOW"):
    """
    SHORT_AT_DEMAND / LONG_AT_SUPPLY – score-based:
    - >= 80: Hard block (STRONG)
    - 60–79: Allow with tp_modifier=0.8 (REDUCED)
    - < 60: Allow normally
    """
    zc = zone_confidence or 0

    if location == Location.AT_SUPPLY and signal == "LONG":
        if zc >= 80:
            return False, "BLOCKED: LONG_AT_STRONG_SUPPLY", 1.0
        if zc >= 60:
            return True, "LOCATION_OK", 0.8
        return True, "LOCATION_OK", 1.0

    if location == Location.AT_DEMAND and signal == "SHORT":
        if zc >= 80:
            return False, "BLOCKED: SHORT_AT_STRONG_DEMAND", 1.0
        if zc >= 60:
            return True, "LOCATION_OK", 0.8
        return True, "LOCATION_OK", 1.0

    return True, "LOCATION_OK", 1.0
