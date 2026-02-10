from enum import Enum


class Location(Enum):
    AT_SUPPLY = "AT_SUPPLY"
    AT_DEMAND = "AT_DEMAND"
    ABOVE_SUPPLY = "ABOVE_SUPPLY"
    BELOW_DEMAND = "BELOW_DEMAND"
    MID_RANGE = "MID_RANGE"


def location_block(location, signal):
    if location == Location.AT_SUPPLY and signal == "LONG":
        return False, "BLOCKED: LONG_AT_SUPPLY"

    if location == Location.AT_DEMAND and signal == "SHORT":
        return False, "BLOCKED: SHORT_AT_DEMAND"

    return True, "LOCATION_OK"
